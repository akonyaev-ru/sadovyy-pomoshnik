"""Проверка собранного .exe на подделке игры.

Отдельно от pytest, потому что проверяет не исходники, а результат сборки:
находит ли собранный файл вложенные в него скрипты (путь `sys._MEIPASS`) и
переживает ли вывод консоль Windows. Оба этих класса дефектов уже ломали сборку
и оба невидимы для pytest.

Проверяется режим по умолчанию — панель помощника, то есть ровно то, что
получит игрок.

Запуск:  python tools/check_exe.py
"""

from __future__ import annotations

import functools
import http.server
import socketserver
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from si_helper import cdp  # noqa: E402

import os

# Собранное лежит на локальном диске, а не в проекте — см. tools/build.py.
BUILD_DIR = Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "SadovyPomoshnik" / "build"

PORT = 9333
STAND = "tools/stand-game.html"
# Стенд открывается под именем игрового сервера: помощник теперь работает
# ТОЛЬКО на страницах игры, и проверять его надо в тех же условиях.
GAME_HOST = "s5.ru.molehillempire.com"
EXPECTED_COUNT = 12  # столько растений в саду стенда подлежит поливу
STATUS = "document.getElementById('si-helper-status').textContent"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return


def find_exe() -> Path:
    """Проверяемый файл: по умолчанию рабочая копия, либо указанный явно.

    Путь аргументом нужен, чтобы проверять именно ту копию, которой будут
    пользоваться, — например, лежащую в `dist/` на Google Диске: Диск уже
    портил собранный файл, и «файл на месте» не значит «файл рабочий».
    """
    if len(sys.argv) > 1:
        given = Path(sys.argv[1])
        if not given.is_file():
            raise SystemExit(f"Нет такого файла: {given}")
        return given
    candidates = sorted(BUILD_DIR.glob("*.exe"))
    if not candidates:
        raise SystemExit(f"Нет .exe в {BUILD_DIR} — сначала: python tools/build.py")
    return candidates[0]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    exe = find_exe()
    print(f"проверяю: {exe.name} ({exe.stat().st_size / 1_048_576:.1f} МБ)")

    handler = functools.partial(QuietHandler, directory=str(ROOT))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        web_port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        url = f"http://{GAME_HOST}:{web_port}/{STAND}"
        # Свой профиль на каждый прогон. Общий не годится: если Chrome с ним
        # уже запущен, новый запуск просто передаёт ему адрес и выходит —
        # отладочный порт не открывается, а проверка цепляется к чужому окну
        # с прошлого раза и видит не ту страницу. Ровно так она и соврала
        # 2026-09-09, сказав, что на странице нет `window.__sent`.
        profile = Path(tempfile.mkdtemp(prefix="si-check-"))
        proc = subprocess.Popen(
            # `--target browser` обязателен: по умолчанию программа теперь
            # селится в приложение upjers Home, если оно установлено, и до
            # стенда дело не доходит. Проверка сборки поймала это сразу.
            [str(exe), "--url", url, "--port", str(PORT), "--profile", str(profile),
             "--target", "browser",
             "--host-rules", f"MAP {GAME_HOST} 127.0.0.1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            return _check(proc)
        finally:
            httpd.shutdown()
            kill_tree(proc)
            shutil.rmtree(profile, ignore_errors=True)


def kill_tree(proc: subprocess.Popen) -> None:
    """Закрывает программу ВМЕСТЕ с её браузером.

    На Windows `terminate()` убивает только саму программу, а запущенный ею
    Chrome переживает это и остаётся висеть. За несколько неудачных прогонов
    так накопилось 29 окон, и следующая проверка цеплялась к чужому окну,
    видела не ту страницу и врала. Поэтому гасим всё дерево процессов.
    """
    if proc.poll() is None:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _check(proc: subprocess.Popen) -> int:
    try:
        target = cdp.find_page(PORT, timeout=40)
    except cdp.CdpTimeout as exc:
        print(f"ПРОВАЛ: {exc}")
        print("--- вывод программы ---")
        print(_drain(proc))
        return 1

    with cdp.Cdp(target["webSocketDebuggerUrl"]) as conn:
        # Ждём именно стенд, а не просто появление кнопок: кнопки могли бы
        # оказаться на пустой странице, показанной браузером до перехода.
        if not _wait(conn, "typeof window.__sent !== 'undefined'"):
            print("ПРОВАЛ: страница стенда так и не загрузилась")
            return 1
        if not _wait(conn, "!!document.getElementById('si-helper')"):
            print("ПРОВАЛ: панель помощника не появилась")
            return 1

        checks = {
            "панель одна": conn.evaluate("document.querySelectorAll('#si-helper').length") == 1,
            "гном с лейкой на месте": conn.evaluate(
                "(document.querySelector('#si-helper [data-si-button=water]')"
                ".getAttribute('src')||'').indexOf('kannenzwerg.gif')>=0"
            ),
            "сама ничего не делает": conn.evaluate("window.__sent.length") == 0,
            # Сады — то, ради чего выпуск 3.3.0: у игрока не было
            # перехода в пятый сад, хотя у Ивана кнопка на него была. С 3.4.0
            # они стоят столбиком в правой рамке игры, как и было у него,
            # плюс две кнопки города — итого семь иконок.
            "столбик садов на месте": conn.evaluate(
                # Считаем по собственному признаку садов: в столбике с 3.7.0
                # не только они, и «картинка без метки города» стало неверно.
                "document.querySelectorAll('#si-helper-gardens [data-si-garden]').length"
            ) == 5,
            "кнопки города на месте": conn.evaluate(
                "document.querySelectorAll('#si-helper-gardens [data-si-city]').length"
            ) == 2,
            # Выгодность в листке попрошайки — выпуск 3.5.0. Открываем листок
            # так же, как открыл бы игрок, и смотрим, посчитан ли процент.
            # Стенд: 10 моркови по 3 и 5 огурцов по 2 — это 40, дают 48.
            "выгодность у попрошайки": _wimp_percent(conn) == "+20%",
            # Грибы и улитки в столбике — выпуск 3.7.0. Рисуются только тем,
            # у кого эти места есть; на стенде они есть.
            "грибы и улитки на месте": conn.evaluate(
                "(function(){var g=document.querySelector('#si-helper-gardens [data-si-nav=megafruit]');"
                "var u=document.querySelector('#si-helper-gardens [data-si-nav=snailracing]');"
                "return !!g && !!u && (g.getAttribute('src')||'').indexOf('pilzgarten')>=0"
                " && (u.getAttribute('src')||'').indexOf('Schnellreise')>=0})()"
            ),
            # Быстрая продажа на рынке — то, ради чего выпуск 3.6.0.
            "гном рынка на месте": conn.evaluate(
                "(document.querySelector('#si-helper [data-si-button=sell]')"
                ".getAttribute('src')||'').indexOf('marktplatz_neu.png')>=0"
            ),
        }

        conn.evaluate("document.querySelector('#si-helper [data-si-button=water]').click(); true")
        if not _wait(conn, f"/Полито|нечего|Не /.test({STATUS})", timeout=30):
            print("ПРОВАЛ: полив не завершился")
            return 1

        sent = conn.evaluate(
            "window.__sent.reduce(function(a,e){return a.concat(e.felder)},[])"
        )
        checks["полито ровно нужное"] = len(sent) == EXPECTED_COUNT
        checks["без повторов"] = len(sent) == len(set(sent))
        checks["отчёт словами"] = conn.evaluate(STATUS) == f"Полито {EXPECTED_COUNT} растений"

        for name, ok in checks.items():
            print(f"  {'OK  ' if ok else 'СБОЙ'}  {name}")
        if not checks["полито ровно нужное"]:
            print(f"        ушло: {sorted(sent)}")

        try:
            conn.call("Browser.close", timeout=5)
        except (cdp.CdpError, cdp.CdpTimeout, OSError):
            pass  # браузер и так закрывается — молчим осознанно

    failed = [n for n, ok in checks.items() if not ok]
    if failed:
        print(f"\nПРОВАЛ: {len(failed)} из {len(checks)} — {', '.join(failed)}")
        return 1

    print(f"\nВсе {len(checks)} проверки зелёные. Сборка рабочая.")
    return 0


def _wimp_percent(conn) -> str:
    """Открывает листок попрошайки и возвращает посчитанный процент."""
    conn.evaluate("wimparea.show(11); true")
    if not _wait(conn, "!!document.getElementById('si-wimp')"):
        return "листок без расчёта"
    percent = conn.evaluate(
        "(document.querySelector('#si-wimp [data-si-wimp=percent]')||{}).textContent||''"
    )
    # Листок обязательно закрываем: пока он открыт, игра не даёт поливать —
    # и следующая проверка честно упиралась в «закройте список покупок».
    conn.evaluate("wimparea.close(); true")
    return percent


def _wait(conn: cdp.Cdp, expr: str, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if conn.evaluate(expr):
                return True
        except (cdp.CdpError, cdp.CdpTimeout):
            pass
        time.sleep(0.3)
    return False


def _drain(proc: subprocess.Popen) -> str:
    """Вывод программы. У сборки без консоли он идёт в файл, а не в трубу."""
    if proc.poll() is None:
        proc.terminate()
    try:
        out, _ = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    if out and out.strip():
        return out

    log = BUILD_DIR.parent / "помощник.log"
    if log.is_file():
        head = "--- журнал " + str(log) + " ---" + chr(10)
        return head + log.read_text(encoding="utf-8", errors="replace")
    return "(пусто: ни в трубе, ни в журнале)"


if __name__ == "__main__":
    raise SystemExit(main())
