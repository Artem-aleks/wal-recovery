import sys
import os
import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QLabel, QComboBox, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QStatusBar, QMessageBox,
    QProgressBar, QTabWidget, QTextEdit, QFrame, QLineEdit,
    QFormLayout, QGroupBox, QSizePolicy, QSpinBox,
    QFileDialog, QDialog, QProgressDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPalette

# Путь к src/
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.postgres_wal import PostgresWALParser
from core.mysql_binlog import MySQLBinlogParser
try:
    from core.config import get_config, load_config
    _HAS_CONFIG = True
except ImportError:
    _HAS_CONFIG = False

DARK = {

    "bg0":     "#080c14",
    "bg1":     "#0d1117",
    "bg2":     "#161b22",
    "bg3":     "#1c2333",

    "border":  "#21262d",
    "border2": "#30363d",
    # Текст
    "text":    "#f0f6fc",
    "dim":     "#8b949e",
    "muted":   "#484f58",
    "blue":    "#58a6ff",
    "cyan":    "#79c0ff",
    "cyan2":   "#56d364",
    "green":   "#3fb950",
    "green2":  "#238636",
    "red":     "#f85149",
    "yellow":  "#d29922",
    "amber":   "#e3b341",
    "purple":  "#bc8cff",
}

# Шрифт приложения — SF Mono / JetBrains Mono / Фолбэк
APP_FONT_UI   = "SF Pro Display, -apple-system, Segoe UI, Ubuntu, Arial"
APP_FONT_MONO = "JetBrains Mono, SF Mono, Menlo, Monaco, Consolas, Courier New"

# Множество восстановленных записей: ключ = (table, pk_value) → datetime строкой
# Состояние восстановлений — единое хранилище для GUI и CLI
try:
    from core.recovery_state import (
        record_restoration, get_restorations, is_restored,
        record_deletion, get_deletions,
        record_scan, get_scans,
        record_change, get_changes,
        get_all_events,
        clear_all as _clear_state, get_state_file
    )
    _HAS_STATE = True
except ImportError:
    try:
        from recovery_state import (
            record_restoration, get_restorations, is_restored,
            record_deletion, get_deletions,
            record_scan, get_scans,
            record_change, get_changes,
            get_all_events,
            clear_all as _clear_state, get_state_file
        )
        _HAS_STATE = True
    except ImportError:
        _HAS_STATE = False

# Флаг доступности конфига
try:
    from core.config import get_config as _gc
    _gc()
    _HAS_CONFIG = True
except Exception:
    try:
        from config import get_config as _gc
        _gc()
        _HAS_CONFIG = True
    except Exception:
        _HAS_CONFIG = False

# In-memory кэш для текущей сессии (дополняет файл состояния)
_session_restored: dict = {}  # {(db, table, pk): restored_at}

LOG_FILE = os.path.expanduser("~/.config/wal-recovery/state.json")

def _is_restored(db: str, table: str, pk: str) -> str:
    """
    Проверяет восстановлена ли запись.
    Порядок: сессионный кэш → state.json (сохраняется между сессиями).
    """
    key = (db, table, str(pk))
    # Сессионный кэш
    if key in _session_restored:
        return _session_restored[key]
    # Файл state.json — работает после перезапуска GUI
    if _HAS_STATE:
        try:
            ts = is_restored(db, table, str(pk))
            if ts:
                # Кэшируем чтобы не читать файл каждый раз
                _session_restored[key] = ts
                return ts
        except Exception:
            pass
    return ""

def _mark_restored(db: str, table: str, pk: str, ts: str):
    """Запоминает восстановление и в сессии и в файле."""
    _session_restored[(db, table, str(pk))] = ts


def combo_style() -> str:
    return f"""
        QComboBox {{
            background: {DARK['bg2']}; color: {DARK['text']};
            border: 1px solid {DARK['border2']}; border-radius: 8px;
            padding: 7px 12px; font-size: 13px;
            selection-background-color: transparent;
        }}
        QComboBox:hover  {{ border-color: {DARK['blue']}; background: {DARK['bg3']}; }}
        QComboBox:focus  {{ border-color: {DARK['blue']}; }}
        QComboBox::drop-down {{ border: none; width: 24px; }}
        QComboBox QAbstractItemView {{
            background: {DARK['bg2']}; color: {DARK['text']};
            border: 1px solid {DARK['border2']}; border-radius: 8px;
            selection-background-color: {DARK['bg3']};
            padding: 4px;
        }}
    """


def input_style() -> str:
    return f"""
        QLineEdit, QSpinBox, QDateTimeEdit {{
            background: {DARK['bg2']}; color: {DARK['text']};
            border: 1px solid {DARK['border2']}; border-radius: 8px;
            padding: 7px 12px; font-size: 13px;
        }}
        QLineEdit:focus, QSpinBox:focus, QDateTimeEdit:focus {{
            border-color: {DARK['blue']};
            background: {DARK['bg3']};
        }}
        QLineEdit:hover, QSpinBox:hover, QDateTimeEdit:hover {{
            border-color: {DARK['dim']};
        }}
    """


def btn_style(bg: str, hover: str = None) -> str:
    hover = hover or bg
    return f"""
        QPushButton {{
            background: {bg}; color: white; border: none;
            border-radius: 8px; padding: 0 20px; font-size: 13px;
            font-weight: 600; letter-spacing: 0.3px;
        }}
        QPushButton:hover   {{
            background: {hover};
            border: 1px solid rgba(255,255,255,0.15);
        }}
        QPushButton:pressed {{ opacity: 0.85; }}
        QPushButton:disabled{{
            background: {DARK['bg2']}; color: {DARK['muted']};
            border: 1px solid {DARK['border']};
        }}
    """



# Рабочие потоки
class ScanWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, parser, table_name: str, hours: int = 24):
        super().__init__()
        self.parser     = parser
        self.table_name = table_name
        self.hours      = hours

    def run(self):
        try:
            self.progress.emit(15)
            self.parser.connect()
            self.progress.emit(40)
            records = self.parser.get_deleted_records(self.table_name,
                                                      since_hours=self.hours)
            self.progress.emit(80)

            # Автоматически помечаем записи которые уже есть в БД
            if _HAS_STATE:
                for rec in records:
                    pk_val = str(rec.get('id') or next(
                        (v for k,v in rec.items() if not k.startswith('_')), ''))
                    if pk_val and not is_restored(
                            self.parser.database, self.table_name, pk_val):
                        try:
                            if self.parser.record_exists(self.table_name, pk_val):
                                record_restoration(
                                    db=self.parser.database,
                                    db_type='PostgreSQL',
                                    table=self.table_name,
                                    pk_value=pk_val,
                                    deleted_at=rec.get('_deleted_at',''),
                                    source='auto-detect',
                                    data=rec,
                                )
                        except Exception:
                            pass

            self.progress.emit(95)
            self.finished.emit(records)
        except Exception as e:
            self.error.emit(str(e))


class RestoreWorker(QThread):
    finished = pyqtSignal(int, int)
    error    = pyqtSignal(str)

    def __init__(self, parser, table_name: str, records: list):
        super().__init__()
        self.parser     = parser
        self.table_name = table_name
        self.records    = records

    def run(self):
        ok = fail = 0
        try:
            self.parser.connect()
            for record in self.records:
                if self.parser.restore_record(self.table_name, record):
                    ok += 1
                else:
                    fail += 1
            self.finished.emit(ok, fail)
        except Exception as e:
            self.error.emit(str(e))



