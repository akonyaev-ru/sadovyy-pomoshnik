"""Поиск по минифицированному исходнику игры.

`wurzel_all.js` — 1,26 МБ в несколько строк, поэтому grep и sed бесполезны:
любая «строка» тянет за собой сотни килобайт. Ищем по смещению в символах и
печатаем ограниченный кусок вокруг находки.

Запуск:  python tools/probe_game_js.py <что искать> [сколько символов вокруг]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "reference" / "game" / "wurzel_all.js"


def snippets(text: str, pattern: str, width: int = 320, limit: int = 6) -> list[str]:
    out = []
    for m in re.finditer(pattern, text):
        start = max(0, m.start() - width // 4)
        end = min(len(text), m.end() + width)
        out.append(f"[смещение {m.start()}]\n…{text[start:end]}…")
        if len(out) >= limit:
            break
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    pattern = sys.argv[1]
    width = int(sys.argv[2]) if len(sys.argv) > 2 else 320
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 6

    path = DEFAULT
    if not path.is_file():
        print(f"нет файла: {path}")
        return 2

    text = path.read_text(encoding="utf-8", errors="replace")
    found = snippets(text, pattern, width, limit)
    if not found:
        print(f"НЕ НАЙДЕНО: {pattern}")
        return 1

    print(f"найдено кусков: {len(found)} (шаблон: {pattern})\n")
    print(("\n" + "-" * 70 + "\n").join(found))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
