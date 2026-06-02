#!/bin/bash
set -e

# ─── Цвета ───────────────────────────────────────────────────────────────────
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'
B='\033[0;34m'; C='\033[0;36m'; W='\033[1;37m'; NC='\033[0m'

ok()   { echo -e "${G}✔${NC}  $1"; }
err()  { echo -e "${R}✘${NC}  $1"; exit 1; }
info() { echo -e "${B}ℹ${NC}  $1"; }
warn() { echo -e "${Y}⚠${NC}  $1"; }
step() {
    echo ""
    echo -e "${C}══════════════════════════════════════════════${NC}"
    echo -e "${C}  $1${NC}"
    echo -e "${C}══════════════════════════════════════════════${NC}"
}

# ─── Баннер ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${W}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${W}║     WAL Recovery Tool — Установщик macOS            ║${NC}"
echo -e "${W}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# Запрещаем запуск от root — Homebrew не работает под sudo
if [ "$EUID" -eq 0 ]; then
    err "Не запускайте этот скрипт через sudo!\nПросто: bash install_macos.sh"
fi

# Проверяем macOS
if [[ "$(uname)" != "Darwin" ]]; then
    err "Этот скрипт предназначен только для macOS."
fi

MACOS_VERSION=$(sw_vers -productVersion)
info "macOS версия: $MACOS_VERSION"

# Определяем архитектуру
ARCH=$(uname -m)
if [[ "$ARCH" == "arm64" ]]; then
    BREW_PREFIX="/opt/homebrew"
    info "Архитектура: Apple Silicon (M1/M2/M3/M4)"
else
    BREW_PREFIX="/usr/local"
    info "Архитектура: Intel x86_64"
fi

CURRENT_USER=$(whoami)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Заранее запрашиваем sudo-пароль (нужен только для /usr/local/bin)
info "Потребуются права администратора для установки команды в /usr/local/bin."
sudo -v
# Обновляем sudo-токен в фоне чтобы не протух
( while true; do sudo -v; sleep 50; done ) &
SUDO_KEEPALIVE_PID=$!
trap "kill $SUDO_KEEPALIVE_PID 2>/dev/null" EXIT

# =============================================================================
# ШАГ 1: Homebrew
# =============================================================================
step "ШАГ 1: Homebrew"

if command -v brew &>/dev/null; then
    ok "Homebrew уже установлен: $(brew --version | head -1)"
else
    info "Устанавливаем Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    ok "Homebrew установлен"
fi

eval "$($BREW_PREFIX/bin/brew shellenv)" 2>/dev/null || true
brew update --quiet 2>/dev/null || true

# =============================================================================
# ШАГ 2: Python 3.11
# =============================================================================
step "ШАГ 2: Python 3.11"

if brew list python@3.11 &>/dev/null; then
    ok "Python 3.11 уже установлен"
else
    info "Устанавливаем Python 3.11..."
    brew install python@3.11
    ok "Python 3.11 установлен"
fi

# Строго используем homebrew python
PYTHON="$BREW_PREFIX/opt/python@3.11/bin/python3.11"
[ ! -f "$PYTHON" ] && PYTHON="$BREW_PREFIX/bin/python3"
[ ! -f "$PYTHON" ] && PYTHON=$(command -v python3)
info "Python: $PYTHON ($(${PYTHON} --version))"

# =============================================================================
# ШАГ 3: Виртуальное окружение Python
# =============================================================================
step "ШАГ 3: Виртуальное окружение (venv)"

if [ -d "$VENV_DIR" ]; then
    warn "venv уже существует, пересоздаём..."
    rm -rf "$VENV_DIR"
fi

info "Создаём venv: $VENV_DIR"
"$PYTHON" -m venv "$VENV_DIR"
ok "venv создан"

VENV_PYTHON="$VENV_DIR/bin/python3"
VENV_PIP="$VENV_DIR/bin/pip"
"$VENV_PIP" install --upgrade pip --quiet

# =============================================================================
# ШАГ 4: Python-зависимости
# =============================================================================
step "ШАГ 4: Python-зависимости (в venv)"

for PKG in psycopg2-binary pymysql "mysql-replication==0.45.1" PyQt6; do
    info "Устанавливаем $PKG..."
    "$VENV_PIP" install "$PKG" --quiet
    ok "$PKG установлен"