# Левая панель — выбор источника
class DBSelectorPanel(QWidget):
    table_selected    = pyqtSignal(str, str, str, str)  # db_type, db, table, hours
    backup_requested  = pyqtSignal(str, str)             # db_type, db_name

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        # Заголовок
        title = QLabel("🗄  Источник данных")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        title.setStyleSheet(f"""
            color: {DARK['text']}; padding-bottom: 6px;
            letter-spacing: 0.3px;
        """)
        lay.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {DARK['border']};")
        lay.addWidget(sep)

        # Тип СУБД
        lay.addWidget(self._lbl("Тип СУБД"))
        self.db_type_combo = QComboBox()
        self.db_type_combo.addItems(["PostgreSQL", "MySQL"])
        self.db_type_combo.setStyleSheet(combo_style())
        self.db_type_combo.currentTextChanged.connect(self._on_type_changed)
        lay.addWidget(self.db_type_combo)

        # База данных
        lay.addWidget(self._lbl("База данных"))
        self.db_combo = QComboBox()
        self.db_combo.setStyleSheet(combo_style())
        self.db_combo.currentTextChanged.connect(self._load_tables)
        lay.addWidget(self.db_combo)

        # Таблица
        lay.addWidget(self._lbl("Таблица"))
        self.table_combo = QComboBox()
        self.table_combo.setStyleSheet(combo_style())
        lay.addWidget(self.table_combo)

        # Глубина поиска
        lay.addWidget(self._lbl("Искать удаления за"))
        self.hours_combo = QComboBox()
        self.hours_combo.addItems(["6 часов", "24 часа", "48 часов",
                                   "7 дней",  "30 дней"])
        self.hours_combo.setCurrentIndex(1)
        self.hours_combo.setStyleSheet(combo_style())
        lay.addWidget(self.hours_combo)

        lay.addSpacing(10)

        # Кнопка сканирования
        self.scan_btn = QPushButton("🔍  Сканировать логи")
        self.scan_btn.setFixedHeight(42)
        self.scan_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['blue']}; color: white;
                border: none; border-radius: 8px;
                font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover   {{ background: #2563eb; }}
            QPushButton:pressed {{ background: #1d4ed8; }}
            QPushButton:disabled{{ background: {DARK['bg2']}; color: {DARK['muted']}; }}
        """)
        self.scan_btn.clicked.connect(self._on_scan)
        lay.addWidget(self.scan_btn)

        # Кнопка резервного копирования
        # Кнопка добавления новой БД
        self.add_db_btn = QPushButton("➕  Добавить базу данных")
        self.add_db_btn.setFixedHeight(36)
        self.add_db_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {DARK['cyan']};
                border: 1px dashed {DARK['cyan']}60; border-radius: 8px;
                padding: 0 16px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(121,192,255,0.08);
                border-style: solid; border-color: {DARK['cyan']};
            }}
        """)
        self.add_db_btn.clicked.connect(self._on_add_db)
        lay.addWidget(self.add_db_btn)

        self.backup_btn = QPushButton("💾  Создать резервную копию")
        self.backup_btn.setFixedHeight(36)
        self.backup_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DARK['bg2']}; color: {DARK['dim']};
                border: 1px solid {DARK['border2']}; border-radius: 8px;
                padding: 0 16px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{
                background: {DARK['bg3']}; color: {DARK['text']};
                border-color: {DARK['cyan2']};
            }}
            QPushButton:disabled {{
                color: {DARK['muted']}; border-color: {DARK['border']};
            }}
        """)
        self.backup_btn.clicked.connect(self._on_backup)
        lay.addWidget(self.backup_btn)

        lay.addStretch()

        # Подсказка CLI
        cli_hint = QLabel("CLI:  wal-recovery --cli")
        cli_hint.setStyleSheet(f"color: {DARK['muted']}; font-size: 10px; "
                               f"font-family: monospace; padding-top: 4px;")
        lay.addWidget(cli_hint)

        # Первоначальная загрузка
        self._on_type_changed("PostgreSQL")

    def _on_add_db(self):
        """Открывает диалог добавления новой БД."""
        dialog = AddDatabaseDialog(self)
        dialog.db_added.connect(self._on_db_added)
        dialog.exec()

    def _on_db_added(self, db_type: str, db_name: str):
        """Обновляет список БД после добавления новой."""
        self._on_type_changed(self.db_type_combo.currentText())

    def _on_backup(self):
        """Создаёт резервную копию выбранной БД."""
        db_type = self.db_type_combo.currentText()
        db_name = self.db_combo.currentText()
        if not db_name:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Ошибка", "Выберите базу данных.")
            return
        # Сигнал в MainWindow
        self.backup_requested.emit(db_type, db_name)

    def _lbl(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet(f"color: {DARK['dim']}; font-size: 12px;")
        return l

    # ------------------------------------------------------------------
    def _on_type_changed(self, db_type: str):
        self.db_combo.clear()
        try:
            if _HAS_CONFIG:
                cfg = get_config()
                dbs = cfg.pg.databases if db_type == "PostgreSQL" else cfg.mysql.databases
            else:
                raise RuntimeError("no config")
        except Exception:
            dbs = (["shop_demo", "employees_demo"] if db_type == "PostgreSQL"
                   else ["library_demo", "clinic_demo"])
        self.db_combo.addItems(dbs)
        self._load_tables()

    def _load_tables(self):
        self.table_combo.clear()
        db_type = self.db_type_combo.currentText()
        db_name = self.db_combo.currentText()
        if not db_name:
            return
        try:
            p = (PostgresWALParser(database=db_name)
                 if db_type == "PostgreSQL"
                 else MySQLBinlogParser(database=db_name))
            p.connect()
            self.table_combo.addItems(p.get_tables())

            # WAL/binlog инфо
            if db_type == "PostgreSQL":
                info = p.get_wal_info()
                lines = [f"{k}: {v}" for k, v in info.items()]
            else:
                info = p.get_binlog_info()
                lines = [f"{k}: {v}" for k, v in info.items() if k != 'log_files']
                fls = info.get('log_files', [])
                if fls:
                    lines.append(f"log_files: {len(fls)} файлов")
                    lines += [f"  {f['file']}" for f in fls[:3]]
            pass  # info_box removed
            p.disconnect()
        except Exception as e:
            pass  # info_box removed

    def _on_scan(self):
        hours_map = {"6 часов": "6", "24 часа": "24", "48 часов": "48",
                     "7 дней": "168", "30 дней": "720"}
        self.table_selected.emit(
            self.db_type_combo.currentText(),
            self.db_combo.currentText(),
            self.table_combo.currentText(),
            hours_map.get(self.hours_combo.currentText(), "24"),
        )


# ===========================================================================
# Таблица удалённых записей (центр)
# ===========================================================================
class DeletedRecordsTable(QWidget):
    restore_requested = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._records = []
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        # Тулбар
        tb = QHBoxLayout()
        tb.setSpacing(8)

        self.title_lbl = QLabel("Удалённые записи")
        self.title_lbl.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.title_lbl.setStyleSheet(f"color: {DARK['text']};")
        tb.addWidget(self.title_lbl)

        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(f"color: {DARK['muted']}; font-size: 12px;")
        tb.addWidget(self.count_lbl)
        tb.addStretch()

        self.sel_all_btn = QPushButton("Выбрать всё")
        self.sel_all_btn.setFixedHeight(32)
        self.sel_all_btn.setStyleSheet(btn_style(DARK['bg2']))
        self.sel_all_btn.clicked.connect(self._select_all)
        tb.addWidget(self.sel_all_btn)

        self.restore_btn = QPushButton("♻  Восстановить выбранные")
        self.restore_btn.setFixedHeight(32)
        self.restore_btn.setStyleSheet(btn_style(DARK['green']))
        self.restore_btn.clicked.connect(self._on_restore)
        self.restore_btn.setEnabled(False)
        tb.addWidget(self.restore_btn)

        lay.addLayout(tb)

        # Прогресс
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(f"""
            QProgressBar {{ background: {DARK['bg2']}; border: none; border-radius: 3px; }}
            QProgressBar::chunk {{ background: {DARK['blue']}; border-radius: 3px; }}
        """)
        lay.addWidget(self.progress)

        # Таблица
        self.table = QTableWidget()
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background: {DARK['bg0']}; color: {DARK['text']};
                gridline-color: {DARK['bg2']}; border: 1px solid {DARK['bg2']};
                border-radius: 8px; font-size: 12px;
            }}
            QTableWidget::item          {{ padding: 6px 8px; }}
            QTableWidget::item:selected {{ background: #1e40af; }}
            QHeaderView::section {{
                background: {DARK['bg1']}; color: {DARK['dim']};
                padding: 8px; border: none;
                border-bottom: 1px solid {DARK['border']}; font-weight: 600;
            }}
        """)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        lay.addWidget(self.table)

        # Пустое состояние
        self.empty_lbl = QLabel(
            "🔍  Нажмите «Сканировать логи»\n"
            "    чтобы найти удалённые записи"
        )
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet(
            f"color: {DARK['muted']}; font-size: 16px; padding: 60px;"
        )
        lay.addWidget(self.empty_lbl)
        self._show_empty(True)

    # ------------------------------------------------------------------
    def show_scanning(self, value: int):
        self.progress.setVisible(True)
        self.progress.setValue(value)

    def load_records(self, records: list, table_name: str = "", db_name: str = ""):
        self._records = records
        self._table_name = table_name
        self.progress.setVisible(False)

        filtered = [r for r in records
                    if not table_name or r.get('_table') == table_name]

        self._show_empty(len(filtered) == 0)
        if not filtered:
            self.count_lbl.setText("(записи не найдены)")
            return

        data_cols = [k for k in filtered[0] if not k.startswith('_')]
        # чекбокс | данные | 🗑 Удалено | ✅ Восстановлено | Источник | Статус
        all_cols = ['☑'] + data_cols + ['🗑 Удалено', '✅ Восстановлено', 'Источник', 'Статус']
        n_data   = len(data_cols)

        self.table.blockSignals(True)
        self.table.clear()
        self.table.setColumnCount(len(all_cols))
        self.table.setRowCount(len(filtered))
        self.table.setHorizontalHeaderLabels(all_cols)

        for ri, rec in enumerate(filtered):
            pk_val = rec.get('id') or (rec.get(data_cols[0]) if data_cols else None)
            rec_key = (rec.get('_table', table_name), str(pk_val))
            already_restored_ts = _is_restored(db_name, rec.get('_table', table_name), str(pk_val))
            already_restored = bool(already_restored_ts)
            restored_at = already_restored_ts

            # Чекбокс
            chk = QTableWidgetItem()
            chk.setCheckState(Qt.CheckState.Unchecked)
            chk.setFlags(Qt.ItemFlag.ItemIsEnabled if already_restored
                         else Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(ri, 0, chk)

            # Данные строки
            for ci, col in enumerate(data_cols, 1):
                item = QTableWidgetItem(str(rec.get(col, '')))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if already_restored:
                    item.setForeground(QColor(DARK['muted']))
                self.table.setItem(ri, ci, item)

            # 🗑 Удалено когда
            del_item = QTableWidgetItem(str(rec.get('_deleted_at', '')))
            del_item.setForeground(QColor(DARK['yellow']))
            del_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(ri, 1 + n_data, del_item)

            # ✅ Восстановлено когда
            rest_item = QTableWidgetItem(restored_at if already_restored else '—')
            rest_item.setForeground(QColor(DARK['green'] if already_restored else DARK['muted']))
            rest_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(ri, 2 + n_data, rest_item)

            # Источник
            src_item = QTableWidgetItem(str(rec.get('_source', '')))
            src_item.setForeground(QColor(DARK['dim']))
            src_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(ri, 3 + n_data, src_item)

            # Статус
            if already_restored:
                st = QTableWidgetItem("✅ Восстановлено")
                st.setForeground(QColor(DARK['green']))
            else:
                st = QTableWidgetItem("⏳ Ожидает")
                st.setForeground(QColor(DARK['dim']))
            st.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(ri, 4 + n_data, st)

        self.table.blockSignals(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._on_check)

        total    = len(filtered)
        restored = sum(1 for r in filtered
                       if _is_restored(
                           db_name,
                           r.get('_table', table_name),
                           str(r.get('id') or '')))
        pending  = total - restored
        g = DARK['green']; d = DARK['dim']
        self.count_lbl.setText(
            f"Найдено: {total}  •  "
            f"<span style='color:{g}'>✅ {restored} восстановлено</span>  •  "
            f"<span style='color:{d}'>⏳ {pending} ожидает</span>"
        )
        self.count_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.restore_btn.setEnabled(pending > 0)

    def _show_empty(self, empty: bool):
        self.table.setVisible(not empty)
        self.empty_lbl.setVisible(empty)

    def _select_all(self):
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(Qt.CheckState.Checked)

    def _on_check(self, item):
        n = sum(1 for r in range(self.table.rowCount())
                if self.table.item(r, 0) and
                   self.table.item(r, 0).checkState() == Qt.CheckState.Checked)
        self.restore_btn.setText(
            f"♻  Восстановить ({n})" if n else "♻  Восстановить выбранные")

    def _on_restore(self):
        sel = [self._records[r]
               for r in range(self.table.rowCount())
               if self.table.item(r, 0) and
                  self.table.item(r, 0).checkState() == Qt.CheckState.Checked]
        if sel:
            self.restore_requested.emit(sel)



# Поток резервного копирования
class BackupWorker(QThread):
    """Создаёт резервную копию БД через pg_dump / mysqldump."""
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)   # путь к файлу
    error    = pyqtSignal(str)

    def __init__(self, db_type: str, db_name: str,
                 output_path: str, parent=None):
        super().__init__(parent)
        self.db_type     = db_type
        self.db_name     = db_name
        self.output_path = output_path

    def run(self):
        import subprocess
        try:
            if _HAS_CONFIG:
                cfg = get_config()
            else:
                self.error.emit("Конфиг недоступен")
                return

            self.progress.emit(10)

            if self.db_type == "PostgreSQL":
                cfg_pg = cfg.pg
                env = os.environ.copy()
                env["PGPASSWORD"] = cfg_pg.password

                cmd = [
                    "pg_dump",
                    f"--host={cfg_pg.host}",
                    f"--port={cfg_pg.port}",
                    f"--username={cfg_pg.user}",
                    "--format=plain",
                    "--no-password",
                    f"--file={self.output_path}",
                    self.db_name,
                ]
                self.progress.emit(30)
                result = subprocess.run(
                    cmd, env=env, capture_output=True, text=True, timeout=120)

                if result.returncode != 0:
                    self.error.emit(
                        f"pg_dump завершился с ошибкой:\n{result.stderr[:300]}")
                    return

            elif self.db_type == "MySQL":
                cfg_my = cfg.mysql
                cmd = [
                    "mysqldump",
                    f"--host={cfg_my.host}",
                    f"--port={cfg_my.port}",
                    f"--user={cfg_my.user}",
                    f"--password={cfg_my.password}",
                    "--single-transaction",
                    "--routines",
                    "--triggers",
                    self.db_name,
                ]
                self.progress.emit(30)
                with open(self.output_path, "w", encoding="utf-8") as f_out:
                    result = subprocess.run(
                        cmd, stdout=f_out, capture_output=False,
                        text=True, timeout=120)

                if result.returncode != 0:
                    self.error.emit(
                        f"mysqldump завершился с ошибкой")
                    return
            else:
                self.error.emit(f"Неизвестный тип БД: {self.db_type}")
                return

            self.progress.emit(95)
            self.finished.emit(self.output_path)

        except FileNotFoundError as e:
            tool = "pg_dump" if self.db_type == "PostgreSQL" else "mysqldump"
            self.error.emit(
                f"Утилита {tool} не найдена.\n"
                f"Установите: sudo apt install postgresql-client / mysql-client")
        except subprocess.TimeoutExpired:
            self.error.emit("Превышено время ожидания (120 сек)")
        except Exception as e:
            self.error.emit(str(e))



