#!/bin/bash

R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'
B='\033[0;34m'; C='\033[0;36m'; W='\033[1;37m'; NC='\033[0m'

ok()   { echo -e "${G}✔${NC}  $1"; }
info() { echo -e "${B}ℹ${NC}  $1"; }
warn() { echo -e "${Y}⚠${NC}  $1"; }
step() {
    echo ""
    echo -e "${C}══════════════════════════════════════════════${NC}"
    echo -e "${C}  $1${NC}"
    echo -e "${C}══════════════════════════════════════════════${NC}"
}

# Запрещаем запуск от root — Homebrew не работает под sudo
if [ "$EUID" -eq 0 ]; then
    echo "Не запускайте этот скрипт через sudo!"
    echo "Просто: bash uninstall_macos.sh"
    exit 1
fi

ARCH=$(uname -m)
BREW_PREFIX=$([[ "$ARCH" == "arm64" ]] && echo "/opt/homebrew" || echo "/usr/local")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
CURRENT_USER=$(whoami)

echo ""
echo -e "${W}    WAL Recovery Tool — Удаление с macOS            ${NC}"
echo ""
warn "Этот скрипт удалит:"
echo "   • Команду /usr/local/bin/wal-recovery"
echo "   • Виртуальное окружение .venv"
echo "   • Конфигурацию ~/.config/wal-recovery/"
echo "   • Демо-базы PostgreSQL (shop_demo, employees_demo)"
echo "   • Демо-базы MySQL (library_demo, clinic_demo)"
echo "   • Слоты репликации PostgreSQL"
echo ""
echo "   Homebrew, PostgreSQL, MySQL и Python НЕ удаляются."
echo ""
read -r -p "Продолжить удаление? [y/N] " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Отменено."
    exit 0
fi

# Запрашиваем sudo заранее (нужен только для /usr/local/bin)
info "Потребуются права администратора для удаления /usr/local/bin/wal-recovery"
sudo -v
( while true; do sudo -v; sleep 50; done ) &
SUDO_KEEPALIVE_PID=$!
trap "kill $SUDO_KEEPALIVE_PID 2>/dev/null" EXIT


# ШАГ 1: Команда wal-recovery
step "ШАГ 1: Удаление команды wal-recovery"

if [ -f /usr/local/bin/wal-recovery ]; then
    sudo rm -f /usr/local/bin/wal-recovery
    ok "Удалено: /usr/local/bin/wal-recovery"
else
    info "Файл /usr/local/bin/wal-recovery не найден"
fi


# ШАГ 2: Виртуальное окружение
step "ШАГ 2: Удаление виртуального окружения"

if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
    ok "Удалено: $VENV_DIR"
else
    info "venv не найден: $VENV_DIR"
fi


# ШАГ 3: Конфигурация
step "ШАГ 3: Удаление конфигурации"

CONF_DIR="$HOME/.config/wal-recovery"
if [ -d "$CONF_DIR" ]; then
    rm -rf "$CONF_DIR"
    ok "Удалено: $CONF_DIR"
else
    info "Конфиг не найден: $CONF_DIR"
fi


# ШАГ 4: Слоты репликации PostgreSQL
step "ШАГ 4: Удаление слотов репликации PostgreSQL"

PSQL="$BREW_PREFIX/opt/postgresql@17/bin/psql"

if [ -f "$PSQL" ] && brew services list | grep -q "postgresql@17.*started"; then
    SLOTS=$("$PSQL" -U "$CURRENT_USER" -d postgres -tAc \
        "SELECT slot_name FROM pg_replication_slots;" 2>/dev/null || echo "")
    if [ -n "$SLOTS" ]; then
        while IFS= read -r SLOT; do
            [ -z "$SLOT" ] && continue
            "$PSQL" -U "$CURRENT_USER" -d postgres \
                -c "SELECT pg_drop_replication_slot('$SLOT');" 2>/dev/null || true
            ok "Слот удалён: $SLOT"
        done <<< "$SLOTS"
    else
        info "Слоты репликации не найдены"
    fi
else
    info "PostgreSQL недоступен, пропускаем"
fi


# ШАГ 5: Демо-базы PostgreSQL
step "ШАГ 5: Удаление демо-БД PostgreSQL"

if [ -f "$PSQL" ] && brew services list | grep -q "postgresql@17.*started"; then
    for DB in shop_demo employees_demo; do
        "$PSQL" -U "$CURRENT_USER" -d postgres \
            -c "DROP DATABASE IF EXISTS $DB;" 2>/dev/null \
            && ok "БД $DB удалена" || warn "Не удалось удалить $DB"
    done
    "$PSQL" -U "$CURRENT_USER" -d postgres \
        -c "DROP ROLE IF EXISTS admin;" 2>/dev/null \
        && ok "Роль wal_recovery удалена" || true
else
    warn "PostgreSQL недоступен, базы не удаляем"
fi

# ШАГ 6: Демо-базы MySQL
step "ШАГ 6: Удаление демо-БД MySQL"

MYSQL_FORMULA=""
for CANDIDATE in mysql mysql@8.4 mysql@8.0 mysql@9.0; do
    if brew list "$CANDIDATE" &>/dev/null 2>&1; then
        MYSQL_FORMULA="$CANDIDATE"
        break
    fi
done

MYSQL_BIN_DIR="$BREW_PREFIX/opt/${MYSQL_FORMULA:-mysql}/bin"
[ -n "$MYSQL_FORMULA" ] && MYSQL_BIN_DIR="$(brew --prefix "$MYSQL_FORMULA" 2>/dev/null)/bin"
MYSQL_CMD="$MYSQL_BIN_DIR/mysql"

if [ -f "$MYSQL_CMD" ] && [ -n "$MYSQL_FORMULA" ] && brew services list | grep -q "$MYSQL_FORMULA.*started"; then
    SQL_TMP=$(mktemp /tmp/wal_uninstall_XXXXXX.sql)
    cat > "$SQL_TMP" << 'MYSQL_SQL'
DROP DATABASE IF EXISTS library_demo;
DROP DATABASE IF EXISTS clinic_demo;
DROP USER IF EXISTS 'admin'@'localhost';
FLUSH PRIVILEGES;
MYSQL_SQL
    "$MYSQL_CMD" -u root < "$SQL_TMP" 2>/dev/null \
        || "$MYSQL_CMD" < "$SQL_TMP" 2>/dev/null \
        || warn "Не удалось подключиться к MySQL"
    rm -f "$SQL_TMP"
    ok "БД MySQL и пользователь удалены"
else
    warn "MySQL недоступен, базы не удаляем"
fi


# ШАГ 7: Резервные копии конфигов
step "ШАГ 7: Очистка резервных копий конфигов"

PG_BACKUPS=$(find "$BREW_PREFIX/var/postgresql@17" \
    -name "postgresql.conf.backup.*" 2>/dev/null || true)
if [ -n "$PG_BACKUPS" ]; then
    echo "$PG_BACKUPS" | xargs rm -f
    ok "Резервные копии postgresql.conf удалены"
else
    info "Резервные копии postgresql.conf не найдены"
fi

MY_BACKUPS=$(find "$BREW_PREFIX/etc" -name "my.cnf.backup.*" 2>/dev/null || true)
if [ -n "$MY_BACKUPS" ]; then
    echo "$MY_BACKUPS" | xargs rm -f
    ok "Резервные копии my.cnf удалены"
else
    info "Резервные копии my.cnf не найдены"
fi

# =============================================================================
# Итог
# =============================================================================
echo ""
echo -e "${G}  ✅  WAL Recovery Tool полностью удалён с macOS      ${NC}"
echo ""
