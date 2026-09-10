"""Загрузка кода, который вставляется в страницу игры.

Файлы лежат рядом с исходниками и попадают внутрь `.exe` при сборке, поэтому
путь ищется двумя способами: обычный запуск из папки и запуск из собранного
PyInstaller файла, который распаковывает данные во временный каталог.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


class PayloadNotFound(RuntimeError):
    """Нужный файл со скриптом не нашёлся."""


def _base_dir() -> Path:
    """Папка, от которой отсчитываются файлы скриптов.

    У собранного PyInstaller приложения данные лежат в `sys._MEIPASS`, а не
    рядом с исполняемым файлом. Без этой ветки собранный `.exe` не найдёт
    скрипты и упадёт уже у пользователя.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def strip_userscript_header(code: str) -> str:
    """Убирает блок ==UserScript==: при вставке через CDP он не нужен."""
    return re.sub(
        r"//\s*==UserScript==.*?//\s*==/UserScript==\s*",
        "",
        code,
        flags=re.DOTALL,
    )


def load(name: str) -> str:
    """Читает скрипт по имени файла и готовит его к вставке."""
    path = _base_dir() / name
    if not path.is_file():
        raise PayloadNotFound(f"не нашёл файл скрипта: {path}")
    return strip_userscript_header(path.read_text(encoding="utf-8"))
