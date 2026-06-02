"""
wal_recovery/core/recovery_state.py

Единое хранилище всех операций — восстановления, удаления, сканирования.
Файл: ~/.config/wal-recovery/state.json
"""

import json
import os
import uuid
from datetime import datetime
from typing import Optional

STATE_FILE = os.path.expanduser("~/.config/wal-recovery/state.json")


def _load() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"restorations": [], "deletions": [], "scans": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Совместимость со старым форматом
        data.setdefault("deletions", [])
        data.setdefault("scans", [])
        return data
    except Exception:
        return {"restorations": [], "deletions": [], "scans": []}


def _save(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[state] Не удалось сохранить: {e}")


# ------------------------------------------------------------------
# Восстановления
# ------------------------------------------------------------------
def record_restoration(db, db_type, table, pk_value,
                        deleted_at, source, data=None):
    state = _load()
    entry = {
        "id":         str(uuid.uuid4()),
        "ts":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action":     "RESTORED",
        "db":         db,
        "db_type":    db_type,
        "table":      table,
        "pk_value":   str(pk_value),
        "deleted_at": str(deleted_at),
        "source":     source,
        "data":       {k: str(v) for k, v in (data or {}).items()
                       if not k.startswith("_")},
    }
    state["restorations"].append(entry)
    _save(state)
    return entry


def get_restorations(db=None, table=None):
    state = _load()
    items = state.get("restorations", [])
    if db:
        items = [r for r in items if r.get("db") == db]
    if table:
        items = [r for r in items if r.get("table") == table]
    return items


def is_restored(db, table, pk_value):
    for r in get_restorations(db, table):
        if r.get("pk_value") == str(pk_value):
            return r.get("ts")
    return None


# ------------------------------------------------------------------
# Удаления (фиксируем обнаруженные удаления)
# ------------------------------------------------------------------
def record_deletion(db, table, count, deleted_at, source="scan"):
    """Записывает факт обнаруженного удаления."""
    state = _load()
    entry = {
        "id":         str(uuid.uuid4()),
        "ts":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action":     "DETECTED",
        "db":         db,
        "table":      table,
        "count":      count,
        "deleted_at": str(deleted_at),
        "source":     source,
    }
    state["deletions"].append(entry)
    # Храним только последние 500 записей об удалениях
    if len(state["deletions"]) > 500:
        state["deletions"] = state["deletions"][-500:]
    _save(state)
    return entry


def get_deletions(db=None, table=None, limit=50):
    state = _load()
    items = state.get("deletions", [])
    if db:
        items = [r for r in items if r.get("db") == db]
    if table:
        items = [r for r in items if r.get("table") == table]
    return items[-limit:]


# ------------------------------------------------------------------
# Сканирования
# ------------------------------------------------------------------
def record_scan(db, table, found_count, source="GUI"):
    state = _load()
    entry = {
        "id":    str(uuid.uuid4()),
        "ts":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": "SCANNED",
        "db":    db,
        "table": table,
        "found": found_count,
        "source": source,
    }
    state["scans"].append(entry)
    if len(state["scans"]) > 200:
        state["scans"] = state["scans"][-200:]
    _save(state)
    return entry


def get_scans(limit=20):
    return _load().get("scans", [])[-limit:]


# ------------------------------------------------------------------
# Все события для истории (объединённая лента)
# ------------------------------------------------------------------
def clear_all():
    _save({"restorations": [], "deletions": [], "scans": []})


def get_state_file():
    return STATE_FILE


def record_change(db: str, table: str, action: str,
                  data: dict = None, source: str = "WAL"):
    """
    Записывает реальное изменение в БД: INSERT, UPDATE, DELETE.
    action: 'INSERT' | 'UPDATE' | 'DELETE'
    """
    state = _load()
    state.setdefault("changes", [])
    entry = {
        "id":      str(uuid.uuid4()),
        "ts":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action":  action,
        "db":      db,
        "table":   table,
        "source":  source,
        "data":    {k: str(v) for k, v in (data or {}).items()
                    if not k.startswith("_")},
    }
    state["changes"].append(entry)
    if len(state["changes"]) > 1000:
        state["changes"] = state["changes"][-1000:]
    _save(state)
    return entry


def get_changes(db=None, table=None, action=None, limit=200):
    """Возвращает реальные изменения в БД."""
    state = _load()
    items = state.get("changes", [])
    if db:
        items = [r for r in items if r.get("db") == db]
    if table:
        items = [r for r in items if r.get("table") == table]
    if action:
        items = [r for r in items if r.get("action") == action]
    return items[-limit:]


def get_all_events(limit=100):
    """Возвращает все события в хронологическом порядке."""
    state = _load()
    events = (
        state.get("restorations", []) +
        state.get("deletions",    []) +
        state.get("scans",        []) +
        state.get("changes",      [])
    )
    events.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return events[:limit]

