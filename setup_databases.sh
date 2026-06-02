#!/bin/bash
set -e

G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${G}✔${NC}  $1"; }
info() { echo -e "${B}ℹ${NC}  $1"; }
warn() { echo -e "${Y}⚠${NC}  $1"; }

if [ "$EUID" -ne 0 ]; then
    echo "Запускайте с sudo: sudo bash setup_databases.sh"
    exit 1
fi

echo ""
echo "   WAL Recovery Tool — Настройка баз данных          "
echo ""

# PostgreSQL
info "ШАГ 1: PostgreSQL..."

if ! command -v psql &>/dev/null; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql postgresql-contrib
    ok "PostgreSQL установлен"
else
    ok "PostgreSQL уже установлен: $(psql --version)"
fi

# wal2json
PG_VER=$(pg_config --version 2>/dev/null | grep -oP '\d+' | head -1 || echo "16")
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    "postgresql-${PG_VER}-wal2json" 2>/dev/null || \
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    postgresql-*-wal2json 2>/dev/null || \
    warn "wal2json не установлен — будет использован pg_waldump"

systemctl start postgresql 2>/dev/null || service postgresql start 2>/dev/null || true
sleep 2

# postgresql.conf
PG_CONF=$(find /etc/postgresql -name "postgresql.conf" 2>/dev/null | head -1)
if [ -f "$PG_CONF" ]; then
    cp "$PG_CONF" "${PG_CONF}.backup.$(date +%Y%m%d)" 2>/dev/null || true
    sed -i "s/^#*wal_level\s*=.*/wal_level = logical/" "$PG_CONF"
    grep -q "^wal_level" "$PG_CONF" || echo "wal_level = logical" >> "$PG_CONF"
    grep -q "^max_replication_slots" "$PG_CONF" || \
        echo -e "max_replication_slots = 10\nmax_wal_senders = 10\nwal_keep_size = 256" >> "$PG_CONF"
    systemctl restart postgresql 2>/dev/null || service postgresql restart 2>/dev/null || true
    sleep 3
    ok "wal_level = logical"
fi

# Пользователь и базы
sudo -u postgres psql << 'PGSQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'admin') THEN
        CREATE ROLE admin LOGIN PASSWORD 'oksat' SUPERUSER REPLICATION;
    ELSE
        ALTER ROLE admin PASSWORD 'oksat' SUPERUSER REPLICATION;
    END IF;
END $$;
DROP DATABASE IF EXISTS shop_demo;
CREATE DATABASE shop_demo OWNER admin;
DROP DATABASE IF EXISTS employees_demo;
CREATE DATABASE employees_demo OWNER admin;
PGSQL

# shop_demo — 15 строк в каждой таблице
sudo -u postgres psql -d shop_demo << 'PGSQL'
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
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY, customer_id INT REFERENCES customers(id),
    product_name VARCHAR(200), quantity INT, total_price NUMERIC(10,2),
    status VARCHAR(30) DEFAULT 'pending', created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO categories (name, description) VALUES
    ('Электроника',     'Гаджеты и устройства'),
    ('Одежда',          'Мужская и женская одежда'),
    ('Книги',           'Художественная и техническая литература'),
    ('Спорт',           'Спортивные товары и инвентарь'),
    ('Дом и сад',       'Товары для дома и дачи')
ON CONFLICT DO NOTHING;

INSERT INTO products (category_id, name, price, stock, sku) VALUES
    (1,'iPhone 15 Pro',129990,15,'APPL-IP15P'),
    (1,'Samsung Galaxy S24',89990,23,'SMSG-GS24'),
    (1,'MacBook Air M3',149990,8,'APPL-MBA-M3'),
    (1,'Sony WH-1000XM5',32990,41,'SONY-WH5'),
    (1,'iPad Air 5',74990,12,'APPL-IPAD5'),
    (2,'Куртка Nike зимняя',8990,55,'NIKE-JKT-W'),
    (2,'Кроссовки Adidas',6490,80,'ADID-SHOE'),
    (2,'Футболка Puma',1990,200,'PUMA-TEE'),
    (3,'Мастер и Маргарита',450,200,'BK-MIM'),
    (3,'Война и Мир',890,150,'BK-WAR'),
    (3,'Чистый код',2490,60,'BK-CC'),
    (4,'Гантели 10кг',3990,30,'SPT-DB10'),
    (4,'Коврик для йоги',1490,90,'SPT-YM'),
    (5,'Набор отвёрток',990,110,'HOME-SD'),
    (5,'Садовые ножницы',1290,75,'HOME-SH')