done

# Патч библиотеки mysql-replication:
# SHOW MASTER STATUS устарела в MySQL 8.0.22+ и удалена в MySQL 9.x
# Заменяем на SHOW BINARY LOG STATUS прямо в установленном файле
BINLOGSTREAM="$VENV_DIR/lib/python3.11/site-packages/pymysqlreplication/binlogstream.py"
if [ ! -f "$BINLOGSTREAM" ]; then
    # Ищем для других версий Python
    BINLOGSTREAM=$(find "$VENV_DIR" -name "binlogstream.py" \
        -path "*/pymysqlreplication/*" 2>/dev/null | head -1)
fi
if [ -f "$BINLOGSTREAM" ]; then
    sed -i '' 's/SHOW MASTER STATUS/SHOW BINARY LOG STATUS/g' "$BINLOGSTREAM"
    ok "Патч mysql-replication: SHOW MASTER STATUS → SHOW BINARY LOG STATUS"
else
    warn "Файл binlogstream.py не найден, патч не применён"
fi

# =============================================================================
# ШАГ 5: PostgreSQL 17
# =============================================================================
step "ШАГ 5: PostgreSQL 17"

# Если запущен PostgreSQL 15 — останавливаем, он конфликтует по порту 5432
if brew services list 2>/dev/null | grep -q "postgresql@15.*started"; then
    warn "Обнаружен запущенный PostgreSQL 15 — останавливаем..."
    brew services stop postgresql@15
    sleep 2
    ok "PostgreSQL 15 остановлен"
fi

# Убираем старый путь pg15 из .zshrc
if grep -q "postgresql@15" ~/.zshrc 2>/dev/null; then
    sed -i '' '/postgresql@15/d' ~/.zshrc
    ok "Старый путь postgresql@15 удалён из ~/.zshrc"
fi

if brew list postgresql@17 &>/dev/null; then
    ok "PostgreSQL 17 уже установлен"
else
    info "Устанавливаем PostgreSQL 17..."
    brew install postgresql@17
    ok "PostgreSQL 17 установлен"
fi

export PATH="$BREW_PREFIX/opt/postgresql@17/bin:$PATH"
if ! grep -qF "postgresql@17/bin" ~/.zshrc 2>/dev/null; then
    echo 'export PATH="'$BREW_PREFIX'/opt/postgresql@17/bin:$PATH"' >> ~/.zshrc
    ok "postgresql@17/bin добавлен в ~/.zshrc"
fi

if ! brew services list | grep -q "postgresql@17.*started"; then
    info "Запускаем PostgreSQL 17..."
    brew services start postgresql@17
    sleep 4
    ok "PostgreSQL запущен"
else
    ok "PostgreSQL уже запущен"
fi

# =============================================================================
# ШАГ 6: wal2json — установка и копирование в PostgreSQL
# =============================================================================
step "ШАГ 6: wal2json"

if brew list wal2json &>/dev/null; then
    ok "wal2json уже установлен"
else
    info "Устанавливаем wal2json..."
    brew install wal2json
    ok "wal2json установлен"
fi

PG_LIB_DIR=$("$BREW_PREFIX/opt/postgresql@17/bin/pg_config" --pkglibdir 2>/dev/null || echo "")
[ -z "$PG_LIB_DIR" ] && PG_LIB_DIR="$BREW_PREFIX/opt/postgresql@17/lib/postgresql"
info "PostgreSQL ищет плагины в: $PG_LIB_DIR"

WAL2JSON_SO=$(find "$BREW_PREFIX/Cellar/wal2json" \
    -path "*postgresql@17*" \
    \( -name "wal2json*.so" -o -name "wal2json*.dylib" \) 2>/dev/null | head -1)

# Фолбэк — берём любой найденный dylib
[ -z "$WAL2JSON_SO" ] && WAL2JSON_SO=$(find "$BREW_PREFIX/Cellar/wal2json" \
    \( -name "wal2json*.so" -o -name "wal2json*.dylib" \) 2>/dev/null | head -1)

