#!/usr/bin/env bash
# Пересоздаёт виртуальное окружение с нуля.
# Запускать ИЗ КОРНЯ проекта ПОСЛЕ того, как папка получила финальное имя
# (venv зашивает абсолютный путь, поэтому переименовывать папку после
#  создания venv нельзя — он сломается).
#
# Рабочее окружение не теряется при неудаче. Раньше `rm -rf venv` выполнялся
# ПЕРВЫМ: если после этого не находился подходящий python3 или падала
# установка, пользователь оставался вообще без окружения — рабочее состояние
# менялось на сломанное, без пути назад (проверено копией скрипта: старый
# marker удалён, выход 127). Теперь сначала идут проверки, потом старое
# окружение ОТКЛАДЫВАЕТСЯ в сторону, и при любой ошибке возвращается на место.
set -euo pipefail

cd "$(dirname "$0")"

VENV="venv"
BACKUP=".venv-previous-$$"
RESTORE_ON_FAIL=0

restore_backup() {
    if [ "$RESTORE_ON_FAIL" = "1" ] && [ -d "$BACKUP" ]; then
        echo "==> Восстанавливаю прежнее окружение" >&2
        rm -rf "$VENV"
        mv "$BACKUP" "$VENV"
    fi
    rm -rf "$BACKUP"
}
trap restore_backup EXIT

# --- preflight: всё, что может помешать, проверяем ДО разрушительных действий -
echo "==> Проверка окружения"
if ! command -v python3 >/dev/null 2>&1; then
    echo "ОШИБКА: python3 не найден в PATH. Существующий venv не тронут." >&2
    exit 1
fi
echo "    python3: $(command -v python3)"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "ОШИБКА: нужен Python >= 3.10 ($(python3 -V)). Существующий venv не тронут." >&2
    exit 1
fi
if ! python3 -c 'import venv' >/dev/null 2>&1; then
    echo "ОШИБКА: модуль venv недоступен (в Debian/Ubuntu: apt install python3-venv)." >&2
    exit 1
fi
if [ ! -f requirements-dev.txt ]; then
    echo "ОШИБКА: requirements-dev.txt не найден — запускайте скрипт из корня проекта." >&2
    exit 1
fi
# Каталог считается НАШИМ venv, только если в нём есть pyvenv.cfg: иначе можно
# снести чужие данные, случайно оказавшиеся по этому пути.
if [ -e "$VENV" ] && { [ ! -d "$VENV" ] || [ ! -f "$VENV/pyvenv.cfg" ]; }; then
    echo "ОШИБКА: '$VENV' существует, но не похож на venv (нет pyvenv.cfg)." >&2
    echo "        Удалите или переименуйте его вручную, если уверены." >&2
    exit 1
fi

if [ -d "$VENV" ]; then
    echo "==> Откладываю прежний venv (вернётся при ошибке)"
    rm -rf "$BACKUP"
    mv "$VENV" "$BACKUP"
    RESTORE_ON_FAIL=1
fi

echo "==> Создаю новый venv"
python3 -m venv "$VENV"

echo "==> Обновляю pip и ставлю зависимости"
"./$VENV/bin/pip" install --upgrade pip
"./$VENV/bin/pip" install -r requirements-dev.txt

echo "==> Проверка импортов"
"./$VENV/bin/python" -c "from whispersync.gui.main_window import main; print('OK: whispersync импортируется')"

# Новое окружение работает — прежнее больше не нужно.
RESTORE_ON_FAIL=0

echo "==> Тесты"
"./$VENV/bin/python" -m pytest -q

echo ""
echo "Готово. Запуск приложения:"
echo "  source venv/bin/activate && python3 main.py"