ON CONFLICT (sku) DO NOTHING;

INSERT INTO customers (name, email, phone, city) VALUES
    ('Иван Петров',    'ivan@mail.ru',     '+7-900-111-2233','Москва'),
    ('Мария Сидорова', 'maria@gmail.com',  '+7-911-222-3344','СПб'),
    ('Алексей Козлов', 'alex@ya.ru',       '+7-922-333-4455','Екб'),
    ('Елена Новикова', 'elena@mail.ru',    '+7-933-444-5566','Казань'),
    ('Дмитрий Волков', 'dmitry@gmail.com', '+7-944-555-6677','НСК'),
    ('Ольга Смирнова', 'olga@mail.ru',     '+7-955-666-7788','Ростов'),
    ('Сергей Зайцев',  'sergey@inbox.ru',  '+7-966-777-8899','Самара'),
    ('Анна Морозова',  'anna@bk.ru',       '+7-977-888-9900','Уфа'),
    ('Павел Кузнецов', 'pavel@mail.ru',    '+7-988-999-0011','Пермь'),
    ('Юлия Орлова',    'yulia@gmail.com',  '+7-999-000-1122','Воронеж'),
    ('Борис Лебедев',  'boris@ya.ru',      '+7-900-111-3344','Красноярск'),
    ('Надежда Попова', 'nadya@mail.ru',    '+7-911-222-4455','Тюмень'),
    ('Артём Соколов',  'artem@gmail.com',  '+7-922-333-5566','Челябинск'),
    ('Виктор Макаров', 'viktor@inbox.ru',  '+7-933-444-6677','Омск'),
    ('Светлана Фёдорова','svetlana@bk.ru', '+7-944-555-7788','Краснодар')
ON CONFLICT (email) DO NOTHING;

INSERT INTO orders (customer_id, product_name, quantity, total_price, status) VALUES
    (1,'iPhone 15 Pro',1,129990,'delivered'),
    (2,'Samsung Galaxy S24',1,89990,'shipped'),
    (3,'MacBook Air M3',1,149990,'pending'),
    (4,'Куртка Nike зимняя',2,17980,'delivered'),
    (5,'Кроссовки Adidas',1,6490,'shipped'),
    (6,'Мастер и Маргарита',3,1350,'delivered'),
    (7,'Sony WH-1000XM5',1,32990,'pending'),
    (8,'Чистый код',2,4980,'delivered'),
    (9,'Гантели 10кг',2,7980,'shipped'),
    (10,'iPad Air 5',1,74990,'delivered'),
    (11,'Коврик для йоги',1,1490,'shipped'),
    (12,'Набор отвёрток',3,2970,'pending'),
    (13,'Футболка Puma',5,9950,'delivered'),
    (14,'Война и Мир',1,890,'shipped'),
    (15,'Садовые ножницы',2,2580,'pending')
ON CONFLICT DO NOTHING;

ALTER TABLE categories REPLICA IDENTITY FULL;
ALTER TABLE products   REPLICA IDENTITY FULL;
ALTER TABLE customers  REPLICA IDENTITY FULL;
ALTER TABLE orders     REPLICA IDENTITY FULL;

DO $$
DECLARE tbl TEXT;
BEGIN
  FOR tbl IN SELECT table_name FROM information_schema.tables
             WHERE table_schema='public' AND table_type='BASE TABLE'
  LOOP
    BEGIN
      PERFORM pg_create_logical_replication_slot(
        'wr_shop_demo_' || tbl, 'wal2json')
      WHERE NOT EXISTS(
        SELECT 1 FROM pg_replication_slots
        WHERE slot_name='wr_shop_demo_' || tbl);
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
  END LOOP;
END $$;

DELETE FROM customers WHERE id IN (5,10,15);
DELETE FROM products  WHERE id IN (4,9,14);
PGSQL

# employees_demo — 15 строк
sudo -u postgres psql -d employees_demo << 'PGSQL'
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
    ('Разработка',       'Москва',           15000000),
    ('Маркетинг',        'Санкт-Петербург',   8000000),
    ('Финансы',          'Москва',            6000000),
    ('HR',               'Москва',            4000000),
    ('Продажи',          'Екатеринбург',      9000000)