if [ -n "$WAL2JSON_SO" ] && [ -d "$PG_LIB_DIR" ]; then
    sudo cp "$WAL2JSON_SO" "$PG_LIB_DIR/" 2>/dev/null || true
    ok "wal2json скопирован в $PG_LIB_DIR/"
    brew services restart postgresql@17
    sleep 3
    ok "PostgreSQL перезапущен"
else
    warn "Не удалось автоматически скопировать wal2json.so"
fi

# =============================================================================
# ШАГ 7: Настройка WAL PostgreSQL
# =============================================================================
step "ШАГ 7: Настройка WAL-логирования"

PG_DATA="$BREW_PREFIX/var/postgresql@17"
PG_CONF="$PG_DATA/postgresql.conf"

if [ -f "$PG_CONF" ]; then
    cp "$PG_CONF" "${PG_CONF}.backup.$(date +%Y%m%d_%H%M%S)"
    NEEDS_RESTART=false

    if grep -qE "^wal_level\s*=\s*logical" "$PG_CONF"; then
        ok "wal_level = logical уже установлен"
    else
        sed -i '' '/^[#]*wal_level/d' "$PG_CONF"
        echo "wal_level = logical" >> "$PG_CONF"
        NEEDS_RESTART=true
        ok "wal_level = logical установлен"
    fi

    if ! grep -qE "^max_replication_slots" "$PG_CONF"; then
        echo "max_replication_slots = 10" >> "$PG_CONF"
        echo "max_wal_senders = 10" >> "$PG_CONF"
        echo "wal_keep_size = 256" >> "$PG_CONF"
        NEEDS_RESTART=true
        ok "Параметры репликации добавлены"
    fi

    if [ "$NEEDS_RESTART" = true ]; then
        info "Перезапускаем PostgreSQL..."
        brew services restart postgresql@17
        sleep 5
        ok "PostgreSQL перезапущен"
    fi
else
    warn "Файл $PG_CONF не найден"
fi

# =============================================================================
# ШАГ 8: Пользователь и демо-БД PostgreSQL
# =============================================================================
step "ШАГ 8: Пользователь и демо-БД PostgreSQL"

PSQL="$BREW_PREFIX/opt/postgresql@17/bin/psql"
PG_USER="admin"
PG_PASS="oksat"

info "Создаём роль $PG_USER..."
"$PSQL" -U "$CURRENT_USER" -d postgres << EOF 2>/dev/null || true
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$PG_USER') THEN
        CREATE ROLE $PG_USER LOGIN PASSWORD '$PG_PASS' SUPERUSER REPLICATION;
    ELSE
        ALTER ROLE $PG_USER PASSWORD '$PG_PASS' SUPERUSER REPLICATION;
    END IF;
END
\$\$;
EOF
ok "Роль $PG_USER готова (SUPERUSER + REPLICATION)"

for DB in shop_demo employees_demo; do
    EXISTS=$("$PSQL" -U "$CURRENT_USER" -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname='$DB'" 2>/dev/null || echo "")
    if [ "$EXISTS" != "1" ]; then
        "$PSQL" -U "$CURRENT_USER" -d postgres \
            -c "CREATE DATABASE $DB OWNER $PG_USER;" 2>/dev/null || true
        ok "БД $DB создана"
    else
        ok "БД $DB уже существует"
    fi
done

# Проверяем wal2json
WAL2JSON_CHECK=$("$PSQL" -U "$CURRENT_USER" -d postgres -tAc \
    "SELECT name FROM pg_available_extensions WHERE name = 'wal2json';" 2>/dev/null || echo "")
if [ "$WAL2JSON_CHECK" = "wal2json" ]; then
    ok "wal2json виден PostgreSQL"
else
    warn "wal2json не виден PostgreSQL — будет использован pg_waldump (фолбэк)"
fi

info "Наполняем shop_demo тестовыми данными..."
"$PSQL" -U "$PG_USER" -d shop_demo << 'SQLEOF' 2>/dev/null || true
CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL,
    description TEXT, created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY, category_id INT, name VARCHAR(200) NOT NULL,
    price NUMERIC(10,2), stock INT DEFAULT 0, sku VARCHAR(50) UNIQUE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY, name VARCHAR(150) NOT NULL,
    email VARCHAR(150) UNIQUE, phone VARCHAR(20), city VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);
