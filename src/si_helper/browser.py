"""Запуск Chrome с отладочным портом и собственным профилем.

Про профиль. Chrome не отдаёт отладочный порт профилю, который уже запущен:
если попросить порт для обычного профиля, Chrome молча передаст адрес
работающему окну и завершится, а порт так и не откроется. Поэтому программа
всегда поднимает браузер со своей папкой профиля. Обычный Chrome пользователя
при этом не трогается вовсе, а вход в игру запоминается внутри нашей папки.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import urllib.request
import time
from pathlib import Path

# Где искать Chrome. Порядок важен: сначала обычные места установки.
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
)


# Приложение upjers Home — тоже Chromium, только внутри Electron. Проверено
# 2026-09-09 по его папке: `LICENSE.electron.txt`, `LICENSES.chromium.html`,
# `resources/app.asar`, `icudtl.dat`, `chrome_100_percent.pak`, `libEGL.dll`.
# Оно принимает `--remote-debugging-port`, отдаёт цели `page` (оболочка) и
# `webview` (в нём открывается сама игра) и принимает вставку кода.
UPJERS_CANDIDATES = (
    r"%LOCALAPPDATA%\Programs\upjers-playground2\upjers Home.exe",
    r"%LOCALAPPDATA%\Programs\upjers-home\upjers Home.exe",
    r"%PROGRAMFILES%\upjers Home\upjers Home.exe",
)

UPJERS_PROCESS = "upjers Home.exe"


class BrowserNotFound(RuntimeError):
    """Chrome не нашёлся на этом компьютере."""


def find_chrome() -> Path:
    """Ищет chrome.exe по обычным местам установки, затем в PATH."""
    for raw in CHROME_CANDIDATES:
        p = Path(os.path.expandvars(raw))
        if p.is_file():
            return p
    which = shutil.which("chrome") or shutil.which("chrome.exe")
    if which:
        return Path(which)
    raise BrowserNotFound(
        "Не нашёл chrome.exe. Проверьте, что Google Chrome установлен, "
        "или укажите путь к нему параметром --chrome."
    )


def find_upjers() -> Path | None:
    """Ищет приложение upjers Home. Возвращает None, если его нет."""
    for raw in UPJERS_CANDIDATES:
        p = Path(os.path.expandvars(raw))
        if p.is_file():
            return p
    return None


def upjers_running() -> list[int]:
    """Номера уже запущенных процессов приложения.

    У приложения замок на один экземпляр: если оно уже открыто, новый запуск
    просто передаёт команду старому и завершается, НЕ открыв отладочный порт.
    Ровно на это я напоролся при первой проверке — порт молчал 45 секунд, а
    причина была в пяти висящих процессах.
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {UPJERS_PROCESS}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="cp866", errors="replace", timeout=15,
        ).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[1].strip().isdigit():
            pids.append(int(parts[1].strip()))
    return pids