# Дашборд
class DashboardTab(QWidget):
    """Главный дашборд — статистика и аномалии."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._anomalies = []
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)

        # Заголовок
        hdr = QHBoxLayout()
        t = QLabel("Дашборд")
        t.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {DARK['text']};")
        hdr.addWidget(t)
        hdr.addStretch()
        self.refresh_btn = QPushButton("🔄  Обновить")
        self.refresh_btn.setFixedHeight(32)
        self.refresh_btn.setStyleSheet(btn_style(DARK['bg2']))
        self.refresh_btn.clicked.connect(self.refresh)
        hdr.addWidget(self.refresh_btn)
        lay.addLayout(hdr)

        # Карточки статистики
        cards_lay = QHBoxLayout()
        cards_lay.setSpacing(12)

        self.card_dbs    = self._make_card("Баз данных",     "0", DARK['blue'])
        self.card_rest   = self._make_card("Восстановлений", "0", DARK['green'])
        self.card_anom   = self._make_card("Аномалий",       "0", DARK['red'])
        self.card_changes = self._make_card("Изменений",     "0", DARK['purple'])

        for card in [self.card_dbs, self.card_rest, self.card_anom, self.card_changes]:
            cards_lay.addWidget(card)
        lay.addLayout(cards_lay)

        # Аномалии
        anom_lbl = QLabel("⚠  Обнаруженные аномалии")
        anom_lbl.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        anom_lbl.setStyleSheet(f"color: {DARK['yellow']};")
        lay.addWidget(anom_lbl)

        self.anom_table = QTableWidget()
        self.anom_table.setColumnCount(6)
        self.anom_table.setHorizontalHeaderLabels(
            ["🕒 Время", "БД", "Таблица", "Удалено строк", "Тип аномалии", ""])
        self.anom_table.setStyleSheet(f"""
            QTableWidget {{
                background: {DARK['bg1']}; color: {DARK['text']};
                gridline-color: transparent;
                border: 1px solid {DARK['border']};
                border-radius: 10px; font-size: 12px;
            }}
            QTableWidget::item {{
                padding: 8px 10px;
                border-bottom: 1px solid {DARK['bg2']};
            }}
            QTableWidget::item:selected {{
                background: rgba(248,81,73,0.15);
            }}
            QHeaderView::section {{
                background: {DARK['bg2']}; color: {DARK['dim']};
                padding: 9px 10px; border: none;
                border-bottom: 1px solid {DARK['border2']};
                font-weight: 700; font-size: 10px; letter-spacing: 1px;
            }}
        """)
        self.anom_table.setAlternatingRowColors(True)
        self.anom_table.verticalHeader().setVisible(False)
        self.anom_table.horizontalHeader().setStretchLastSection(True)
        self.anom_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.anom_table.setFixedHeight(200)
        lay.addWidget(self.anom_table)

        # Последние восстановления
        rest_lbl = QLabel("✅  Последние восстановления")
        rest_lbl.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        rest_lbl.setStyleSheet(f"color: {DARK['green']};")
        lay.addWidget(rest_lbl)

        self.rest_table = QTableWidget()
        self.rest_table.setColumnCount(5)
        self.rest_table.setHorizontalHeaderLabels(
            ["🕒 Время", "БД", "Таблица", "ID", "Источник"])
        self.rest_table.setStyleSheet(self.anom_table.styleSheet())
        self.rest_table.setAlternatingRowColors(True)
        self.rest_table.verticalHeader().setVisible(False)
        self.rest_table.horizontalHeader().setStretchLastSection(True)
        self.rest_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        lay.addWidget(self.rest_table)

        # Автосканирование — лента сообщений
        scan_lbl = QLabel("🔍  Автосканирование (каждую минуту)")
        scan_lbl.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        scan_lbl.setStyleSheet(f"color: {DARK['dim']};")
        lay.addWidget(scan_lbl)

        self.scan_feed = QTextEdit()
        self.scan_feed.setReadOnly(True)
        self.scan_feed.setFixedHeight(130)
        self.scan_feed.setStyleSheet(f"""
            QTextEdit {{
                background: {DARK['bg1']}; color: {DARK['dim']};
                border: 1px solid {DARK['border']};
                border-radius: 8px;
                font-family: JetBrains Mono, Menlo, Monaco, Consolas, Courier New;
                font-size: 11px; padding: 8px;
            }}
        """)
        lay.addWidget(self.scan_feed)

    def _make_card(self, title: str, value: str, color: str) -> QWidget:
        card = QWidget()
        card.setStyleSheet(f"""
            QWidget {{
                background: {DARK['bg1']};
                border: 1px solid {DARK['border2']};
                border-radius: 12px;
            }}
        """)
        vl = QVBoxLayout(card)
        vl.setContentsMargins(20, 16, 20, 16)
        vl.setSpacing(6)

        val_lbl = QLabel(value)
        val_lbl.setFont(QFont("Arial", 32, QFont.Weight.Bold))
        val_lbl.setStyleSheet(f"color: {color}; border: none;")
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"color: {DARK['dim']}; font-size: 12px; border: none;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Сохраняем ссылку на лейбл значения
        val_lbl.setObjectName(f"val_{title}")
        vl.addWidget(val_lbl)
        vl.addWidget(title_lbl)

        card._val_lbl = val_lbl
        return card

    def add_anomaly(self, db: str, table: str, count: int, kind: str):
        """Добавляет аномалию в таблицу дашборда с кнопкой Принять."""
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self._anomalies.append((ts, db, table, count, kind))

        ri = self.anom_table.rowCount()
        self.anom_table.insertRow(ri)

        colors = {
            "Массовое удаление": DARK['yellow'],
            "Удалена вся таблица": DARK['red'],
        }
        row_color = colors.get(kind, DARK['amber'])

        for ci, val in enumerate([ts, db, table, str(count), kind]):
            item = QTableWidgetItem(str(val))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if ci == 4:
                item.setForeground(QColor(row_color))
                item.setFont(QFont("Arial", 11, QFont.Weight.Bold))
            self.anom_table.setItem(ri, ci, item)

        # Кнопка "Принять" в последней колонке
        accept_btn = QPushButton("✓ Принять")
        accept_btn.setFixedHeight(26)
        accept_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(63,185,80,0.15); color: {DARK['green']};
                border: 1px solid {DARK['green']}60; border-radius: 5px;
                font-size: 11px; font-weight: 700; padding: 0 8px;
            }}
            QPushButton:hover {{
                background: rgba(63,185,80,0.3);
            }}
        """)
        # Захватываем ri в замыкание
        def make_accept(row):
            return lambda: self._accept_anomaly(row)
        accept_btn.clicked.connect(make_accept(ri))
        self.anom_table.setCellWidget(ri, 5, accept_btn)

        self.anom_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.anom_table.horizontalHeader().setStretchLastSection(False)
        # Обновляем счётчик
        self.card_anom._val_lbl.setText(str(len(self._anomalies)))
        self.card_anom._val_lbl.setStyleSheet(
            f"color: {DARK['red']}; border: none;")

    def _accept_anomaly(self, row: int):
        """Удаляет принятую аномалию из таблицы."""
        self.anom_table.removeRow(row)
        # Переподключаем кнопки с правильными индексами
        for r in range(self.anom_table.rowCount()):
            btn = self.anom_table.cellWidget(r, 5)
            if btn:
                try:
                    btn.clicked.disconnect()
                except Exception:
                    pass
                def make_accept(row):
                    return lambda: self._accept_anomaly(row)
                btn.clicked.connect(make_accept(r))
        # Обновляем счётчик
        remaining = self.anom_table.rowCount()
        self.card_anom._val_lbl.setText(str(remaining))
        if remaining == 0:
            self.card_anom._val_lbl.setStyleSheet(
                f"color: {DARK['green']}; border: none;")

    def add_scan_message(self, msg: str):
        """Добавляет сообщение в ленту автосканирования."""
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self.scan_feed.append(f"[{ts}]  {msg}")
        # Прокрутка вниз
        sb = self.scan_feed.verticalScrollBar()
        sb.setValue(sb.maximum())

    def update_stats(self, n_dbs: int, n_slots: int, n_restored: int):
        self.card_dbs._val_lbl.setText(str(n_dbs))
        self.card_rest._val_lbl.setText(str(n_restored))

    def update_changes_count(self, count: int):
        self.card_changes._val_lbl.setText(str(count))

    def refresh_restorations(self):
        """Обновляет таблицу последних восстановлений и счётчик."""
        if not _HAS_STATE:
            return
        try:
            all_items = get_restorations()
            # Обновляем счётчик
            self.card_rest._val_lbl.setText(str(len(all_items)))
            items = all_items[-10:]
            items.reverse()
            self.rest_table.setRowCount(len(items))
            for ri, r in enumerate(items):
                for ci, val in enumerate([
                    r.get("ts",""), r.get("db",""),
                    r.get("table",""), r.get("pk_value",""),
                    r.get("source","")
                ]):
                    item = QTableWidgetItem(str(val))
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    if ci == 4:
                        c = DARK['blue'] if val == "GUI" else DARK['amber']
                        item.setForeground(QColor(c))
                    self.rest_table.setItem(ri, ci, item)
            self.rest_table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents)
            self.rest_table.horizontalHeader().setStretchLastSection(True)
        except Exception:
            pass

    def refresh(self):
        """Обновляет все карточки и таблицы дашборда из state.json."""
        if not _HAS_STATE:
            return
        try:
            # Восстановления
            n_rest = len(get_restorations())
            self.card_rest._val_lbl.setText(str(n_rest))
        except Exception:
            pass
        try:
            # Изменения
            n_chg = len(get_changes())
            self.card_changes._val_lbl.setText(str(n_chg))
        except Exception:
            pass
        self.refresh_restorations()


