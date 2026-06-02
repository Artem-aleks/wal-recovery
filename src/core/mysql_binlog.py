"""
wal_recovery/core/mysql_binlog.py
Парсинг binary log (binlog) MySQL для поиска удалённых записей.

MySQL хранит все изменения в binlog в ROW-формате:
  - Каждая удалённая строка сохраняется ПОЛНОСТЬЮ (до удаления)
  - Используем библиотеку mysql-replication для чтения
"""

import pymysql
from datetime import datetime, timedelta
from typing import Optional

try:
    from pymysqlreplication import BinLogStreamReader
    from pymysqlreplication.row_event import DeleteRowsEvent, WriteRowsEvent
    HAS_REPLICATION = True
except ImportError:
    HAS_REPLICATION = False


class MySQLBinlogParser:
    """Читает binlog MySQL и извлекает удалённые записи."""

    def __init__(self, host=None, port=None,
                 user=None, password=None,
                 database="library_demo"):
        # Берём значения из конфига если не переданы явно
        try:
            from core.config import get_config
            cfg = get_config().mysql
        except ImportError:
            try:
                from config import get_config
                cfg = get_config().mysql
            except ImportError:
                cfg = None

        self.host     = host     or (cfg.host     if cfg else "localhost")
        self.port     = port     or (cfg.port     if cfg else 3306)
        self.user     = user     or (cfg.user     if cfg else "admin")
        self.password = password or (cfg.password if cfg else "oksat")
        self.database = database
        self._conn    = None

    # ------------------------------------------------------------------
    # Подключение
    # ------------------------------------------------------------------
    def connect(self):
        self._conn = pymysql.connect(
            host=self.host, port=self.port,
            user=self.user, password=self.password,
            database=self.database,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    def disconnect(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Поиск удалённых записей через binlog
    # ------------------------------------------------------------------
    def get_deleted_records(self, table_name: str,
                             since_hours: int = 48) -> list[dict]:
        """
        Читает binlog и возвращает все удалённые строки таблицы.

        Требует: библиотека mysql-replication + пользователь
        с правами REPLICATION SLAVE, REPLICATION CLIENT.
        """
        if not HAS_REPLICATION:
            return self._fallback_mysqlbinlog(table_name)

        deleted    = []
        since_time = datetime.now() - timedelta(hours=since_hours)

        mysql_settings = {
            "host":   self.host,
            "port":   self.port,
            "user":   self.user,
            "passwd": self.password,
        }

        try:
            # Определяем с какого файла читать
            if not self._conn:
                self.connect()

            # Получаем список всех binlog файлов
            if not self._conn:
                self.connect()

            all_logs = []
            with self._conn.cursor() as cur:
                cur.execute("SHOW BINARY LOGS")
                rows = cur.fetchall()
                for r in rows:
                    fname = r.get('Log_name') or r.get('log_name', '')
                    fsize = r.get('File_size') or r.get('file_size', 0)
                    if fname and int(fsize) > 4:
                        all_logs.append(fname)

            if not all_logs:
                return deleted

            # Читаем только первый файл — BinLogStreamReader
            # автоматически переходит к следующим файлам сам.
            # Запускаем ОДИН поток с первого файла — он пройдёт все до конца.
            try:
                stream = BinLogStreamReader(
                    connection_settings=mysql_settings,
                    server_id=999,
                    only_events=[DeleteRowsEvent],
                    only_tables=[table_name],
                    only_schemas=[self.database],
                    blocking=False,
                    resume_stream=False,
                    log_file=all_logs[0],
                    log_pos=4,
                )

                seen_keys = set()

                for binlogevent in stream:
                    event_time = datetime.fromtimestamp(binlogevent.timestamp)
                    if event_time < since_time:
                        continue

                    try:
                        cur_log_file = binlogevent.packet.log_file
                        cur_log_pos  = binlogevent.packet.log_pos
                    except Exception:
                        cur_log_file = all_logs[0]
                        cur_log_pos  = 0

                    for row in binlogevent.rows:
                        record = dict(row['values'])

                        # Дедупликация по уникальному ключу события
                        dedup_key = (cur_log_file, cur_log_pos,
                                     str(record.get('id', '')))
                        if dedup_key in seen_keys:
                            continue
                        seen_keys.add(dedup_key)

                        record['_deleted_at'] = event_time.strftime('%Y-%m-%d %H:%M:%S')
                        record['_table']      = table_name
                        record['_source']     = 'MySQL binlog'
                        record['_log_file']   = cur_log_file
                        record['_log_pos']    = cur_log_pos
                        deleted.append(record)

                stream.close()

            except Exception as e:
                pass

        except Exception as e:
            print(f"[MySQL Binlog] Ошибка: {e}")
            deleted = self._fallback_mysqlbinlog(table_name)

        return deleted

    # ------------------------------------------------------------------
    # Фолбэк через mysqlbinlog CLI-утилиту
    # ------------------------------------------------------------------
    def _fallback_mysqlbinlog(self, table_name: str) -> list[dict]:
        """
        Запускает mysqlbinlog и парсит SQL-выражения DELETE.
        Используется когда mysql-replication не установлен.
        """
        import subprocess
        import re

        deleted = []
        try:
            # Получаем имя текущего binlog файла
            if not self._conn:
                self.connect()

            with self._conn.cursor() as cur:
                cur.execute("SHOW BINARY LOGS")
                logs = cur.fetchall()

            for log in logs:
                log_file = log.get('Log_name', log.get('log_name', ''))
                log_path = f"/var/log/mysql/{log_file}"

                result = subprocess.run(
                    ["mysqlbinlog", "--base64-output=DECODE-ROWS",
                     "-v", log_path],
                    capture_output=True, text=True, timeout=30
                )

                # Ищем блоки DELETE для нашей таблицы
                lines = result.stdout.splitlines()
                in_delete = False
                current_ts = ''

                for line in lines:
                    # Временная метка события
                    ts_match = re.match(r'#(\d{6}\s+\d+:\d+:\d+)', line)
                    if ts_match:
                        current_ts = ts_match.group(1)

                    # Начало DELETE-события для нашей таблицы
                    if f'DELETE FROM `{self.database}`.`{table_name}`' in line or \
                       f'DELETE FROM `{table_name}`' in line:
                        in_delete = True

                    # Строка данных (### @1=..., @2=..., ...)
                    if in_delete and line.startswith('###'):
                        record = self._parse_binlog_row(line, table_name, current_ts)
                        if record:
                            deleted.append(record)

                    if in_delete and line.strip() == '':
                        in_delete = False

        except Exception as e:
            print(f"[mysqlbinlog fallback] Ошибка: {e}")

        return deleted

    def _parse_binlog_row(self, line: str, table_name: str,
                          timestamp: str) -> Optional[dict]:
        """Парсит строку вида '### @1=42 /* INT meta=0 nullable=0 ... */'"""
        import re
        match = re.match(r'###\s+@(\d+)=(.+?)(?:\s+/\*|$)', line)
        if not match:
            return None

        col_idx = int(match.group(1))
        col_val = match.group(2).strip().strip("'")

        return {
            f'col_{col_idx}': col_val,
            '_deleted_at': timestamp,
            '_table':      table_name,
            '_source':     'mysqlbinlog CLI'
        }

    # ------------------------------------------------------------------
    # Список таблиц
    # ------------------------------------------------------------------
    def get_tables(self) -> list[str]:
        if not self._conn:
            self.connect()

        with self._conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            rows = cur.fetchall()
            # DictCursor возвращает {'Tables_in_<db>': name}
            return [list(r.values())[0] for r in rows]

    # ------------------------------------------------------------------
    # Схема таблицы
    # ------------------------------------------------------------------
    def get_table_columns(self, table_name: str) -> list[dict]:
        if not self._conn:
            self.connect()

        with self._conn.cursor() as cur:
            cur.execute(f"DESCRIBE `{table_name}`")
            rows = cur.fetchall()
            return [
                {
                    'column_name':  r['Field'],
                    'data_type':    r['Type'],
                    'is_nullable':  r['Null'],
                    'column_default': r['Default']
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # Восстановление записи
    # ------------------------------------------------------------------
    def restore_record(self, table_name: str, record: dict) -> bool:
        """INSERT удалённой строки обратно в таблицу."""
        if not self._conn:
            self.connect()

        data = {k: v for k, v in record.items() if not k.startswith('_')}
        if not data:
            return False

        cols        = ', '.join(f'`{k}`' for k in data.keys())
        placeholders = ', '.join(['%s'] * len(data))
        values      = list(data.values())

        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f"INSERT IGNORE INTO `{table_name}` ({cols}) VALUES ({placeholders})",
                    values
                )
            self._conn.commit()
            return True
        except pymysql.Error as e:
            print(f"[MySQL] Ошибка восстановления: {e}")
            self._conn.rollback()
            return False

    # ------------------------------------------------------------------
    # Информация о binlog
    # ------------------------------------------------------------------
    def get_binlog_info(self) -> dict:
        """Возвращает информацию о текущих настройках binlog."""
        if not self._conn:
            self.connect()

        info = {}
        with self._conn.cursor() as cur:
            # Глобальные переменные binlog
            for var in ['log_bin', 'binlog_format', 'binlog_row_image',
                        'expire_logs_days', 'max_binlog_size']:
                cur.execute(f"SHOW VARIABLES LIKE '{var}'")
                row = cur.fetchone()
                if row:
                    info[var] = row['Value']

            # Список binlog файлов
            cur.execute("SHOW BINARY LOGS")
            logs = cur.fetchall()
            info['log_files'] = [
                {'file': r.get('Log_name', ''),
                 'size': r.get('File_size', 0)}
                for r in logs
            ]

            # Текущая позиция — SHOW MASTER STATUS устарела в MySQL 8.0.22+
            # Пробуем новую команду, фолбэк на старую
            master = None
            for cmd in ("SHOW BINARY LOG STATUS", "SHOW MASTER STATUS"):
                try:
                    cur.execute(cmd)
                    master = cur.fetchone()
                    if master:
                        break
                except Exception:
                    continue
            if master:
                info['current_file'] = master.get('File', '')
                info['current_pos']  = master.get('Position', 0)

        return info


# ------------------------------------------------------------------
# Быстрая проверка
# ------------------------------------------------------------------
if __name__ == "__main__":
    parser = MySQLBinlogParser(database="library_demo")
    parser.connect()

    print("=== Таблицы в library_demo ===")
    for t in parser.get_tables():
        print(f"  • {t}")

    print("\n=== Настройки binlog ===")
    info = parser.get_binlog_info()
    for k, v in info.items():
        if k != 'log_files':
            print(f"  {k}: {v}")

    print("\n  Файлы binlog:")
    for f in info.get('log_files', []):
        print(f"    {f['file']}  ({f['size']} байт)")

    print("\n=== Удалённые записи в таблице readers ===")
    deleted = parser.get_deleted_records("readers")
    for d in deleted:
        print(f"  {d}")

    parser.disconnect()