"""Замер: не замедляет ли наша вставка загрузку страницы игры.

Жалоба игрока — «сад грузится больше минуты». Прежде чем строить догадки, надо
отделить своё влияние от чужого. Скрипт грузит ОБЩЕДОСТУПНУЮ страницу игры
(без входа и без единого действия) на чистом профиле — с нашей вставкой и без
неё — и печатает время до события `load`.

Ограничение, о котором надо помнить: мы меряем страницу входа, а не сад.
Сад доступен только внутри аккаунта, и туда агент не ходит.

Запуск:  python tools/measure_load.py [сколько повторов]
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from si_helper import browser, cdp, payload  # noqa: E402

URL = "https://ru.molehillempire.com/"


def measure(inject: bool) -> tuple[float, int]:
    """Возвращает секунды до `load` и число запросов страницы."""
    profile = Path(tempfile.mkdtemp(prefix="si-measure-"))
    proc, port = browser.launch("about:blank", profile=profile, headless=True)
    try:
        target = cdp.find_page(port)
        with cdp.Cdp(target["webSocketDebuggerUrl"]) as conn:
            if inject:
                conn.inject_on_every_load(payload.load("panel.js"))
            started = time.monotonic()
            try:
                conn.navigate(URL, timeout=120)
            except cdp.CdpTimeout:
                return (float("inf"), -1)
            elapsed = time.monotonic() - started
            try:
                count = conn.evaluate("performance.getEntriesByType('resource').length")
            except cdp.CdpError:
                count = -1
            return (elapsed, count or 0)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"страница: {URL}")
    print(f"профиль каждый раз чистый, повторов: {repeats}\n")

    results: dict[str, list[float]] = {"без вставки": [], "с нашей вставкой": []}
    for i in range(repeats):
        for label, inject in (("без вставки", False), ("с нашей вставкой", True)):
            secs, res = measure(inject)
            results[label].append(secs)
            print(f"  прогон {i + 1}  {label:<18} {secs:6.2f} с   запросов: {res}")

    print()
    for label, values in results.items():
        good = [v for v in values if v != float("inf")]
        if good:
            print(f"  {label:<18} среднее {sum(good) / len(good):.2f} с, "
                  f"лучшее {min(good):.2f} с, худшее {max(good):.2f} с")
        else:
            print(f"  {label:<18} ни один прогон не догрузился")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