INSERT INTO categories (name) VALUES ('Электроника'),('Одежда'),('Книги')
ON CONFLICT DO NOTHING;
INSERT INTO products (category_id, name, price, stock, sku) VALUES
    (1,'iPhone 15 Pro',129990,15,'APPL-IP15P'),
    (1,'Samsung Galaxy S24',89990,23,'SMSG-GS24'),
    (1,'MacBook Air M3',149990,8,'APPL-MBA-M3'),
    (1,'Sony WH-1000XM5',32990,41,'SONY-WH5'),
    (2,'Куртка Nike',8990,55,'NIKE-JKT'),
    (3,'Мастер и Маргарита',450,200,'BK-MIM'),
    (3,'Война и Мир',890,150,'BK-WAR')
ON CONFLICT (sku) DO NOTHING;
INSERT INTO customers (name, email, phone, city) VALUES
    ('Иван Петров','ivan@mail.ru','+7-900-111-2233','Москва'),
    ('Мария Сидорова','maria@gmail.com','+7-911-222-3344','СПб'),
    ('Алексей Козлов','alex@ya.ru','+7-922-333-4455','Екб'),
    ('Елена Новикова','elena@mail.ru','+7-933-444-5566','Казань'),
    ('Дмитрий Волков','dmitry@gmail.com','+7-944-555-6677','НСК')
ON CONFLICT (email) DO NOTHING;
DELETE FROM customers WHERE id IN (3,5);
DELETE FROM products  WHERE id IN (4,6);
SQLEOF
ok "shop_demo заполнена, тестовые удаления выполнены"

# Заполняем employees_demo
info "Наполняем employees_demo..."
"$PSQL" -U "$PG_USER" -d employees_demo << 'SQLEOF' 2>/dev/null || true
CREATE TABLE IF NOT EXISTS departments (
    id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL,
    location VARCHAR(100), budget NUMERIC(12,2),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS employees (
    id SERIAL PRIMARY KEY, dept_id INT REFERENCES departments(id),
    full_name VARCHAR(150) NOT NULL, position VARCHAR(100),
    salary NUMERIC(10,2), hire_date DATE, email VARCHAR(150) UNIQUE,
    created_at TIMESTAMP DEFAULT NOW()
);
INSERT INTO departments (name, location, budget) VALUES
    ('Разработка',    'Москва',       15000000),
    ('Маркетинг',     'Санкт-Петербург', 8000000),
    ('Финансы',       'Москва',       6000000),
    ('HR',            'Москва',       4000000)
ON CONFLICT DO NOTHING;
INSERT INTO employees (dept_id, full_name, position, salary, hire_date, email) VALUES
    (1,'Алексей Смирнов','Senior Developer',180000,'2021-03-15','a.smirnov@company.ru'),
    (1,'Мария Иванова','Junior Developer',90000,'2023-06-01','m.ivanova@company.ru'),
    (1,'Дмитрий Козлов','Team Lead',220000,'2019-11-20','d.kozlov@company.ru'),
    (2,'Елена Новикова','Marketing Manager',150000,'2020-08-10','e.novikova@company.ru'),
    (2,'Сергей Волков','SMM Specialist',95000,'2022-04-05','s.volkov@company.ru'),
    (3,'Ольга Петрова','CFO',280000,'2018-01-12','o.petrova@company.ru'),
    (3,'Андрей Соколов','Accountant',120000,'2021-09-30','a.sokolov@company.ru'),
    (4,'Наталья Морозова','HR Director',160000,'2019-05-22','n.morozova@company.ru')
ON CONFLICT (email) DO NOTHING;
DELETE FROM employees WHERE id IN (2, 6);
DELETE FROM departments WHERE id = 4;
SQLEOF
ok "employees_demo заполнена, тестовые удаления выполнены"

# Включаем REPLICA IDENTITY FULL — чтобы WAL писал все поля строки, а не только PK
info "Настраиваем REPLICA IDENTITY FULL для всех таблиц..."
"$PSQL" -U "$PG_USER" -d shop_demo << 'SQLEOF' 2>/dev/null || true
ALTER TABLE categories REPLICA IDENTITY FULL;
ALTER TABLE products   REPLICA IDENTITY FULL;
ALTER TABLE customers  REPLICA IDENTITY FULL;
SQLEOF

"$PSQL" -U "$PG_USER" -d employees_demo << 'SQLEOF' 2>/dev/null || true
ALTER TABLE departments REPLICA IDENTITY FULL;
ALTER TABLE employees   REPLICA IDENTITY FULL;
SQLEOF
ok "REPLICA IDENTITY FULL включён"

# Создаём слоты репликации для каждой таблицы заранее
# Формат имени совпадает с postgres_wal.py: wr_{db}_{table}
info "Создаём слоты репликации для всех таблиц..."

"$PSQL" -U "$CURRENT_USER" -d shop_demo << 'SQLEOF' 2>/dev/null || true
DO $$
DECLARE tbl TEXT;
BEGIN
  FOR tbl IN SELECT table_name FROM information_schema.tables
             WHERE table_schema='public' AND table_type='BASE TABLE'
  LOOP
    DECLARE slot TEXT := 'wr_shop_demo_' || tbl;
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = slot) THEN
        PERFORM pg_create_logical_replication_slot(slot, 'wal2json');
      END IF;
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
  END LOOP;
