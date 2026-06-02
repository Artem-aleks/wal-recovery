import os
import configparser
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PGConfig:
    host:      str       = "localhost"
    port:      int       = 5432
    user:      str       = "admin"
    password:  str       = "oksat"
    databases: list[str] = field(default_factory=lambda: ["shop_demo", "employees_demo"])


@dataclass
class MySQLConfig:
    host:      str       = "localhost"
    port:      int       = 3306
    user:      str       = "admin"
    password:  str       = "oksat"
    databases: list[str] = field(default_factory=lambda: ["library_demo", "clinic_demo"])


@dataclass
class AppConfig:
    pg:    PGConfig    = field(default_factory=PGConfig)
    mysql: MySQLConfig = field(default_factory=MySQLConfig)
    # Путь к конфигурационному файлу (может быть переопределён флагом --config)
    config_file: str   = "/etc/wal-recovery/databases.conf"



def load_config(config_path: str = None) -> AppConfig:
    """
    Загружает конфигурацию.
    config_path — путь к файлу; если None, ищем в стандартных местах.
    """
    cfg = AppConfig()

    # Список мест поиска конфига
    search_paths = [
        config_path,
        "/etc/wal-recovery/databases.conf",
        os.path.expanduser("~/.config/wal-recovery/databases.conf"),
        os.path.join(os.path.dirname(__file__), "../../databases.conf"),
    ]

    conf_file = None
    for path in search_paths:
        if path and Path(path).exists():
            conf_file = path
            break

    # Читаем файл если нашли
    if conf_file:
        parser = configparser.ConfigParser()
        parser.read(conf_file, encoding='utf-8')

        if 'postgresql' in parser:
            s = parser['postgresql']
            cfg.pg.host     = s.get('host',     cfg.pg.host)
            cfg.pg.port     = int(s.get('port', cfg.pg.port))
            cfg.pg.user     = s.get('user',     cfg.pg.user)
            cfg.pg.password = s.get('password', cfg.pg.password)
            raw_dbs = s.get('databases', '')
            if raw_dbs:
                cfg.pg.databases = [d.strip() for d in raw_dbs.split(',') if d.strip()]

        if 'mysql' in parser:
            s = parser['mysql']
            cfg.mysql.host     = s.get('host',     cfg.mysql.host)
            cfg.mysql.port     = int(s.get('port', cfg.mysql.port))
            cfg.mysql.user     = s.get('user',     cfg.mysql.user)
            cfg.mysql.password = s.get('password', cfg.mysql.password)
            raw_dbs = s.get('databases', '')
            if raw_dbs:
                cfg.mysql.databases = [d.strip() for d in raw_dbs.split(',') if d.strip()]

        cfg.config_file = conf_file

    # Переменные окружения перезаписывают всё (удобно для docker/CI)
    cfg.pg.host     = os.environ.get('WAL_PG_HOST',     cfg.pg.host)
    cfg.pg.port     = int(os.environ.get('WAL_PG_PORT', cfg.pg.port))
    cfg.pg.user     = os.environ.get('WAL_PG_USER',     cfg.pg.user)
    cfg.pg.password = os.environ.get('WAL_PG_PASS',     cfg.pg.password)

    cfg.mysql.host     = os.environ.get('WAL_MY_HOST',     cfg.mysql.host)
    cfg.mysql.port     = int(os.environ.get('WAL_MY_PORT', cfg.mysql.port))
    cfg.mysql.user     = os.environ.get('WAL_MY_USER',     cfg.mysql.user)
    cfg.mysql.password = os.environ.get('WAL_MY_PASS',     cfg.mysql.password)

    return cfg

_config: AppConfig | None = None

def get_config(config_path: str = None) -> AppConfig:
    """Возвращает синглтон конфигурации."""
    global _config
    if _config is None or config_path:
        _config = load_config(config_path)
    return _config

def reset_config():
    """Сбросить кэш (нужно в тестах)."""
    global _config
    _config = None

if __name__ == "__main__":
    c = get_config()
    print(f"PostgreSQL: {c.pg.host}:{c.pg.port}  user={c.pg.user}")
    print(f"  databases: {c.pg.databases}")
    print(f"MySQL:      {c.mysql.host}:{c.mysql.port}  user={c.mysql.user}")
    print(f"  databases: {c.mysql.databases}")
    print(f"Config file: {c.config_file}")
