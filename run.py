"""Точка входа: и для запуска из папки, и для сборки в один .exe."""

from __future__ import annotations

import sys
from pathlib import Path

# При обычном запуске пакет лежит в src/. У собранного PyInstaller приложения
# он уже внутри, и путь добавлять не нужно, но лишним это не будет.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from si_helper.app import main  # noqa: E402

if __name__ == "__main__":
    code = main()
    if code != 0:
        # Пользователь запускает файл двойным щелчком. Если просто выйти с
        # ошибкой, окно захлопнется мгновенно и он не увидит, что случилось.
        try:
            input("\n  Нажмите Enter, чтобы закрыть окно…")
        except EOFError:
            pass
    sys.exit(code)