END $$;
SQLEOF

"$PSQL" -U "$CURRENT_USER" -d employees_demo << 'SQLEOF' 2>/dev/null || true
DO $$
DECLARE tbl TEXT;
BEGIN
  FOR tbl IN SELECT table_name FROM information_schema.tables
             WHERE table_schema='public' AND table_type='BASE TABLE'
  LOOP
    DECLARE slot TEXT := 'wr_employees_demo_' || tbl;
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = slot) THEN
        PERFORM pg_create_logical_replication_slot(slot, 'wal2json');
      END IF;
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
  END LOOP;
END $$;
SQLEOF

ok "Слоты репликации созданы — WAL начнёт записывать события немедленно"

# =============================================================================
# ШАГ 9: MySQL 8
# =============================================================================
step "ШАГ 9: MySQL 8"

# Определяем реальное имя формулы и путь к бинарникам MySQL
# Стратегия: сначала ищем бинарник mysql в Cellar, потом через which
MYSQL_FORMULA=""
MYSQL_BIN_DIR=""

# 1. Ищем через brew list — перебираем возможные имена формул
for CANDIDATE in mysql mysql@8.4 mysql@8.0 mysql@9.0 mysql@8; do
    if brew list "$CANDIDATE" &>/dev/null 2>&1; then
        MYSQL_FORMULA="$CANDIDATE"
        ok "MySQL найден как формула: $MYSQL_FORMULA"
        break
    fi
done

# 2. Ищем бинарник mysql напрямую в Cellar (надёжнее чем brew --prefix)
if [ -z "$MYSQL_BIN_DIR" ]; then
    MYSQL_BIN=$(find "$BREW_PREFIX/Cellar" -path "*/mysql*/bin/mysql" \
        ! -name "mysql_*" -type f 2>/dev/null | head -1)
    if [ -n "$MYSQL_BIN" ]; then
        MYSQL_BIN_DIR="$(dirname "$MYSQL_BIN")"
        ok "Найден mysql в Cellar: $MYSQL_BIN_DIR"
    fi
fi

# 3. Фолбэк — ищем через which
if [ -z "$MYSQL_BIN_DIR" ] && command -v mysql &>/dev/null; then
    MYSQL_BIN_DIR="$(dirname "$(which mysql)")"
    ok "Найден mysql через PATH: $MYSQL_BIN_DIR"
fi

# 4. Если вообще не нашли — устанавливаем
if [ -z "$MYSQL_BIN_DIR" ]; then
    info "MySQL не найден нигде, устанавливаем..."
    brew install mysql
    MYSQL_FORMULA="mysql"
    MYSQL_BIN=$(find "$BREW_PREFIX/Cellar" -path "*/mysql*/bin/mysql" \
        ! -name "mysql_*" -type f 2>/dev/null | head -1)
    MYSQL_BIN_DIR="$(dirname "$MYSQL_BIN")"
    ok "MySQL установлен: $MYSQL_BIN_DIR"
fi

info "MySQL bin: $MYSQL_BIN_DIR"

