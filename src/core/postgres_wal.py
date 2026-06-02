"""
wal_recovery/core/postgres_wal.py

Восстановление удалённых записей из любой PostgreSQL БД через WAL.

Принципы работы с произвольными БД:
  1. Слот репликации создаётся отдельно для каждой БД + таблицы
  2. REPLICA IDENTITY FULL включается автоматически при первом сканировании
  3. Схема таблицы определяется динамически из information_schema
  4. Пользователь и пароль берутся из конфига — достаточно иметь права REPLICATION
"""

import subprocess
import re
import json
import os
import psycopg2
import psycopg2.extras
from datetime import datetime
from typing import Optional


class PostgresWALParser:
    """Читает WAL-логи PostgreSQL и извлекает удалённые записи."""

    def __init__(self, host=None, port=None, user=None,
                 password=None, database="shop_demo"):
        try:
            from core.config import get_config
            cfg = get_config().pg
        except ImportError:
            try:
                from config import get_config
                cfg = get_config().pg
            except ImportError:
                cfg = None

        self.host     = host     or (cfg.host     if cfg else "localhost")
        self.port     = port     or (cfg.port     if cfg else 5432)
        self.user     = user     or (cfg.user     if cfg else "admin")
        self.password = password or (cfg.password if cfg else "oksat")
        self.database = database
        self._conn    = None

    # ------------------------------------------------------------------
    # Подключение
    # ------------------------------------------------------------------
    def connect(self):
        self._conn = psycopg2.connect(
            host=self.host, port=self.port,
            user=self.user, password=self.password,
            database=self.database
        )
        self._conn.autocommit = True

    def disconnect(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Автоматическая подготовка таблицы для любой БД
    # ------------------------------------------------------------------
    def _ensure_replica_identity(self, table_name: str):
        """
        Включает REPLICA IDENTITY FULL для таблицы если ещё не включено.
        Вызывается автоматически перед каждым сканированием —
        работает с любой таблицей в любой БД.
        """
        try:
            with self._conn.cursor() as cur:
                # Проверяем текущее значение replica identity
                cur.execute("""
                    SELECT relreplident
                    FROM pg_class
                    WHERE relname = %s
                      AND relnamespace = (
                          SELECT oid FROM pg_namespace WHERE nspname = 'public'
                      )
                """, (table_name,))
                row = cur.fetchone()
                if row and row[0] != 'f':
                    # 'f' = FULL, уже включено
                    cur.execute(
                        f"ALTER TABLE public.{table_name} REPLICA IDENTITY FULL"
                    )
        except psycopg2.Error:
            pass  # Нет прав — продолжаем без REPLICA IDENTITY FULL

    def _ensure_slot(self, slot_name: str):
        """Создаёт слот репликации если не существует."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT slot_name FROM pg_replication_slots WHERE slot_name = %s",
                (slot_name,)
            )
            if not cur.fetchone():
                cur.execute(
                    "SELECT pg_create_logical_replication_slot(%s, 'wal2json')",
                    (slot_name,)
                )

    def _slot_name(self, table_name: str) -> str:
        """
        Генерирует уникальное имя слота для пары БД+таблица.
        Формат: wr_{db}_{table} (max 63 символа — лимит PostgreSQL).
        Работает для любых имён БД и таблиц.
        """
        safe_db    = re.sub(r'[^a-z0-9]', '_', self.database.lower())
        safe_table = re.sub(r'[^a-z0-9]', '_', table_name.lower())
        name = f"wr_{safe_db}_{safe_table}"
        # Если имя слишком длинное — обрезаем с хешем для уникальности
        if len(name) > 63:
            import hashlib
            suffix = hashlib.md5(name.encode()).hexdigest()[:8]
            name = f"wr_{suffix}_{safe_table}"[:63]
        return name

    # ------------------------------------------------------------------
    # Основной метод: поиск удалённых записей
    # ------------------------------------------------------------------
    def get_deleted_records(self, table_name: str,
                             since_hours: int = 24) -> list[dict]:
        """
        Возвращает удалённые строки из указанной таблицы.

        Работает с любой PostgreSQL БД при условии:
          - wal_level = logical
          - пользователь имеет права REPLICATION
          - установлен плагин wal2json

        Каждая пара (БД, таблица) получает свой слот репликации —
        события разных таблиц никогда не смешиваются.
        """
        if not self._conn:
            self.connect()

        deleted  = []
        slot_name = self._slot_name(table_name)

        try:
            with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

                # Автоматически включаем REPLICA IDENTITY FULL
                # для любой таблицы в любой БД
                self._ensure_replica_identity(table_name)

                # Создаём слот если нет
                self._ensure_slot(slot_name)

                # Читаем WAL только для нужной таблицы
                cur.execute("""
                    SELECT data
                    FROM pg_logical_slot_peek_changes(
                        %s, NULL, NULL,
                        'pretty-print',      '1',
                        'include-timestamp', '1',
                        'add-tables',        %s
                    )
                """, (slot_name, f"public.{table_name}"))

                rows   = cur.fetchall()
                cutoff = datetime.now().timestamp() - since_hours * 3600

                for row in rows:
                    try:
                        change = json.loads(row['data'])
                        for entry in change.get('change', []):

                            # Фильтр 1: только операции DELETE
                            if entry.get('kind') != 'delete':
                                continue

                            # Фильтр 2: только нужная таблица
                            # (защита от краевых случаев)
                            entry_table = entry.get('table', '')
                            if entry_table and entry_table != table_name:
                                continue

                            # Извлекаем данные строки
                            record = {}
                            col_names  = entry.get('columnnames') or []
                            col_values = entry.get('columnvalues') or []

                            if col_names:
                                # REPLICA IDENTITY FULL — все поля
                                for col, val in zip(col_names, col_values):
                                    record[col] = val
                            else:
                                # Только первичный ключ (без REPLICA IDENTITY FULL)
                                old_keys = entry.get('oldkeys', {})
                                for col, val in zip(
                                    old_keys.get('keynames', []),
                                    old_keys.get('keyvalues', [])
                                ):
                                    record[col] = val

                            if not record:
                                continue

                            # Временная метка
                            ts_str = (
                                entry.get('timestamp') or
                                change.get('timestamp') or ''
                            )

                            # Фильтр 3: по времени (since_hours)
                            if ts_str:
                                try:
                                    ts_clean = ts_str.split('+')[0].strip()
                                    ts = datetime.fromisoformat(ts_clean)
                                    if ts.timestamp() < cutoff:
                                        continue
                                except Exception:
                                    pass

                            record['_deleted_at'] = ts_str
                            record['_table']      = entry_table or table_name
                            record['_source']     = 'WAL/logical'
                            deleted.append(record)

                    except (json.JSONDecodeError, KeyError):
                        continue

        except psycopg2.Error as e:
            err_msg = str(e).lower()
            if 'wal2json' in err_msg or 'plugin' in err_msg:
                # wal2json не установлен — пробуем pg_waldump
                deleted = self._fallback_waldump(table_name, since_hours)
            # Остальные ошибки подключения — возвращаем пустой список

        return deleted

    # ------------------------------------------------------------------
    # Восстановление записи в правильную таблицу
    # ------------------------------------------------------------------
    def restore_record(self, table_name: str, record: dict) -> bool:
        """
        Восстанавливает запись через INSERT.

        Таблица берётся из аргумента table_name (который в свою
        очередь берётся из record['_table'] в вызывающем коде).
        Это гарантирует что запись вернётся именно туда откуда удалена.
        """
        if not self._conn:
            self.connect()

        # Убираем служебные поля
        data = {k: v for k, v in record.items() if not k.startswith('_')}
        if not data:
            return False

        # Динамически определяем колонки таблицы
        # чтобы не вставлять несуществующие поля (для любой БД)
        try:
            existing_cols = self._get_column_names(table_name)
            if existing_cols:
                data = {k: v for k, v in data.items() if k in existing_cols}
        except Exception:
            pass

        if not data:
            return False

        cols   = ', '.join(f'"{k}"' for k in data.keys())
        vals   = ', '.join(['%s'] * len(data))
        values = list(data.values())

        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f'INSERT INTO "{table_name}" ({cols}) VALUES ({vals}) '
                    f'ON CONFLICT DO NOTHING',
                    values
                )
            return True
        except psycopg2.Error as e:
            print(f"[PG] Ошибка восстановления в {table_name}: {e}")
            return False

    def _get_column_names(self, table_name: str) -> list[str]:
        """Возвращает список имён колонок таблицы из information_schema."""
        with self._conn.cursor() as cur:
            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = %s
                ORDER BY ordinal_position
            """, (table_name,))
            return [r[0] for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Фолбэк через pg_waldump (если wal2json не установлен)
    # ------------------------------------------------------------------
    def _fallback_waldump(self, table_name: str, since_hours: int) -> list[dict]:
        deleted    = []
        pg_version = self._get_pg_version()
        wal_dir    = f"/var/lib/postgresql/{pg_version}/main/pg_wal"

        if not os.path.exists(wal_dir):
            # macOS — ищем через pg_config
            try:
                result = subprocess.run(
                    ["pg_config", "--pkgdatadir"],
                    capture_output=True, text=True
                )
                data_dir = result.stdout.strip().replace('share/postgresql', 'var/postgresql')
                wal_dir = os.path.join(data_dir, 'pg_wal')
            except Exception:
                return []

        if not os.path.exists(wal_dir):
            return []

        try:
            result = subprocess.run(
                ["pg_waldump", "--path", wal_dir, "--rmgr=Heap"],
                capture_output=True, text=True, timeout=30
            )
            for line in result.stdout.splitlines():
                if 'DELETE' in line and 'Heap' in line:
                    record = self._parse_waldump_line(line, table_name)
                    if record:
                        deleted.append(record)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        return deleted

    def _parse_waldump_line(self, line: str, table_name: str) -> Optional[dict]:
        ts_match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        timestamp = ts_match.group(1) if ts_match else ''
        return {
            '_table':      table_name,
            '_deleted_at': timestamp,
            '_source':     'pg_waldump',
            '_raw':        line.strip()
        }

    def _get_pg_version(self) -> str:
        try:
            result = subprocess.run(
                ["pg_config", "--version"],
                capture_output=True, text=True
            )
            version = re.search(r'\d+', result.stdout)
            return version.group() if version else "17"
        except FileNotFoundError:
            return "17"

    def read_wal_raw(self, table_name: str,
                     kind_filter: str = 'all',
                     since_dt: str = '',
                     until_dt: str = '') -> list[dict]:
        """
        Читает все WAL-события для таблицы без фильтрации по DELETE.
        Используется вкладкой «WAL Логи» для прямого просмотра лога.

        kind_filter: 'all' | 'insert' | 'update' | 'delete'
        since_dt / until_dt: строки формата 'YYYY-MM-DD HH:MM:SS'
        """
        if not self._conn:
            self.connect()

        records   = []
        slot_name = self._slot_name(table_name)

        # Парсим границы времени
        def _parse_dt(s: str):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return None

        since = _parse_dt(since_dt)
        until = _parse_dt(until_dt)

        try:
            with self._conn.cursor(
                    cursor_factory=psycopg2.extras.RealDictCursor) as cur:

                # Создаём слот если нет
                self._ensure_slot(slot_name)

                # Читаем все изменения для таблицы
                cur.execute("""
                    SELECT data
                    FROM pg_logical_slot_peek_changes(
                        %s, NULL, NULL,
                        'pretty-print',      '1',
                        'include-timestamp', '1',
                        'add-tables',        %s
                    )
                """, (slot_name, f"public.{table_name}"))

                rows = cur.fetchall()

                for row in rows:
                    try:
                        change = json.loads(row['data'])
                        for entry in change.get('change', []):
                            kind = entry.get('kind', '').lower()

                            # Фильтр по типу операции
                            if kind_filter != 'all' and kind != kind_filter:
                                continue

                            # Фильтр по таблице
                            entry_table = entry.get('table', '')
                            if entry_table and entry_table != table_name:
                                continue

                            # Временная метка
                            ts_str = (
                                entry.get('timestamp') or
                                change.get('timestamp') or ''
                            )

                            # Фильтр по диапазону дат
                            if ts_str and (since or until):
                                try:
                                    ts = datetime.fromisoformat(
                                        ts_str.split('+')[0].strip())
                                    if since and ts < since:
                                        continue
                                    if until and ts > until:
                                        continue
                                except Exception:
                                    pass

                            # Собираем запись
                            record = {}

                            if kind == 'delete':
                                col_names  = entry.get('columnnames') or []
                                col_values = entry.get('columnvalues') or []
                                if not col_names:
                                    old_keys = entry.get('oldkeys', {})
                                    col_names  = old_keys.get('keynames', [])
                                    col_values = old_keys.get('keyvalues', [])
                            elif kind == 'update':
                                col_names  = entry.get('columnnames', [])
                                col_values = entry.get('columnvalues', [])
                            else:  # insert
                                col_names  = entry.get('columnnames', [])
                                col_values = entry.get('columnvalues', [])

                            for col, val in zip(col_names, col_values):
                                record[col] = val

                            # Метаданные события
                            record['_kind']      = kind.upper()
                            record['_timestamp'] = ts_str
                            record['_table']     = entry_table or table_name
                            record['_source']    = 'WAL/logical'

                            # LSN если есть
                            lsn = change.get('nextlsn') or change.get('lsn') or ''
                            record['_lsn'] = lsn

                            records.append(record)

                    except (json.JSONDecodeError, KeyError):
                        continue

        except psycopg2.Error as e:
            raise RuntimeError(f"Ошибка чтения WAL: {e}")

        return records

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------
    def get_table_columns(self, table_name: str) -> list[dict]:
        """Возвращает список колонок таблицы с типами."""
        if not self._conn:
            self.connect()
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = %s
                ORDER BY ordinal_position
            """, (table_name,))
            return [dict(r) for r in cur.fetchall()]

    def record_exists(self, table_name: str, pk_value) -> bool:
        """Проверяет существует ли запись с данным PK в таблице."""
        if not self._conn:
            return False
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f'SELECT 1 FROM "{table_name}" WHERE id = %s LIMIT 1',
                    (pk_value,)
                )
                return cur.fetchone() is not None
        except Exception:
            return False

    def get_tables(self) -> list[str]:
        """Возвращает список пользовательских таблиц в БД."""
        if not self._conn:
            self.connect()
        with self._conn.cursor() as cur:
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type   = 'BASE TABLE'
                ORDER BY table_name
            """)
            return [r[0] for r in cur.fetchall()]

    def get_wal_info(self) -> dict:
        """Возвращает настройки WAL и статус слотов репликации."""
        if not self._conn:
            self.connect()

        info   = {}
        params = ['wal_level', 'max_wal_senders', 'wal_keep_size',
                  'archive_mode', 'max_replication_slots']

        with self._conn.cursor() as cur:
            for param in params:
                try:
                    cur.execute("SHOW %s" % param)
                    info[param] = cur.fetchone()[0]
                except Exception:
                    info[param] = 'н/д'

            try:
                cur.execute("SELECT pg_current_wal_lsn()::text")
                info['current_lsn'] = cur.fetchone()[0]
            except Exception:
                info['current_lsn'] = 'н/д'

            try:
                cur.execute("SELECT pg_size_pretty(sum(size)) FROM pg_ls_waldir()")
                info['wal_size'] = cur.fetchone()[0]
            except Exception:
                info['wal_size'] = 'н/д'

            # Показываем слоты только этой БД
            try:
                cur.execute("""
                    SELECT slot_name, active, restart_lsn
                    FROM pg_replication_slots
                    WHERE database = %s
                """, (self.database,))
                slots = cur.fetchall()
                info['slots'] = len(slots)
                info['active_slots'] = sum(1 for s in slots if s[1])
            except Exception:
                info['slots'] = 0

        return info

    def setup_table_for_recovery(self, table_name: str) -> dict:
        """
        Подготавливает таблицу к работе с утилитой:
          1. Включает REPLICA IDENTITY FULL
          2. Создаёт слот репликации
        Возвращает статус каждого шага.
        Вызывается при подключении новой БД.
        """
        if not self._conn:
            self.connect()

        status = {
            'table':            table_name,
            'replica_identity': False,
            'slot_created':     False,
            'slot_name':        self._slot_name(table_name),
            'error':            None,
        }

        try:
            # Шаг 1: REPLICA IDENTITY FULL
            with self._conn.cursor() as cur:
                cur.execute(
                    f"ALTER TABLE public.{table_name} REPLICA IDENTITY FULL"
                )
            status['replica_identity'] = True
        except psycopg2.Error as e:
            status['error'] = f"REPLICA IDENTITY: {e}"

        try:
            # Шаг 2: слот репликации
            self._ensure_slot(status['slot_name'])
            status['slot_created'] = True
        except psycopg2.Error as e:
            status['error'] = (status.get('error') or '') + f" Slot: {e}"

        return status

    def setup_all_tables(self) -> list[dict]:
        """
        Подготавливает все таблицы БД к работе с утилитой.
        Удобно вызывать при подключении новой БД.
        """
        tables = self.get_tables()
        return [self.setup_table_for_recovery(t) for t in tables]