ON CONFLICT DO NOTHING;

INSERT INTO employees (dept_id, full_name, position, salary, hire_date, email) VALUES
    (1,'Алексей Смирнов','Senior Developer',  180000,'2021-03-15','a.smirnov@corp.ru'),
    (1,'Мария Иванова',  'Junior Developer',   90000,'2023-06-01','m.ivanova@corp.ru'),
    (1,'Дмитрий Козлов', 'Team Lead',         220000,'2019-11-20','d.kozlov@corp.ru'),
    (1,'Игорь Беляев',   'DevOps Engineer',   160000,'2022-01-10','i.belyaev@corp.ru'),
    (1,'Татьяна Русова', 'QA Engineer',       120000,'2022-08-15','t.rusova@corp.ru'),
    (2,'Елена Новикова', 'Marketing Manager', 150000,'2020-08-10','e.novikova@corp.ru'),
    (2,'Сергей Волков',  'SMM Specialist',     95000,'2022-04-05','s.volkov@corp.ru'),
    (2,'Алина Громова',  'Content Manager',    85000,'2023-02-20','a.gromova@corp.ru'),
    (3,'Ольга Петрова',  'CFO',               280000,'2018-01-12','o.petrova@corp.ru'),
    (3,'Андрей Соколов', 'Accountant',        120000,'2021-09-30','a.sokolov@corp.ru'),
    (3,'Вера Тихонова',  'Financial Analyst', 130000,'2022-05-18','v.tihonova@corp.ru'),
    (4,'Наталья Морозова','HR Director',      160000,'2019-05-22','n.morozova@corp.ru'),
    (4,'Кирилл Захаров', 'HR Manager',        110000,'2022-11-01','k.zaharov@corp.ru'),
    (5,'Роман Степанов', 'Sales Manager',     170000,'2020-07-14','r.stepanov@corp.ru'),
    (5,'Юлия Орехова',   'Sales Specialist',   95000,'2023-03-01','y.orehova@corp.ru')
ON CONFLICT (email) DO NOTHING;

ALTER TABLE departments REPLICA IDENTITY FULL;
ALTER TABLE employees   REPLICA IDENTITY FULL;

DO $$
DECLARE tbl TEXT;
BEGIN
  FOR tbl IN SELECT table_name FROM information_schema.tables
             WHERE table_schema='public' AND table_type='BASE TABLE'
  LOOP
    BEGIN
      PERFORM pg_create_logical_replication_slot(
        'wr_employees_demo_' || tbl, 'wal2json')
      WHERE NOT EXISTS(
        SELECT 1 FROM pg_replication_slots
        WHERE slot_name='wr_employees_demo_' || tbl);
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
  END LOOP;
END $$;

DELETE FROM employees WHERE id IN (2,7,13);
PGSQL

ok "PostgreSQL: shop_demo и employees_demo настроены (15 строк)"


# MySQL
info "ШАГ 2: MySQL..."

if ! command -v mysql &>/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-server
    ok "MySQL установлен"
else
    ok "MySQL уже установлен: $(mysql --version)"
fi

systemctl start mysql 2>/dev/null || service mysql start 2>/dev/null || true
sleep 2

MY_CONF="/etc/mysql/mysql.conf.d/mysqld.cnf"
[ ! -f "$MY_CONF" ] && MY_CONF="/etc/mysql/my.cnf"
if [ -f "$MY_CONF" ] && ! grep -q "binlog_format" "$MY_CONF"; then
    cp "$MY_CONF" "${MY_CONF}.backup.$(date +%Y%m%d)" 2>/dev/null || true
    cat >> "$MY_CONF" << 'MYCNF'

# WAL Recovery Tool
server-id        = 1
log_bin          = /var/log/mysql/mysql-bin.log
binlog_format    = ROW
binlog_row_image = FULL
expire_logs_days = 30
MYCNF
    systemctl restart mysql 2>/dev/null || service mysql restart 2>/dev/null || true
    sleep 3
    ok "binlog настроен"
fi

mysql -u root << 'MYSQL_SQL'
CREATE USER IF NOT EXISTS 'admin'@'localhost' IDENTIFIED BY 'oksat';
GRANT ALL PRIVILEGES ON *.* TO 'admin'@'localhost' WITH GRANT OPTION;
GRANT REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'admin'@'localhost';
DROP DATABASE IF EXISTS library_demo;
CREATE DATABASE library_demo CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
DROP DATABASE IF EXISTS clinic_demo;
CREATE DATABASE clinic_demo CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
FLUSH PRIVILEGES;
MYSQL_SQL