export PATH="$MYSQL_BIN_DIR:$PATH"
if ! grep -qF "$MYSQL_BIN_DIR" ~/.zshrc 2>/dev/null; then
    echo "export PATH=\"$MYSQL_BIN_DIR:\$PATH\"" >> ~/.zshrc
    ok "$MYSQL_BIN_DIR добавлен в ~/.zshrc"
fi

# Запускаем MySQL через mysql.server (идёт в комплекте с MySQL)
# Не используем brew services — он не всегда знает формулу mysql
MYSQL_SERVER="$MYSQL_BIN_DIR/mysql.server"

_mysql_running() {
    "$MYSQL_BIN_DIR/mysql" -u root -e "SELECT 1" &>/dev/null 2>&1
}

if _mysql_running; then
    ok "MySQL уже запущен"
elif [ -f "$MYSQL_SERVER" ]; then
    info "Запускаем MySQL через mysql.server..."
    "$MYSQL_SERVER" start 2>/dev/null || true
    sleep 5
    if _mysql_running; then
        ok "MySQL запущен"
    else
        warn "MySQL не ответил — попробуйте вручную: $MYSQL_SERVER start"
    fi
else
    warn "mysql.server не найден. Запустите MySQL вручную."
fi

# ШАГ 10: Настройка binlog MySQL
step "ШАГ 10: Настройка binlog MySQL"

MY_CONF=""
for CANDIDATE in \
    "$BREW_PREFIX/etc/my.cnf" \
    "$BREW_PREFIX/etc/mysql/my.cnf" \
    "$(dirname "$MYSQL_BIN_DIR")/etc/my.cnf"; do
    if [ -f "$CANDIDATE" ]; then
        MY_CONF="$CANDIDATE"
        break
    fi
done

if [ -z "$MY_CONF" ]; then
    MY_CONF="$BREW_PREFIX/etc/my.cnf"
    mkdir -p "$(dirname $MY_CONF)"
    echo "[mysqld]" > "$MY_CONF"
    info "Создан новый my.cnf: $MY_CONF"
fi

cp "$MY_CONF" "${MY_CONF}.backup.$(date +%Y%m%d_%H%M%S)"

if grep -q "binlog_format" "$MY_CONF" 2>/dev/null; then
    ok "binlog уже настроен"
else
    cat >> "$MY_CONF" << 'EOF'

# === WAL Recovery Tool ===
server-id        = 1
log_bin          = /tmp/mysql-bin.log
binlog_format    = ROW
binlog_row_image = FULL
expire_logs_days = 30
EOF
    # Перезапускаем MySQL через mysql.server
    if [ -f "$MYSQL_SERVER" ]; then
        "$MYSQL_SERVER" restart 2>/dev/null || { "$MYSQL_SERVER" stop 2>/dev/null; sleep 2; "$MYSQL_SERVER" start 2>/dev/null; }
    fi
    sleep 5
    ok "binlog настроен: ROW format"
fi

# ШАГ 11: Пользователь и демо-БД MySQL
step "ШАГ 11: Пользователь и демо-БД MySQL"

MYSQL_CMD="$MYSQL_BIN_DIR/mysql"

MYSQL_INIT_SQL=$(mktemp /tmp/wal_mysql_init_XXXXXX.sql)
cat > "$MYSQL_INIT_SQL" << 'MYSQL_SQL'
CREATE USER IF NOT EXISTS 'admin'@'localhost' IDENTIFIED BY 'oksat';
GRANT ALL PRIVILEGES ON *.* TO 'admin'@'localhost' WITH GRANT OPTION;
GRANT REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'admin'@'localhost';
DROP DATABASE IF EXISTS library_demo;
CREATE DATABASE library_demo CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
DROP DATABASE IF EXISTS clinic_demo;
CREATE DATABASE clinic_demo CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
FLUSH PRIVILEGES;
MYSQL_SQL

"$MYSQL_CMD" -u root < "$MYSQL_INIT_SQL" 2>/dev/null \
    || "$MYSQL_CMD" < "$MYSQL_INIT_SQL" 2>/dev/null \
    || warn "Не удалось подключиться к MySQL как root"