# ===========================================================================
# Поток автосканирования
# ===========================================================================
class AutoScanWorker(QThread):
    """Сканирует все БД каждую минуту и сообщает об удалениях и аномалиях."""
    found     = pyqtSignal(str, str, list)   # db_name, table, records
    anomaly   = pyqtSignal(str, str, int, str)  # db, table, count, kind
    message   = pyqtSignal(str)
    stats_upd = pyqtSignal(int, int, int)    # n_dbs, n_slots, n_restored

    ANOMALY_THRESHOLD = 5   # порог для аномалии

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._seen_anomalies = set()  # дедупликация: (db, table, kind)

    def stop(self):
        """Останавливает поток корректно — ждёт завершения."""
        self._running = False
        self.quit()      # просим поток завершить event loop
        self.wait(3000)  # ждём до 3 секунд

    def run(self):
        import time
        while self._running:
            self._scan_cycle()
            # Ждём 60 секунд с возможностью прервать
            for _ in range(60):
                if not self._running:
                    return
                time.sleep(1)

    def _scan_cycle(self):
        try:
            if _HAS_CONFIG:
                cfg = get_config()
            else:
                return

            total_dbs = len(cfg.pg.databases) + len(cfg.mysql.databases)
            total_slots = 0
            total_restored = len(get_restorations()) if _HAS_STATE else 0

            self.message.emit(
                f"Сканирую {total_dbs} баз данных...")

            # Сканируем PostgreSQL
            for db in cfg.pg.databases:
                try:
                    from core.postgres_wal import PostgresWALParser
                except ImportError:
                    try:
                        from postgres_wal import PostgresWALParser
                    except ImportError:
                        break

                try:
                    p = PostgresWALParser(database=db)
                    p.connect()
                    tables = p.get_tables()

                    # Считаем слоты
                    import psycopg2
                    with p._conn.cursor() as cur:
                        cur.execute(
                            "SELECT count(*) FROM pg_replication_slots "
                            "WHERE database = %s", (db,))
                        total_slots += cur.fetchone()[0]

                    for tbl in tables:
                        # Читаем ВСЕ изменения через WAL (INSERT/UPDATE/DELETE)
                        try:
                            all_events = p.read_wal_raw(tbl,
                                kind_filter="all", since_dt="", until_dt="")
                            for ev in all_events:
                                kind = ev.get("_kind","").upper()
                                if _HAS_STATE and kind in ("INSERT","UPDATE","DELETE"):
                                    try:
                                        record_change(
                                            db=db, table=tbl,
                                            action=kind,
                                            data={k:v for k,v in ev.items()
                                                  if not k.startswith("_")},
                                            source="auto"
                                        )
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                        recs = p.get_deleted_records(tbl, since_hours=1)
                        if recs:
                            count = len(recs)
                            self.found.emit(db, tbl, recs)

                            if _HAS_STATE:
                                try:
                                    record_deletion(db, tbl, count,
                                        recs[0].get("_deleted_at",""), "auto")
                                except Exception:
                                    pass

                            if count >= 50:
                                anom_key = (db, tbl, "Удалена вся таблица")
                                if anom_key not in self._seen_anomalies:
                                    self._seen_anomalies.add(anom_key)
                                    self.anomaly.emit(
                                        db, tbl, count, "Удалена вся таблица")
                                    self.message.emit(
                                        f"🚨 АНОМАЛИЯ: {db}.{tbl} — "
                                        f"удалена вся таблица ({count} строк)!")
                            elif count > self.ANOMALY_THRESHOLD:
                                anom_key = (db, tbl, "Массовое удаление")
                                if anom_key not in self._seen_anomalies:
                                    self._seen_anomalies.add(anom_key)
                                    self.anomaly.emit(
                                        db, tbl, count, "Массовое удаление")
                                    self.message.emit(
                                        f"⚠  Аномалия: {db}.{tbl} — "
                                        f"удалено {count} строк (>{self.ANOMALY_THRESHOLD})")
                            else:
                                self.message.emit(
                                    f"ℹ  {db}.{tbl}: найдено {count} удалений")
                    p.disconnect()
                except Exception as e:
                    self.message.emit(f"⚡ {db}: {str(e)[:60]}")

            self.stats_upd.emit(total_dbs, total_slots, total_restored)
            self.message.emit("✔  Цикл сканирования завершён")

        except Exception as e:
            self.message.emit(f"Ошибка автосканирования: {e}")


