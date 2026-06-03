# WAL Recovery Tool

Инструмент для анализа и восстановления удалённых записей из журналов транзакций PostgreSQL (WAL) и MySQL (binlog).

Проект разработан в рамках курсовой работы по дисциплине «Компьютерная экспертиза».

---

# Возможности проекта

* Анализ WAL PostgreSQL
* Анализ MySQL binlog
* Поиск удалённых записей
* GUI-интерфейс на PyQt6
* История сканирований и восстановлений
* Хранение состояния в JSON
* Работа через CLI и GUI
* Сборка `.deb` пакета
* Автоматическая установка на macOS

---

# Архитектура проекта

```text
wal-recovery/
│
├── src/
│   ├── main.py
│   ├── core/
│   │   ├── postgres_wal.py
│   │   ├── mysql_binlog.py
│   │   ├── config.py
│   │   └── recovery_state.py
│
├── install_macos.sh
├── uninstall_macos.sh
├── setup_databases.sh
├── build_deb.sh
└── README.md
```

---

# Системные требования

## Ubuntu / Debian

* Python 3.10+
* PostgreSQL
* MySQL / MariaDB
* pip
* git

## macOS

* Python 3
* Homebrew
* PostgreSQL
* MySQL

---

# Установка проекта через GitHub

## Ubuntu / Debian

### 1. Установка git

```bash
sudo apt update
sudo apt install git -y
```

### 2. Клонирование проекта

```bash
git clone https://github.com/Artem-aleks/wal-recovery.git
```

### 3. Переход в папку проекта

```bash
cd wal-recovery
```

### 4. Установка Python-зависимостей

```bash
pip install -r requirements.txt
```

### 5. Настройка тестовых БД

```bash
chmod +x setup_databases.sh
./setup_databases.sh
```

### 6. Запуск приложения

```bash
python3 src/main.py
```

---

# Установка на macOS

## 1. Установка Homebrew

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

## 2. Клонирование проекта

```bash
git clone https://github.com/Artem-aleks/wal-recovery.git
```

## 3. Переход в папку проекта

```bash
cd wal-recovery
```

## 4. Запуск автоматической установки

```bash
chmod +x install_macos.sh
./install_macos.sh
```

## 5. Запуск приложения

```bash
python3 src/main.py
```

---

# Настройка PostgreSQL

Необходимо включить логическую репликацию.

Открыть файл:

```bash
postgresql.conf
```

Изменить параметры:

```conf
wal_level = logical
max_replication_slots = 10
max_wal_senders = 10
```

После изменения параметров перезапустить PostgreSQL.

---

# Настройка MySQL

Включить бинарное логирование:

```conf
[mysqld]
server-id=1
log_bin=mysql-bin
binlog_format=ROW
```

После изменения параметров перезапустить MySQL.

---

# Конфигурация подключения

Подключение к БД можно настроить:

* через файл `databases.conf`
* через переменные окружения

Пример:

```conf
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=password

MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=password
```

---

# Использование

## Запуск GUI

```bash
python3 src/main.py
```

## Возможности GUI

* Подключение к PostgreSQL
* Подключение к MySQL
* Просмотр удалённых записей
* Анализ изменений
* Просмотр истории восстановлений
* Dashboard со статистикой

---

# Сборка .deb пакета

```bash
chmod +x build_deb.sh
./build_deb.sh
```

После сборки пакет появится в директории проекта.

---

# Удаление на macOS

```bash
chmod +x uninstall_macos.sh
./uninstall_macos.sh
```

---

# Хранение состояния

Программа сохраняет историю операций в:

```bash
~/.config/wal-recovery/state.json
```

Сохраняются:

* сканирования
* восстановления
* удаления
* изменения

---

# Используемые технологии

* Python 3
* PyQt6
* PostgreSQL
* MySQL
* wal2json
* pymysqlreplication
* JSON
* Linux / macOS

---

# Автор

Artem Aleks

---

# Лицензия

Проект создан в учебных целях.