rm -f "$MYSQL_INIT_SQL"
ok "Пользователь и базы MySQL созданы"

MYSQL_DATA_SQL=$(mktemp /tmp/wal_mysql_data_XXXXXX.sql)
cat > "$MYSQL_DATA_SQL" << 'MYSQL_SQL'
CREATE TABLE IF NOT EXISTS authors (
    id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(150) NOT NULL,
    birth_year INT, country VARCHAR(100)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS books (
    id INT AUTO_INCREMENT PRIMARY KEY, author_id INT,
    title VARCHAR(200) NOT NULL, isbn VARCHAR(20) UNIQUE,
    year INT, copies_total INT DEFAULT 1, copies_avail INT DEFAULT 1,
    FOREIGN KEY (author_id) REFERENCES authors(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS readers (
    id INT AUTO_INCREMENT PRIMARY KEY, full_name VARCHAR(150) NOT NULL,
    ticket_no VARCHAR(20) UNIQUE NOT NULL, phone VARCHAR(20)
) ENGINE=InnoDB;
INSERT IGNORE INTO authors (name, birth_year, country) VALUES
    ('Михаил Булгаков',1891,'Россия'),
    ('Лев Толстой',1828,'Россия'),
    ('Стивен Кинг',1947,'США');
INSERT IGNORE INTO books (author_id, title, isbn, year, copies_total, copies_avail) VALUES
    (1,'Мастер и Маргарита','978-001',1966,5,3),
    (1,'Белая гвардия','978-002',1925,3,2),
    (2,'Война и мир','978-003',1869,4,4),
    (3,'Сияние','978-004',1977,3,2);
INSERT IGNORE INTO readers (full_name, ticket_no, phone) VALUES
    ('Борис Карпов','LIB-0001','+7-901-111-1111'),
    ('Вера Смирнова','LIB-0002','+7-902-222-2222'),
    ('Геннадий Уваров','LIB-0003','+7-903-333-3333'),
    ('Диана Харитонова','LIB-0004','+7-904-444-4444');
DELETE FROM readers WHERE id IN (2,4);
DELETE FROM books WHERE id IN (2);
MYSQL_SQL

"$MYSQL_CMD" -u admin -poksat library_demo < "$MYSQL_DATA_SQL" 2>/dev/null || true
rm -f "$MYSQL_DATA_SQL"
ok "library_demo заполнена, тестовые удаления выполнены"

# ШАГ 12: Wrapper-команда wal-recovery
step "ШАГ 12: Установка команды wal-recovery"

# Создаём wrapper
sudo tee /usr/local/bin/wal-recovery > /dev/null << WRAPPER_EOF
#!/bin/bash
export PATH="$BREW_PREFIX/bin:$BREW_PREFIX/opt/postgresql@17/bin:$MYSQL_BIN_DIR:\$PATH"
exec "$VENV_DIR/bin/python3" "$SCRIPT_DIR/src/main.py" "\$@"
WRAPPER_EOF

sudo chmod +x /usr/local/bin/wal-recovery
ok "Команда /usr/local/bin/wal-recovery создана (использует venv)"

# Конфигурационный файл
step "ШАГ 13: Конфигурация"

CONF_DIR="$HOME/.config/wal-recovery"
mkdir -p "$CONF_DIR"
cat > "$CONF_DIR/databases.conf" << 'CONF_EOF'
# WAL Recovery Tool — конфигурация (macOS)

[postgresql]
host     = localhost
port     = 5432
user     = admin
password = oksat
databases = shop_demo, employees_demo

[mysql]
host     = localhost
port     = 3306
user     = admin
password = oksat
databases = library_demo, clinic_demo
CONF_EOF
ok "Конфиг записан: $CONF_DIR/databases.conf"

# Итог
# =============================================================================
echo ""
echo -e "${G}  ✅  WAL Recovery Tool успешно установлен на macOS!  ${NC}"
echo ""
echo "  Запуск GUI:       wal-recovery"
echo "  Запуск CLI:       wal-recovery --cli"
echo "  Сканирование:     wal-recovery scan pg:shop_demo customers"
echo "  Восстановление:   wal-recovery restore my:library_demo readers"
echo "  Удаление:         bash uninstall_macos.sh"
echo ""