def _port_answers(port: int, timeout: float = 1.5) -> bool:
    """Отвечает ли порт как отладочный порт браузера."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return False
    return isinstance(data, dict) and (
        "webSocketDebuggerUrl" in data or "Browser" in data
    )


def debug_port_of(pids: list[int]) -> int | None:
    """Отладочный порт уже работающего приложения, если он открыт.

    Зачем это нужно. У приложения замок на один экземпляр: пока живёт хоть
    один его процесс, новый запуск не откроет свой порт. Раньше мы в этом
    случае просто просили закрыть приложение руками — а закрывать его
    принудительно нельзя, оно теряет вход в игру.

    Но если приложение запускали МЫ, порт у него уже открыт, и подключиться
    к нему можно без всякого перезапуска. Ровно так помощник и работает
    дальше — ему нужен порт, а не свой собственный процесс.

    Порт ищем по списку слушающих сокетов этих процессов и проверяем
    запросом: мало ли что ещё слушает приложение.
    """
    if not pids:
        return None
    nashi = set(pids)
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, encoding="cp866",
            errors="replace", timeout=20,
        ).stdout
    except Exception:
        return None

    kandidaty: list[int] = []
    for line in out.splitlines():
        parts = line.split()
        # Строка вида: TCP  127.0.0.1:63951  0.0.0.0:0  LISTENING  7080
        # Слово состояния переведено на язык системы — на него не смотрим.
        if len(parts) < 4 or parts[0].upper() != "TCP":
            continue
        if not parts[-1].isdigit() or int(parts[-1]) not in nashi:
            continue
        adres = parts[1]
        if not adres.startswith("127.0.0.1:"):
            continue
        try:
            kandidaty.append(int(adres.rsplit(":", 1)[1]))
        except ValueError:
            continue

    for port in kandidaty:
        if _port_answers(port):
            return port
    return None


def show_app_window(pids: list[int], title_part: str = "upjers") -> str:
    """Выводит окно приложения вперёд — но НЕ достаёт его из трея.

    Возвращает:
      * `"vpered"`  — окно было на экране или свёрнуто, мы его показали;
      * `"v-tree"`  — окно спрятано в трей: НЕ ТРОГАЛИ, надо сказать человеку;
      * `""`        — окна не нашли.

    ⚠️ ПОЧЕМУ СПРЯТАННОЕ В ТРЕЙ ОКНО НЕ ТРОГАЕМ.
    Проверено на живой системе 2026-09-10 и стоило владельцу кривого экрана.
    Приложение прячется в трей своей собственной логикой, и окно при этом
    не свёрнуто, а СКРЫТО (`IsWindowVisible = False`). Если вытащить его
    снаружи через `SW_SHOW`, окно появляется, а отрисовка остаётся в старом
    размере: рамка 1920 px, а страница считает себя 892 px — справа чёрное
    поле. Причём чинится это НИКАК: ни разворот, ни явный `SetWindowPos`
    отрисовку не сдвигают, страница так и стоит на 892×610. Мы обходим
    приложение с чёрного хода, и оно остаётся разобранным.
    Правильный путь для человека — значок upjers у часов; наше дело сказать
    об этом словами, а не лезть в чужое окно.

    `title_part` — по какой подписи узнаём нужное окно. Вынесено в параметр
    не для гибкости, а чтобы это можно было ПРОВЕРИТЬ на постороннем окне
    с известной подписью.
    """
    if not pids or os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return ""

    nashi = set(pids)
    found: list[int] = []
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _each(hwnd, _lparam):
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in nashi:
                return True
            n = u32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            # Окон у Electron много и почти все служебные; нужное — с
            # подписью приложения.
            if buf.value and title_part.lower() in buf.value.lower():
                found.append(hwnd)
            return True

        u32.EnumWindows(callback(_each), 0)
        if not found:
            return ""

        SW_RESTORE = 9
        pokazali = False
        for hwnd in found:
            if not u32.IsWindowVisible(hwnd):
                continue                      # в трее — не наше дело
            if u32.IsIconic(hwnd):
                u32.ShowWindow(hwnd, SW_RESTORE)   # свёрнутое развернуть можно
            u32.SetForegroundWindow(hwnd)
            pokazali = True
    except Exception:
        return ""
    return "vpered" if pokazali else "v-tree"


class AttachedApp:
    """Уже работающее приложение, которое мы НЕ запускали.

    Главному циклу от процесса нужно ровно две вещи: «ещё живо?» и
    «останови». Здесь `poll()` смотрит, отвечает ли отладочный порт, а
    `terminate()` не делает НИЧЕГО: чужой запуск не наш, чтобы его снимать,
    и вместе с ним ушёл бы вход в игру.
    """

    def __init__(self, port: int) -> None:
        self.port = port
        self._ushlo: int | None = None

    def poll(self) -> int | None:
        if self._ushlo is not None:
            return self._ushlo
        if _port_answers(self.port, timeout=2.0):
            return None
        self._ushlo = 0
        return 0

    def terminate(self) -> None:
        return None


def close_upjers(timeout: float = 12.0) -> int:
    """Закрывает приложение upjers Home и ждёт, пока оно действительно уйдёт.

    Зачем: у приложения замок на один экземпляр, и пока живёт хоть один его
    процесс, новый запуск не откроет отладочный порт — помощник не появится.
    А процессы остаются висеть и после закрытия окна: Electron держит главный,
    отрисовку, графику и служебные. Владелец видел «6 процессов» при закрытых
    окнах — это ровно тот случай.

    ⚠️ ВЫЗЫВАТЬ ТОЛЬКО ПО ЯВНОМУ УКАЗАНИЮ (`--force-close`).
    Проверено 2026-09-09: приложение прячется в трей и штатно не выходит —
    ни `taskkill` без `/F`, ни `WM_CLOSE` его не берут. Остаётся только убить,
    а убитый Electron НЕ успевает сохранить вход: после этого игра встречает
    окном пароля. Мягкая попытка ниже оставлена на случай, если приложение
    когда-нибудь научится выходить само, но полагаться на неё нельзя.
    """
    pids = upjers_running()
    if not pids:
        return 0
    closed = len(pids)

    for force in (False, True):
        pids = upjers_running()
        if not pids:
            break
        cmd = ["taskkill"] + (["/F"] if force else []) + ["/T"]
        for pid in pids:
            cmd += ["/PID", str(pid)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=20)
        except Exception:
            pass

        deadline = time.monotonic() + (timeout / 2)
        while time.monotonic() < deadline:
            if not upjers_running():
                return closed
            time.sleep(0.4)

    return closed


def launch_app(app: Path, port: int | None = None) -> tuple[subprocess.Popen, int]:
    """Запускает приложение upjers Home с отладочным портом.

    Ни адреса, ни своей папки профиля не передаём: у приложения свой вход и
    свои сохранённые данные. Игрок заходит в игру там же, где привык, —
    ничего заново вводить не надо. Этим приложение выгодно отличается от
    отдельного Chrome, из-за которого терялся вход.
    """
    port = port or free_port()
    proc = subprocess.Popen(
        [str(app), f"--remote-debugging-port={port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc, port


def free_port() -> int:
    """Свободный порт от системы: фиксированный номер рано или поздно занят."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def profile_dir() -> Path:
    """Своя папка профиля рядом с данными пользователя, а не в Program Files."""
    base = Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "SadovyPomoshnik"
    base.mkdir(parents=True, exist_ok=True)
    return base / "chrome-profile"


def launch(
    url: str,
    port: int | None = None,
    chrome: Path | None = None,
    profile: Path | None = None,
    headless: bool = False,
    host_rules: str | None = None,
) -> tuple[subprocess.Popen, int]:
    """Запускает Chrome и возвращает процесс и номер отладочного порта."""
    exe = chrome or find_chrome()
    port = port or free_port()
    prof = profile or profile_dir()
    prof.mkdir(parents=True, exist_ok=True)

    args = [
        str(exe),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={prof}",
        # Без этого Chrome при первом запуске показывает мастер настройки и
        # предложение войти в аккаунт — маме это ни к чему.
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    if host_rules:
        # Только для проверок: заставляет браузер считать, что локальный стенд
        # находится на узле игры. Так граница «работаем только на страницах
        # игры» проверяется по-настоящему, а не через лазейку в самом коде.
        args.append(f"--host-resolver-rules={host_rules}")
    if headless:
        # Режим для проверок: окно не открывается, всё остальное как обычно.
        args += ["--headless=new", "--disable-gpu"]
    args.append(url)

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc, port