# ===========================================================================
# Вкладка изменений в БД
# ===========================================================================
class ChangesTab(QWidget):
    """
    Показывает все изменения в БД: INSERT, UPDATE, DELETE.
    Читает WAL-логи через read_wal_raw для полной картины.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.reload()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        # Заголовок
        hdr = QHBoxLayout()
        t = QLabel("📊  Все изменения в базах данных")
        t.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {DARK['text']};")
        hdr.addWidget(t)
        hdr.addStretch()

        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(f"color: {DARK['dim']}; font-size: 12px;")
        hdr.addWidget(self.count_lbl)

        reload_btn = QPushButton("🔄")
        reload_btn.setFixedSize(32, 32)
        reload_btn.setStyleSheet(btn_style(DARK['bg2']))
        reload_btn.setToolTip("Обновить")
        reload_btn.clicked.connect(self.reload)
        hdr.addWidget(reload_btn)
        lay.addLayout(hdr)

        # Фильтры
        fb = QHBoxLayout()
        fb.setSpacing(8)

        self.filter_action = QComboBox()
        self.filter_action.addItems(["Все операции", "INSERT", "UPDATE", "DELETE"])
        self.filter_action.setStyleSheet(combo_style())
        self.filter_action.setFixedWidth(160)
        self.filter_action.currentTextChanged.connect(self._apply_filter)
        fb.addWidget(QLabel("Операция:"))

        self.filter_db = QComboBox()
        self.filter_db.addItem("Все БД")
        self.filter_db.setStyleSheet(combo_style())
        self.filter_db.setFixedWidth(150)
        self.filter_db.currentTextChanged.connect(self._apply_filter)

        fb.addWidget(self.filter_action)
        fb.addWidget(QLabel("  БД:"))
        fb.addWidget(self.filter_db)

        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍  Поиск...")
        self.search.setStyleSheet(input_style())
        self.search.setFixedHeight(32)
        self.search.textChanged.connect(self._apply_filter)
        fb.addWidget(self.search)
        fb.addStretch()
        lay.addLayout(fb)

        # Прогресс
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(f"""
            QProgressBar {{ background: {DARK['bg2']}; border: none; border-radius: 2px; }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {DARK['blue']}, stop:1 {DARK['purple']});
                border-radius: 2px;
            }}
        """)
        lay.addWidget(self.progress)

        # Таблица
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["🕒 Время", "⚡ Операция", "БД", "Таблица", "Данные", "Источник"])
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background: {DARK['bg0']}; color: {DARK['text']};
                gridline-color: transparent;
                border: 1px solid {DARK['border']};
                border-radius: 10px; font-size: 12px;
            }}
            QTableWidget::item {{
                padding: 7px 10px;
                border-bottom: 1px solid {DARK['bg2']};
            }}
            QTableWidget::item:selected {{
                background: rgba(88,166,255,0.12);
            }}
            QHeaderView::section {{
                background: {DARK['bg1']}; color: {DARK['dim']};
                padding: 9px 10px; border: none;
                border-bottom: 1px solid {DARK['border2']};
                font-weight: 700; font-size: 10px; letter-spacing: 1px;
            }}
        """)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        lay.addWidget(self.table)

        self.empty_lbl = QLabel(
            "\ud83d\udcca  \u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445 \u043e\u0431 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f\u0445\n\n"
            "\u0412\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u0435 \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u2014 INSERT, UPDATE, DELETE\n"
            "\u0431\u0443\u0434\u0443\u0442 \u043e\u0442\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u044b \u0437\u0434\u0435\u0441\u044c.")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet(
            f"color: {DARK['muted']}; font-size: 14px; padding: 60px;")
        lay.addWidget(self.empty_lbl)

        self._all_rows = []
        self._show_empty(True)

    def _show_empty(self, empty: bool):
        self.table.setVisible(not empty)
        self.empty_lbl.setVisible(empty)

    def reload(self):
        """Читает все изменения из state.json."""
        if not _HAS_STATE:
            return
        try:
            events = get_all_events(limit=500)
        except Exception:
            return

        self._all_rows = []
        dbs = set()

        for e in events:
            action = e.get("action", "")
            db     = e.get("db", "")
            table  = e.get("table", "")
            ts     = e.get("ts", "")
            src    = e.get("source", "")
            dbs.add(db)

            if action == "RESTORED":
                op      = "INSERT"
                color   = DARK['green']
                details = f"id={e.get('pk_value','?')} восстановлено"
            elif action == "DETECTED":
                op      = "DELETE"
                color   = DARK['red']
                details = f"удалено строк: {e.get('count','?')}"
            elif action == "SCANNED":
                op      = "SCAN"
                color   = DARK['blue']
                details = f"найдено удалений: {e.get('found',0)}"
            elif action in ("INSERT", "UPDATE", "DELETE"):
                op      = action
                color   = (DARK['green'] if action == "INSERT"
                           else DARK['yellow'] if action == "UPDATE"
                           else DARK['red'])
                data    = e.get("data", {})
                pk      = data.get("id", "")
                details = f"id={pk}  " + ",  ".join(
                    f"{k}={v}" for k,v in list(data.items())[:3] if k != "id")
            else:
                continue

            self._all_rows.append({
                "ts": ts, "op": op, "color": color,
                "db": db, "table": table,
                "details": details, "source": src,
            })

        # Обновляем фильтр БД
        cur_db = self.filter_db.currentText()
        self.filter_db.blockSignals(True)
        self.filter_db.clear()
        self.filter_db.addItem("Все БД")
        for db in sorted(dbs):
            if db:
                self.filter_db.addItem(db)
        idx = self.filter_db.findText(cur_db)
        self.filter_db.setCurrentIndex(max(0, idx))
        self.filter_db.blockSignals(False)

        self._apply_filter()

    def _apply_filter(self):
        action_f = self.filter_action.currentText()
        db_f     = self.filter_db.currentText()
        search_f = self.search.text().lower()

        rows = self._all_rows
        if action_f != "Все операции":
            rows = [r for r in rows if r["op"] == action_f]
        if db_f != "Все БД":
            rows = [r for r in rows if r["db"] == db_f]
        if search_f:
            rows = [r for r in rows
                    if any(search_f in str(v).lower()
                           for v in r.values())]

        self._show_empty(len(rows) == 0)
        if not rows:
            self.count_lbl.setText("")
            return

        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))

        action_colors = {
            "INSERT": DARK['green'],
            "DELETE": DARK['red'],
            "UPDATE": DARK['yellow'],
            "SCAN":   DARK['blue'],
        }
        src_icons = {"GUI": "🖥", "CLI": "⌨", "auto": "🤖"}

        for ri, r in enumerate(rows):
            def cell(text, color=None, bold=False):
                item = QTableWidgetItem(str(text))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled |
                              Qt.ItemFlag.ItemIsSelectable)
                if color:
                    item.setForeground(QColor(color))
                if bold:
                    item.setFont(QFont("Arial", 11, QFont.Weight.Bold))
                return item

            op    = r["op"]
            c     = action_colors.get(op, DARK['dim'])
            src   = r["source"]
            icon  = src_icons.get(src.upper(),
                    src_icons.get(src.lower(), "?"))

            self.table.setItem(ri, 0, cell(r["ts"],           DARK['dim']))
            self.table.setItem(ri, 1, cell(op,                c, bold=True))
            self.table.setItem(ri, 2, cell(r["db"],           DARK['text']))
            self.table.setItem(ri, 3, cell(r["table"],        DARK['text']))
            self.table.setItem(ri, 4, cell(r["details"],      DARK['dim']))
            self.table.setItem(ri, 5, cell(f"{icon} {src}",   DARK['muted']))

        self.table.blockSignals(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)

        ins  = sum(1 for r in rows if r["op"] == "INSERT")
        dlt  = sum(1 for r in rows if r["op"] == "DELETE")
        scn  = sum(1 for r in rows if r["op"] == "SCAN")
        cg = DARK["green"]; cr = DARK["red"]; cb = DARK["blue"]
        self.count_lbl.setText(
            f"Всего: {len(rows)}  •  "
            f"<span style='color:{cg}'>INSERT {ins}</span>  •  "
            f"<span style='color:{cr}'>DELETE {dlt}</span>  •  "
            f"<span style='color:{cb}'>SCAN {scn}</span>"
        )
        self.count_lbl.setTextFormat(Qt.TextFormat.RichText)


