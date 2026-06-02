#!/bin/bash
set -e

PKG_NAME="wal-recovery"
PKG_VERSION="1.0.0"
PKG_ARCH="amd64"
PKG_DIR="${PKG_NAME}_${PKG_VERSION}_${PKG_ARCH}"

G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${G}✔${NC}  $1"; }
info() { echo -e "${B}ℹ${NC}  $1"; }
warn() { echo -e "${Y}⚠${NC}  $1"; }

echo ""
echo "WAL Recovery Tool — Сборка .deb пакета"
echo ""

# Проверяем что запущено из корня проекта
if [ ! -f "src/main.py" ]; then
    echo "Ошибка: запускайте из корня проекта!"
    echo "  cd wal-recovery"
    echo "  bash build_deb.sh"
    exit 1
fi

if ! command -v dpkg-deb &>/dev/null; then
    info "Устанавливаем dpkg-dev..."
    sudo apt-get install -y dpkg-dev
fi

# Создаём структуру директорий
info "[1/5] Структура директорий..."
rm -rf "$PKG_DIR"

mkdir -p "$PKG_DIR/DEBIAN"
mkdir -p "$PKG_DIR/opt/wal-recovery/src/core"
mkdir -p "$PKG_DIR/opt/wal-recovery/src/gui"
mkdir -p "$PKG_DIR/usr/local/bin"
mkdir -p "$PKG_DIR/usr/share/applications"
mkdir -p "$PKG_DIR/etc/wal-recovery"
ok "Директории созданы"

#  Копируем исходный код
info "[2/5] Копируем исходный код..."

cp src/main.py                        "$PKG_DIR/opt/wal-recovery/src/"
cp src/core/config.py                 "$PKG_DIR/opt/wal-recovery/src/core/"
cp src/core/postgres_wal.py           "$PKG_DIR/opt/wal-recovery/src/core/"
cp src/core/mysql_binlog.py           "$PKG_DIR/opt/wal-recovery/src/core/"
cp src/core/recovery_state.py         "$PKG_DIR/opt/wal-recovery/src/core/"
cp src/gui/main_window.py             "$PKG_DIR/opt/wal-recovery/src/gui/"

for DIR in src src/core src/gui; do
    if [ -f "$DIR/__init__.py" ]; then
        cp "$DIR/__init__.py" "$PKG_DIR/opt/wal-recovery/$DIR/"
    else
        touch "$PKG_DIR/opt/wal-recovery/$DIR/__init__.py"
    fi
done

cp setup_databases.sh "$PKG_DIR/opt/wal-recovery/"
chmod +x "$PKG_DIR/opt/wal-recovery/setup_databases.sh"

ok "Исходный код скопирован"

#Конфигурационный файл
info "[3/5] Конфигурационный файл..."

cat > "$PKG_DIR/etc/wal-recovery/databases.conf" << 'CONF'
# WAL Recovery Tool
# /etc/wal-recovery/databases.conf
#
# Чтобы добавить новую БД — допишите её имя в список databases
# Переменные окружения имеют приоритет:
#   WAL_PG_HOST, WAL_PG_PORT, WAL_PG_USER, WAL_PG_PASS
#   WAL_MY_HOST, WAL_MY_PORT, WAL_MY_USER, WAL_MY_PASS

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
CONF

ok "Конфиг добавлен"

#DEBIAN-метаданные
info "[4/5] DEBIAN-метаданные..."

DEBIAN_SRC="debian"
[ ! -d "$DEBIAN_SRC" ] && DEBIAN_SRC="."

cp "$DEBIAN_SRC/control"  "$PKG_DIR/DEBIAN/control"
cp "$DEBIAN_SRC/postinst" "$PKG_DIR/DEBIAN/postinst"
cp "$DEBIAN_SRC/prerm"    "$PKG_DIR/DEBIAN/prerm"

chmod 755 "$PKG_DIR/DEBIAN/postinst"
chmod 755 "$PKG_DIR/DEBIAN/prerm"

INSTALLED_SIZE=$(du -sk "$PKG_DIR/opt" "$PKG_DIR/etc" 2>/dev/null | \
    awk '{sum+=$1} END{print sum}')
echo "Installed-Size: ${INSTALLED_SIZE}" >> "$PKG_DIR/DEBIAN/control"

ok "Метаданные готовы"

# Сборка .deb
info "[5/5] Собираем .deb пакет..."

dpkg-deb --build --root-owner-group "$PKG_DIR"

DEB_FILE="${PKG_DIR}.deb"
DEB_SIZE=$(du -sh "$DEB_FILE" | cut -f1)

echo ""
ok "Пакет собран: ${DEB_FILE}  (${DEB_SIZE})"
echo ""
echo "  Удаление:"
echo "    sudo apt remove wal-recovery"
echo ""
