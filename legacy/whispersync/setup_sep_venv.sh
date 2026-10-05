#!/usr/bin/env bash
# Создаёт ИЗОЛИРОВАННОЕ окружение `.sep-venv` для извлечения эмбиента камеры
# (audio-separator + RoFormer). Держится отдельно от основного venv, потому что
# audio-separator требует более старый Python (3.12), чем основное приложение
# (3.14). Пайплайн вызывает его как подпроцесс — см. whispersync/engine/separation.py.
#
# Запускать из корня проекта. Нужен python3.12 в PATH (или uv).
#
# СЕТЬ: скрипт скачивает пакеты из PyPI, а при первом запуске функции
# audio-separator докачивает веса модели (~1.5 ГБ). См. SECURITY.md.
#
# Как и setup_venv.sh, прежнее окружение не удаляется, пока новое не собрано:
# раньше `rm -rf .sep-venv` шёл первым, и отсутствие python3.12 оставляло
# пользователя без рабочего окружения.
set -euo pipefail

cd "$(dirname "$0")"

VENV=".sep-venv"
BACKUP=".sep-venv-previous-$$"
RESTORE_ON_FAIL=0

restore_backup() {
    if [ "$RESTORE_ON_FAIL" = "1" ] && [ -d "$BACKUP" ]; then
        echo "==> Восстанавливаю прежнее .sep-venv" >&2
        rm -rf "$VENV"
        mv "$BACKUP" "$VENV"
    fi
    rm -rf "$BACKUP"
}
trap restore_backup EXIT

# --- preflight ---------------------------------------------------------------
echo "==> Проверка окружения"
USE_UV=0
if command -v uv >/dev/null 2>&1; then
    USE_UV=1
    echo "    uv: $(command -v uv)"
fi
if [ "$USE_UV" = "0" ]; then
    if ! command -v python3.12 >/dev/null 2>&1; then
        echo "ОШИБКА: нужен python3.12 в PATH (или uv). Существующий .sep-venv не тронут." >&2
        echo "        audio-separator пока не поддерживает более новые Python." >&2
        exit 1
    fi
    echo "    python3.12: $(command -v python3.12)"
    if ! python3.12 -c 'import venv' >/dev/null 2>&1; then
        echo "ОШИБКА: модуль venv недоступен для python3.12." >&2
        exit 1
    fi
fi
if [ -e "$VENV" ] && { [ ! -d "$VENV" ] || [ ! -f "$VENV/pyvenv.cfg" ]; }; then
    echo "ОШИБКА: '$VENV' существует, но не похож на venv (нет pyvenv.cfg)." >&2
    exit 1
fi

if [ -d "$VENV" ]; then
    echo "==> Откладываю прежний .sep-venv (вернётся при ошибке)"
    rm -rf "$BACKUP"
    mv "$VENV" "$BACKUP"
    RESTORE_ON_FAIL=1
fi

# Версия закреплена: без pin окружение невоспроизводимо, а обновление
# audio-separator/torch способно молча сменить поведение сепарации. См.
# requirements-sep.txt.
SEP_REQUIREMENTS="requirements-sep.txt"

if [ "$USE_UV" = "1" ]; then
    echo "==> Создаю .sep-venv (Python 3.12) через uv"
    uv venv --python python3.12 "$VENV"
    echo "==> Ставлю audio-separator[gpu]"
    if [ -f "$SEP_REQUIREMENTS" ]; then
        VIRTUAL_ENV="$PWD/$VENV" uv pip install -r "$SEP_REQUIREMENTS"
    else
        VIRTUAL_ENV="$PWD/$VENV" uv pip install "audio-separator[gpu]"
    fi
else
    echo "==> Создаю .sep-venv через python3.12"
    python3.12 -m venv "$VENV"
    "./$VENV/bin/pip" install --upgrade pip
    if [ -f "$SEP_REQUIREMENTS" ]; then
        "./$VENV/bin/pip" install -r "$SEP_REQUIREMENTS"
    else
        "./$VENV/bin/pip" install "audio-separator[gpu]"
    fi
fi

echo "==> Проверка: RoFormer грузится в .sep-venv"
"./$VENV/bin/python" -c "
from audio_separator.separator import Separator
import torch
print('audio-separator OK, torch', torch.__version__, 'cuda', torch.cuda.is_available())
"

RESTORE_ON_FAIL=0

echo ""
echo "Готово. Модель MelBand-RoFormer скачается автоматически при первом запуске"
echo "с включённой опцией 'Add camera-ambience track' (--ambience-track в CLI)."