mysql -u admin -poksat library_demo << 'MYSQL_SQL'
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
    ticket_no VARCHAR(20) UNIQUE NOT NULL, phone VARCHAR(20), email VARCHAR(100)
) ENGINE=InnoDB;

INSERT IGNORE INTO authors (name, birth_year, country) VALUES
    ('Михаил Булгаков', 1891,'Россия'),
    ('Лев Толстой',     1828,'Россия'),
    ('Стивен Кинг',     1947,'США'),
    ('Фёдор Достоевский',1821,'Россия'),
    ('Антон Чехов',     1860,'Россия');

INSERT IGNORE INTO books (author_id, title, isbn, year, copies_total, copies_avail) VALUES
    (1,'Мастер и Маргарита',   '978-001',1966,5,3),
    (1,'Белая гвардия',        '978-002',1925,3,2),
    (1,'Собачье сердце',       '978-003',1925,4,4),
    (2,'Война и мир',          '978-004',1869,4,3),
    (2,'Анна Каренина',        '978-005',1878,3,2),
    (3,'Сияние',               '978-006',1977,3,2),
    (3,'Оно',                  '978-007',1986,2,1),
    (3,'Зелёная миля',         '978-008',1996,4,3),
    (4,'Преступление и наказание','978-009',1866,5,4),
    (4,'Идиот',                '978-010',1869,3,2),
    (5,'Три сестры',           '978-011',1901,4,4),
    (5,'Вишнёвый сад',         '978-012',1904,3,3),
    (1,'Роковые яйца',         '978-013',1924,2,2),
    (2,'Детство',              '978-014',1852,3,3),
    (3,'Побег из Шоушенка',    '978-015',1982,4,4);

INSERT IGNORE INTO readers (full_name, ticket_no, phone, email) VALUES
    ('Борис Карпов',      'LIB-0001','+7-901-111-1111','b.karpov@mail.ru'),
    ('Вера Смирнова',     'LIB-0002','+7-902-222-2222','v.smirnova@mail.ru'),
    ('Геннадий Уваров',   'LIB-0003','+7-903-333-3333','g.uvarov@mail.ru'),
    ('Диана Харитонова',  'LIB-0004','+7-904-444-4444','d.har@mail.ru'),
    ('Евгений Лосев',     'LIB-0005','+7-905-555-5555','e.losev@mail.ru'),
    ('Жанна Орлова',      'LIB-0006','+7-906-666-6666','j.orlova@mail.ru'),
    ('Захар Миронов',     'LIB-0007','+7-907-777-7777','z.mironov@mail.ru'),
    ('Ирина Фомина',      'LIB-0008','+7-908-888-8888','i.fomina@mail.ru'),
    ('Константин Попов',  'LIB-0009','+7-909-999-9999','k.popov@mail.ru'),
    ('Лариса Никонова',   'LIB-0010','+7-910-000-0000','l.nikonova@mail.ru'),
    ('Михаил Зверев',     'LIB-0011','+7-911-111-2222','m.zverev@mail.ru'),
    ('Нина Белова',       'LIB-0012','+7-912-222-3333','n.belova@mail.ru'),
    ('Олег Титов',        'LIB-0013','+7-913-333-4444','o.titov@mail.ru'),
    ('Полина Агеева',     'LIB-0014','+7-914-444-5555','p.ageeva@mail.ru'),
    ('Роман Воронин',     'LIB-0015','+7-915-555-6666','r.voronin@mail.ru');

DELETE FROM readers WHERE id IN (2,4,9);
DELETE FROM books WHERE id IN (2,7);
MYSQL_SQL

ok "MySQL: library_demo настроена (15 строк)"

echo ""
ok "Все базы данных настроены!"
echo ""
echo "  PostgreSQL — подключение:"
echo "    psql -U admin -d shop_demo -h localhost     # пароль: oksat"
echo "    psql -U admin -d employees_demo -h localhost"
echo ""
echo "  MySQL — подключение:"
echo "    mysql -u admin -poksat library_demo"
echo ""
echo "  Запуск утилиты:"
echo "    wal-recovery          # GUI"
echo "    wal-recovery --cli    # CLI"
echo ""