# ===========================================================================
# Диалог добавления новой БД
# ===========================================================================
class AddDatabaseDialog(QDialog):
    """Диалог добавления новой базы данных в конфиг."""
    db_added = pyqtSignal(str, str)   # db_type, db_name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить базу данных")
        self.setMinimumSize(480, 380)
        self.resize(500, 400)
        self.setStyleSheet(f"""
            QDialog {{
                background: {DARK['bg1']};
                border: 1px solid {DARK['border2']};
                border-radius: 12px;
            }}
            QLabel {{ color: {DARK['text']}; font-size: 13px; }}
        """)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(14)

        title = QLabel("➕  Добавить базу данных")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {DARK['text']};")
        lay.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {DARK['border']};")
        lay.addWidget(sep)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        # Тип СУБД
        self.type_cb = QComboBox()
        self.type_cb.addItems(["PostgreSQL", "MySQL"])
        self.type_cb.setStyleSheet(combo_style())
        form.addRow("Тип СУБД:", self.type_cb)

        # Имя БД
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("например: my_database")
        self.name_edit.setStyleSheet(input_style())
        self.name_edit.setMinimumHeight(34)
        form.addRow("Имя БД:", self.name_edit)

        # Хост
        self.host_edit = QLineEdit("localhost")
        self.host_edit.setStyleSheet(input_style())
        self.host_edit.setMinimumHeight(34)
        form.addRow("Хост:", self.host_edit)

        # Порт
        self.port_edit = QLineEdit()
        self.port_edit.setStyleSheet(input_style())
        self.port_edit.setMinimumHeight(34)
        self.type_cb.currentTextChanged.connect(
            lambda t: self.port_edit.setText("5432" if t == "PostgreSQL" else "3306"))
        self.port_edit.setText("5432")
        form.addRow("Порт:", self.port_edit)

        # Пользователь
        self.user_edit = QLineEdit("admin")
        self.user_edit.setStyleSheet(input_style())
        self.user_edit.setMinimumHeight(34)
        form.addRow("Пользователь:", self.user_edit)

        # Пароль
        self.pass_edit = QLineEdit("oksat")
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_edit.setStyleSheet(input_style())
        self.pass_edit.setMinimumHeight(34)
        form.addRow("Пароль:", self.pass_edit)

        lay.addLayout(form)
        lay.addStretch()

        # Кнопки
        btns = QHBoxLayout()

        cancel_btn = QPushButton("Отмена")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setStyleSheet(btn_style(DARK['bg2']))
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)

        self.add_btn = QPushButton("✅  Добавить")
        self.add_btn.setFixedHeight(36)
        self.add_btn.setStyleSheet(btn_style(DARK['blue'], '#2563eb'))
        self.add_btn.clicked.connect(self._on_add)
        btns.addWidget(self.add_btn)

        lay.addLayout(btns)

    def _on_add(self):
        db_type = self.type_cb.currentText()
        db_name = self.name_edit.text().strip()
        host    = self.host_edit.text().strip() or "localhost"
        port    = self.port_edit.text().strip()
        user    = self.user_edit.text().strip() or "admin"
        password = self.pass_edit.text()

        if not db_name:
            QMessageBox.warning(self, "Ошибка", "Введите имя базы данных.")
            return

        # Записываем в конфиг
        import configparser
        conf_paths = [
            "/etc/wal-recovery/databases.conf",
            os.path.expanduser("~/.config/wal-recovery/databases.conf"),
        ]
        conf_file = None
        for p in conf_paths:
            if os.path.exists(p):
                conf_file = p
                break
        if not conf_file:
            conf_file = conf_paths[1]
            os.makedirs(os.path.dirname(conf_file), exist_ok=True)

        parser = configparser.ConfigParser()
        parser.read(conf_file, encoding="utf-8")

        section = "postgresql" if db_type == "PostgreSQL" else "mysql"
        if not parser.has_section(section):
            parser.add_section(section)

        # Обновляем параметры подключения
        parser.set(section, "host",     host)
        parser.set(section, "port",     port)
        parser.set(section, "user",     user)
        parser.set(section, "password", password)

        # Добавляем БД в список
        existing = parser.get(section, "databases", fallback="")
        db_list  = [d.strip() for d in existing.split(",") if d.strip()]
        if db_name not in db_list:
            db_list.append(db_name)
        parser.set(section, "databases", ", ".join(db_list))

        try:
            with open(conf_file, "w", encoding="utf-8") as f:
                parser.write(f)
            if _HAS_CONFIG:
                try:
                    from core.config import reset_config
                    reset_config()
                except Exception:
                    try:
                        from config import reset_config
                        reset_config()
                    except Exception:
                        pass
            self.db_added.emit(db_type, db_name)
            QMessageBox.information(
                self, "Готово",
                f"✅  База данных добавлена!\n\n"
                f"  Тип:  {db_type}\n"
                f"  Имя:  {db_name}\n"
                f"  Конфиг: {conf_file}"
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить конфиг:\n{e}")

# ===========================================================================
# Главное окно
# ===========================================================================
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self._parser        = None
        self._scan_worker    = None
        self._rest_worker    = None
        self._autoscan_worker = None
        self._current_table  = ""
        self._current_db     = ""
        self._current_db_type = ""
        self._setup_window()
        self._build_ui()
        self._apply_theme()
        self._start_autoscan()

    def _setup_window(self):
        self.setWindowTitle("WAL Recovery Tool — Восстановление удалённых записей")
        self.setMinimumSize(1100, 700)
        self.resize(1300, 800)

    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Шапка ──────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(60)
        header.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {DARK['bg1']}, stop:1 #0d1b2e);
            border-bottom: 1px solid {DARK['border2']};
        """)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 0, 24, 0)
        hl.setSpacing(12)

        # Иконка-точка
        logo = QLabel("WAL Recovery Tool")
        logo.setFont(QFont("Arial", 15, QFont.Weight.Bold))
        logo.setStyleSheet(f"color: {DARK['text']}; letter-spacing: 0.5px;")
        hl.addWidget(logo)
        hl.addStretch()

        # Кнопка авто-сканирования
        self.autoscan_btn = QPushButton("● Авто-скан: вкл")
        self.autoscan_btn.setFixedHeight(28)
        self.autoscan_btn.setCheckable(True)
        self.autoscan_btn.setChecked(True)
        self.autoscan_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(63,185,80,0.15); color: {DARK['green']};
                border: 1px solid {DARK['green']}40; border-radius: 6px;
                padding: 0 12px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:checked {{
                background: rgba(63,185,80,0.15); color: {DARK['green']};
                border-color: {DARK['green']}80;
            }}
            QPushButton:!checked {{
                background: rgba(248,81,73,0.12); color: {DARK['red']};
                border-color: {DARK['red']}40;
            }}
        """)
        self.autoscan_btn.clicked.connect(self._toggle_autoscan)
        hl.addWidget(self.autoscan_btn)

        root.addWidget(header)

        # ── Сплиттер ───────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(f"QSplitter::handle {{ background: {DARK['bg2']}; }}")

        # Левая панель
        self.db_panel = DBSelectorPanel()
        self.db_panel.setFixedWidth(290)
        self.db_panel.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {DARK['bg1']}, stop:1 {DARK['bg0']});
            border-right: 1px solid {DARK['border']};
        """)
        self.db_panel.table_selected.connect(self._on_scan_requested)
        self.db_panel.backup_requested.connect(self._on_backup_requested)
        splitter.addWidget(self.db_panel)

        # Правая часть
        right = QWidget()
        right.setStyleSheet(f"background: {DARK['bg0']};")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 16, 16, 16)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane  {{
                border: none;
                background: {DARK['bg0']};
            }}
            QTabBar::tab {{
                background: transparent; color: {DARK['muted']};
                padding: 10px 24px; border: none;
                border-bottom: 2px solid transparent;
                font-size: 13px; font-weight: 500;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                color: {DARK['text']};
                border-bottom: 2px solid {DARK['blue']};
                background: rgba(88,166,255,0.06);
            }}
            QTabBar::tab:hover {{
                color: {DARK['cyan']};
                background: rgba(255,255,255,0.03);
            }}
            QTabBar::tab:!selected {{
                margin-top: 2px;
            }}
        """)

        # Вкладка 1: Дашборд
        self.dashboard_tab = DashboardTab()
        self.tabs.addTab(self.dashboard_tab, "🏠  Дашборд")

        # Вкладка 2: Удалённые записи
        self.records_widget = DeletedRecordsTable()
        self.records_widget.restore_requested.connect(self._on_restore_requested)
        self.tabs.addTab(self.records_widget, "🗑  Удалённые записи")

        # Вкладка 3: Изменения в БД
        self.changes_tab = ChangesTab()
        self.tabs.addTab(self.changes_tab, "📊  Изменения")

        # Автообновление истории при переключении на вкладку
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # log_widget нужен для _log()
        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)

        rl.addWidget(self.tabs)
        splitter.addWidget(right)
        splitter.setSizes([290, 1010])
        root.addWidget(splitter)

        # ── Статус-бар ─────────────────────────────────────────────
        self.status = QStatusBar()
        self.status.setStyleSheet(f"""
            QStatusBar {{
                background: {DARK['bg1']}; color: {DARK['dim']};
                font-size: 11px; padding: 0 16px;
                border-top: 1px solid {DARK['border']};
            }}
            QStatusBar::item {{ border: none; }}
        """)
        self.setStatusBar(self.status)
        self.status.showMessage(
            "Готов к работе.  GUI: wal-recovery  |  CLI: wal-recovery --cli  |  "
            "Справка: wal-recovery --help"
        )

    # ------------------------------------------------------------------
    def _on_tab_changed(self, index: int):
        widget = self.tabs.widget(index)
        if widget is self.changes_tab:
            self.changes_tab.reload()
        elif widget is self.dashboard_tab:
            self.dashboard_tab.refresh()
        elif widget is self.records_widget:
            # Перезагружаем статусы — могли измениться между сессиями
            if self.records_widget._records:
                self.records_widget.load_records(
                    self.records_widget._records,
                    self._current_table,
                    self._current_db)

    # ── Резервное копирование ──────────────────────────────────────────
    def _on_backup_requested(self, db_type: str, db_name: str):
        """Диалог выбора пути и запуск резервного копирования."""
        from datetime import datetime
        from PyQt6.QtWidgets import QFileDialog, QProgressDialog

        # Формат имени файла
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext     = "sql"
        default = f"{db_name}_backup_{ts}.{ext}"

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            f"Сохранить резервную копию {db_name}",
            os.path.expanduser(f"~/{default}"),
            "SQL файлы (*.sql);;Все файлы (*)"
        )
        if not out_path:
            return

        # Прогресс-диалог
        self._backup_progress = QProgressDialog(
            f"Создаю резервную копию {db_name}...", "Отмена", 0, 100, self)
        self._backup_progress.setWindowTitle("Резервное копирование")
        self._backup_progress.setMinimumWidth(400)
        self._backup_progress.setStyleSheet(f"""
            QProgressDialog {{
                background: {DARK['bg1']}; color: {DARK['text']};
                border: 1px solid {DARK['border2']}; border-radius: 10px;
            }}
            QProgressBar {{
                background: {DARK['bg2']}; border: none; border-radius: 4px; height: 8px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {DARK['blue']}, stop:1 {DARK['purple']});
                border-radius: 4px;
            }}
            QPushButton {{
                background: {DARK['bg2']}; color: {DARK['text']};
                border: 1px solid {DARK['border2']}; border-radius: 6px;
                padding: 6px 16px;
            }}
        """)
        self._backup_progress.show()

        self._backup_worker = BackupWorker(db_type, db_name, out_path)
        self._backup_worker.progress.connect(self._backup_progress.setValue)
        self._backup_worker.finished.connect(self._on_backup_done)
        self._backup_worker.error.connect(self._on_backup_error)
        self._backup_progress.canceled.connect(
            self._backup_worker.terminate)
        self._backup_worker.start()

    def _on_backup_done(self, path: str):
        self._backup_progress.setValue(100)
        self._backup_progress.close()
        file_size = os.path.getsize(path) if os.path.exists(path) else 0
        size_str  = (f"{file_size // 1024} КБ" if file_size < 1024*1024
                     else f"{file_size // (1024*1024)} МБ")
        lines = [
            "Резервная копия успешно сохранена!",
            "",
            f"Файл: {path}",
            f"Размер: {size_str}",
            "",
            "Для восстановления:",
            "  PostgreSQL:  psql -U admin -d <база> < файл.sql",
            "  MySQL:       mysql -u admin -poksat <база> < файл.sql",
        ]
        QMessageBox.information(self, "Резервная копия создана",
                                chr(10).join(lines))
        self.dashboard_tab.add_scan_message(
            f"💾 Резервная копия создана: {os.path.basename(path)} ({size_str})")
        self.status.showMessage(f"Резервная копия: {path}")

    def _on_backup_error(self, err: str):
        self._backup_progress.close()
        QMessageBox.critical(self, "Ошибка резервного копирования", err)
        self.status.showMessage("Ошибка резервного копирования")

    # ── Автосканирование ────────────────────────────────────────────────
    def _start_autoscan(self):
        """Запускает фоновое автосканирование каждую минуту."""
        # Останавливаем предыдущий если был
        if self._autoscan_worker and self._autoscan_worker.isRunning():
            self._autoscan_worker.stop()
        self._autoscan_worker = AutoScanWorker()
        self._autoscan_worker.message.connect(self._on_autoscan_message)
        self._autoscan_worker.anomaly.connect(self._on_autoscan_anomaly)
        self._autoscan_worker.stats_upd.connect(self._on_autoscan_stats)
        self._autoscan_worker.start()

    def _toggle_autoscan(self, checked: bool):
        """Включает/выключает автосканирование."""
        if checked:
            self.autoscan_btn.setText("● Авто-скан: вкл")
            self.autoscan_btn.setEnabled(False)
            self._start_autoscan()
            self.autoscan_btn.setEnabled(True)
        else:
            self.autoscan_btn.setText("○ Авто-скан: выкл")
            self.autoscan_btn.setEnabled(False)
            if self._autoscan_worker:
                w = self._autoscan_worker
                self._autoscan_worker = None
                w.stop()  # stop() теперь ждёт завершения
            self.autoscan_btn.setEnabled(True)
            self.dashboard_tab.add_scan_message("⏸  Автосканирование остановлено")

    def _on_autoscan_message(self, msg: str):
        self.dashboard_tab.add_scan_message(msg)

    def _on_autoscan_anomaly(self, db: str, table: str, count: int, kind: str):
        self.dashboard_tab.add_anomaly(db, table, count, kind)
        # Показываем предупреждение в заголовке
        self.autoscan_btn.setText(f"⚠ Аномалия: {table}!")
        self.autoscan_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(248,81,73,0.2); color: {DARK['red']};
                border: 1px solid {DARK['red']}; border-radius: 6px;
                padding: 0 12px; font-size: 11px; font-weight: 700;
            }}
        """)
        # Переключаемся на дашборд
        self.tabs.setCurrentWidget(self.dashboard_tab)

    def _on_autoscan_stats(self, n_dbs: int, n_slots: int, n_restored: int):
        self.dashboard_tab.update_stats(n_dbs, n_slots, n_restored)
        self.dashboard_tab.refresh_restorations()

    def _apply_theme(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {DARK['bg0']}; color: {DARK['text']};
                font-family: {APP_FONT_UI};
            }}
            QScrollBar:vertical {{
                background: {DARK['bg1']}; width: 6px; border: none; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {DARK['border2']}; border-radius: 3px; min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {DARK['dim']};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; background: none;
            }}
            QScrollBar:horizontal {{
                background: {DARK['bg1']}; height: 6px; border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {DARK['border2']}; border-radius: 3px;
            }}
            QLabel {{ color: {DARK['text']}; }}
            QToolTip {{
                background: {DARK['bg3']}; color: {DARK['text']};
                border: 1px solid {DARK['border2']}; border-radius: 6px;
                padding: 6px 10px; font-size: 12px;
            }}
            QSplitter::handle {{
                background: {DARK['border']};
            }}
        """)

    # ------------------------------------------------------------------
    def _log(self, text: str):
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self.log_widget.append(f"[{ts}]  {text}")

    # ------------------------------------------------------------------
    def _on_scan_requested(self, db_type: str, db_name: str,
                            table_name: str, hours: str):
        if not table_name:
            QMessageBox.warning(self, "Предупреждение", "Выберите таблицу.")
            return

        self._current_table   = table_name
        self._current_db      = db_name
        self._current_db_type = db_type
        self._log(f"▶ Сканирование: {db_type} / {db_name} / {table_name}  (за {hours} ч)")
        self.status.showMessage(f"Сканируем логи таблицы «{table_name}»...")

        self._parser = (PostgresWALParser(database=db_name)
                        if db_type == "PostgreSQL"
                        else MySQLBinlogParser(database=db_name))

        self.db_panel.scan_btn.setEnabled(False)
        self.records_widget.show_scanning(10)

        self._scan_worker = ScanWorker(self._parser, table_name, int(hours))
        self._scan_worker.progress.connect(self.records_widget.show_scanning)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.start()

    def _on_scan_done(self, records: list):
        self.db_panel.scan_btn.setEnabled(True)
        # load_records сам проверит state.json и покажет статус восстановлено/ожидает
        # _is_restored кэширует из state.json, так что после перезапуска GUI
        # уже восстановленные записи будут помечены и недоступны для повторного выбора
        self.records_widget.load_records(records, self._current_table, self._current_db)
        self.tabs.setCurrentIndex(1)  # Удалённые записи

        msg = f"Найдено {len(records)} удалённых записей в «{self._current_table}»"
        self.status.showMessage(msg)
        self._log(f"✅ {msg}")

        # Записываем в историю
        if _HAS_STATE:
            try:
                record_scan(self._current_db, self._current_table,
                            len(records), source="GUI")
                for rec in records:
                    record_deletion(
                        self._current_db, self._current_table,
                        1, rec.get("_deleted_at", ""), source="GUI")
            except Exception:
                pass

        if not records:
            self._log(
                "ℹ  Записи не найдены. Возможные причины:\n"
                "   • WAL/binlog не содержит удалений за выбранный период\n"
                "   • Плагин wal2json не установлен (PostgreSQL)\n"
                "   • Нет прав REPLICATION SLAVE (MySQL)\n"
                "   Проверьте настройки на вкладке ⚙ Настройки"
            )

    def _on_scan_error(self, err: str):
        self.db_panel.scan_btn.setEnabled(True)
        self.records_widget.show_scanning(0)
        self.status.showMessage(f"Ошибка: {err}")
        self._log(f"❌ Ошибка сканирования: {err}")
        QMessageBox.critical(self, "Ошибка сканирования", err)

    # ------------------------------------------------------------------
    def _on_restore_requested(self, records: list):
        n = len(records)
        reply = QMessageBox.question(
            self, "Подтверждение",
            f"Восстановить {n} запись(-ей) в таблицу «{self._current_table}»?\n\n"
            "Будет выполнен INSERT для каждой выбранной строки.\n"
            "Записи с конфликтом первичного ключа будут пропущены.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._log(f"♻ Восстановление {n} записей в «{self._current_table}»...")
        self.status.showMessage("Восстановление...")

        self._rest_worker = RestoreWorker(self._parser, self._current_table, records)
        self._rest_worker.finished.connect(self._on_restore_done)
        self._rest_worker.error.connect(self._on_restore_error)
        self._rest_worker.start()

    def _on_restore_done(self, ok: int, fail: int):
        restored_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Запоминаем восстановленные записи с датой
        for rec in self._rest_worker.records:
            pk_val = rec.get('id') or next(
                (v for k, v in rec.items() if not k.startswith('_')), None)
            if pk_val is not None:
                _mark_restored(self._current_db, self._current_table,
                               str(pk_val), restored_at)
                # Записываем в общий state.json — CLI тоже увидит
                if _HAS_STATE:
                    record_restoration(
                        db=self._current_db,
                        db_type=self._current_db_type,
                        table=self._current_table,
                        pk_value=str(pk_val),
                        deleted_at=rec.get('_deleted_at', ''),
                        source='GUI',
                        data=rec,
                    )

        msg = f"Восстановлено: {ok}  |  Пропущено: {fail}"
        self.status.showMessage(msg)
        self._log(f"✅ {msg}")

        # Пишем в файл истории
        try:
            os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
            with open(LOG_FILE, 'a', encoding='utf-8') as lf:
                for rec in self._rest_worker.records:
                    pk_val = rec.get('id', '?')
                    deleted_at = rec.get('_deleted_at', '')
                    lf.write(
                        f"[{restored_at}] RESTORED  "
                        f"table={self._current_table}  "
                        f"id={pk_val}  "
                        f"deleted_at={deleted_at}\n"
                    )
        except Exception:
            pass

        # Обновляем таблицу — статусы изменились
        self.records_widget.load_records(
            self.records_widget._records, self._current_table, self._current_db)

        # Обновляем вкладку изменений
        self.changes_tab.reload()

        # Обновляем весь дашборд
        try:
            self.dashboard_tab.refresh()
            self.dashboard_tab.add_scan_message(
                f"✅ Восстановлено {ok} записей в {self._current_table}")
        except Exception:
            pass

        QMessageBox.information(
            self, "Готово",
            f"✅ Успешно восстановлено: {ok}\n"
            f"⚠  Пропущено (конфликт / ошибка): {fail}\n\n"
            f"🕒 Время восстановления: {restored_at}"
        )

    def _on_restore_error(self, err: str):
        self.status.showMessage(f"Ошибка: {err}")
        self._log(f"❌ Ошибка восстановления: {err}")
        QMessageBox.critical(self, "Ошибка", err)


# ===========================================================================
# ===========================================================================
# CLI — интерактивный режим
# ===========================================================================
class CLI:
    """Простой интерактивный CLI для WAL Recovery Tool."""

    R  = '[0;31m'
    G  = '[0;32m'
    Y  = '[1;33m'
    B  = '[0;34m'
    C  = '[0;36m'
    W  = '[1;37m'
    DIM= '[2m'
    NC = '[0m'

    def _ok(self, m):  print(f"{self.G}✔{self.NC}  {m}")
    def _info(self, m):print(f"{self.B}ℹ{self.NC}  {m}")
    def _warn(self, m):print(f"{self.Y}⚠{self.NC}  {m}")
    def _err(self, m): print(f"{self.R}✖{self.NC}  {m}")
    def _hr(self, c='─'): print(c * 60)

    def _get_config(self):
        try:
            from core.config import get_config, reset_config
            reset_config()
            return get_config()
        except Exception:
            try:
                from config import get_config, reset_config
                reset_config()
                return get_config()
            except Exception:
                return None

    def _choose(self, prompt, options):
        print(f"\n{prompt}")
        for i, o in enumerate(options, 1):
            print(f"  {i}) {o}")
        while True:
            v = input("> ").strip()
            if v.lower() == 'q':
                return None
            try:
                idx = int(v) - 1
                if 0 <= idx < len(options):
                    return options[idx]
            except ValueError:
                pass
            print("  Введите номер из списка или q для выхода")

    def interactive(self):
        print()
        print(f"{self.W}╔══════════════════════════════════════════════════════╗{self.NC}")
        print(f"{self.W}║        WAL Recovery Tool  v1.0                      ║{self.NC}")
        print(f"{self.W}╚══════════════════════════════════════════════════════╝{self.NC}")
        print(f"{self.DIM}  CLI-режим. Введите номер действия или 'q' для выхода{self.NC}")

        while True:
            print(f"""
{self.W}Главное меню:{self.NC}
  1) Список таблиц в БД
  2) Сканировать удалённые записи
  3) Восстановить удалённые записи
  q) Выход""")

            choice = input("> ").strip().lower()
            if choice == 'q':
                self._info("Выход.")
                break
            elif choice == '1':
                self._cmd_list()
            elif choice == '2':
                self._cmd_scan_interactive()
            elif choice == '3':
                self._cmd_restore_interactive()

    def _pick_db(self):
        cfg = self._get_config()
        db_type = self._choose("Тип СУБД:", ["PostgreSQL", "MySQL"])
        if not db_type:
            return None, None, None

        if db_type == "PostgreSQL":
            dbs = cfg.pg.databases if cfg else ["shop_demo", "employees_demo"]
        else:
            dbs = cfg.mysql.databases if cfg else ["library_demo", "clinic_demo"]

        db = self._choose("База данных:", dbs)
        if not db:
            return None, None, None
        return db_type, db, cfg

    def _get_parser(self, db_type, db):
        if db_type == "PostgreSQL":
            return PostgresWALParser(database=db)
        else:
            return MySQLBinlogParser(database=db)

    def _cmd_list(self):
        db_type, db, cfg = self._pick_db()
        if not db:
            return
        try:
            p = self._get_parser(db_type, db)
            p.connect()
            tables = p.get_tables()
            p.disconnect()
            print(f"\n  {self.W}Таблицы в {db}:{self.NC}")
            for t in tables:
                print(f"    • {t}")
        except Exception as e:
            self._err(f"Ошибка: {e}")

    def _cmd_scan_interactive(self):
        db_type, db, cfg = self._pick_db()
        if not db:
            return
        try:
            p = self._get_parser(db_type, db)
            p.connect()
            tables = p.get_tables()
            p.disconnect()
        except Exception as e:
            self._err(f"Ошибка подключения: {e}")
            return

        table = self._choose("Таблица:", tables)
        if not table:
            return

        hours_map = {"6 часов": 6, "24 часа": 24, "48 часов": 48,
                     "7 дней": 168, "30 дней": 720}
        h_str = self._choose("Глубина поиска:", list(hours_map.keys()))
        if not h_str:
            return
        hours = hours_map[h_str]

        self._info(f"Подключаюсь к {db_type}:{db}...")
        self._info(f"Сканирую таблицу «{table}» за {hours} ч...")

        try:
            p = self._get_parser(db_type, db)
            p.connect()
            records = p.get_deleted_records(table, since_hours=hours)
            p.disconnect()
        except Exception as e:
            self._err(f"Ошибка сканирования: {e}")
            return

        if not records:
            self._warn("Удалённых записей не найдено.")
            return

        self._ok(f"Найдено {len(records)} записей:")
        self._hr()
        data_cols = [k for k in records[0] if not k.startswith('_')]
        header = '  '.join(f"{c:<20}" for c in data_cols[:4] + ['_deleted_at'])
        print(f"  {self.W}{header}{self.NC}")
        print(f"  {self.DIM}{'─'*60}{self.NC}")
        for r in records:
            row = '  '.join(str(r.get(c,''))[:20].ljust(20)
                            for c in data_cols[:4] + ['_deleted_at'])
            print(f"  {row}")
        self._hr()

    def _cmd_restore_interactive(self):
        db_type, db, cfg = self._pick_db()
        if not db:
            return
        try:
            p = self._get_parser(db_type, db)
            p.connect()
            tables = p.get_tables()
            p.disconnect()
        except Exception as e:
            self._err(f"Ошибка подключения: {e}")
            return

        table = self._choose("Таблица:", tables)
        if not table:
            return

        hours_map = {"6 часов": 6, "24 часа": 24, "7 дней": 168, "30 дней": 720}
        h_str = self._choose("Глубина поиска:", list(hours_map.keys()))
        if not h_str:
            return
        hours = hours_map[h_str]

        self._info(f"Подключаюсь к {db_type}:{db}...")
        self._info(f"Сканирую таблицу «{table}»...")

        try:
            p = self._get_parser(db_type, db)
            p.connect()
            records = p.get_deleted_records(table, since_hours=hours)
        except Exception as e:
            self._err(f"Ошибка: {e}")
            return

        if not records:
            self._warn("Нечего восстанавливать.")
            p.disconnect()
            return

        self._ok(f"Найдено {len(records)} записей. Восстановить все? [y/N] ", )
        if input().strip().lower() not in ('y','yes','д','да'):
            self._info("Отменено.")
            p.disconnect()
            return

        ok = fail = 0
        for rec in records:
            if p.restore_record(table, rec):
                ok += 1
                # Записываем в state.json
                try:
                    from core.recovery_state import record_restoration
                    pk = str(rec.get('id','?'))
                    record_restoration(db=db, db_type=db_type, table=table,
                        pk_value=pk, deleted_at=rec.get('_deleted_at',''),
                        source='CLI', data=rec)
                except Exception:
                    pass
            else:
                fail += 1

        p.disconnect()
        self._hr('═')
        self._ok(f"Восстановлено: {ok}")
        if fail:
            self._err(f"Ошибок: {fail}")
        self._hr('═')


# ===========================================================================
# Точка входа
# ===========================================================================
def _run_gui():
    app = QApplication(sys.argv)
    app.setApplicationName("WAL Recovery Tool")
    app.setApplicationVersion("1.0.0")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(DARK['bg0']))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(DARK['text']))
    palette.setColor(QPalette.ColorRole.Base,            QColor(DARK['bg1']))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(DARK['bg2']))
    palette.setColor(QPalette.ColorRole.ToolTipBase,     QColor(DARK['bg2']))
    palette.setColor(QPalette.ColorRole.ToolTipText,     QColor(DARK['text']))
    palette.setColor(QPalette.ColorRole.Text,            QColor(DARK['text']))
    palette.setColor(QPalette.ColorRole.Button,          QColor(DARK['bg2']))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(DARK['text']))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor(DARK['blue']))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def main():
    # Проверяем аргументы ДО инициализации Qt
    if '--cli' in sys.argv or '-c' in sys.argv:
        CLI().interactive()
        return

    # Субкоманды CLI без Qt
    if len(sys.argv) > 1 and sys.argv[1] in ('scan', 'restore', 'list', 'info'):
        cli = CLI()
        cmd = sys.argv[1]
        if cmd == 'list' and len(sys.argv) > 2:
            cli._cmd_list()
        elif cmd in ('scan', 'restore'):
            cli._cmd_scan_interactive() if cmd == 'scan'                 else cli._cmd_restore_interactive()
        else:
            CLI().interactive()
        return

    # GUI по умолчанию
    _run_gui()


if __name__ == "__main__":
    main()