"""Красный цикл для оболочки программы — без доступа к живой игре.

Проверяется цепочка целиком: запуск Chrome → отладочный порт → подключение →
вставка кода → выполнение на странице-подделке. Всё это агент гоняет у себя,
не отнимая заходы у игрока.

Главная проверка — `test_injection_survives_reload`: код должен вернуться сам
после перезагрузки страницы. Именно этого не умеет закладка, и именно ради
этого делается программа. Сломается — тест покраснеет.
"""

from __future__ import annotations

import functools
import os
import http.server
import shutil
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from si_helper import app, browser, cdp, payload  # noqa: E402

STAND = "tools/stand.html"
MARKER = "window.__proverkaVstavki = (window.__proverkaVstavki || 0) + 1;"


@pytest.fixture(scope="session")
def stand_server():
    """Отдаёт папку проекта по HTTP на свободном порту."""
    handler = functools.partial(QuietHandler, directory=str(ROOT))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{port}"
        httpd.shutdown()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Тот же файловый сервер, но без записей в вывод теста."""

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def chrome(stand_server):
    """Запущенный Chrome без окна плюс подключение к его вкладке.

    Профиль — свой на каждый запуск. Общий не годится: пока предыдущий Chrome
    не закрылся, новый запуск с тем же профилем передаёт ему адрес и выходит,
    не открыв отладочный порт. Из-за этого 2026-09-09 набор рассыпался восемью
    ошибками, а в системе осталось восемь висящих окон.
    """
    profile = Path(tempfile.mkdtemp(prefix="si-shell-"))
    proc, port = browser.launch(f"{stand_server}/{STAND}", headless=True, profile=profile)
    try:
        target = cdp.find_page(port)
        with cdp.Cdp(target["webSocketDebuggerUrl"]) as conn:
            yield conn
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        browser.remove_profile(profile)


# ── сами проверки ────────────────────────────────────────────────────


def test_chrome_found():
    """Chrome находится там, где мы его ищем."""
    assert browser.find_chrome().is_file()


def test_debug_port_opens(chrome):
    """Отладочный порт живой: вкладка отвечает на выполнение выражения."""
    assert chrome.evaluate("1 + 1") == 2


def test_stand_loaded(chrome, stand_server):
    """Открылась именно страница-подделка, а не пустая вкладка."""
    chrome.navigate(f"{stand_server}/{STAND}")
    assert chrome.evaluate("document.title").startswith("Стенд")


def test_stand_has_204_cells(chrome, stand_server):
    """Подделка честно рисует сад: 204 клетки, как в настоящей игре."""
    chrome.navigate(f"{stand_server}/{STAND}")
    assert chrome.evaluate("document.querySelectorAll('#garden img').length") == 204


def test_injection_runs(chrome, stand_server):
    """Вставленный код выполняется на странице."""
    chrome.inject_on_every_load(MARKER)
    chrome.navigate(f"{stand_server}/{STAND}?x=1")
    assert chrome.evaluate("window.__proverkaVstavki") == 1


def test_injection_survives_reload(chrome, stand_server):
    """ГЛАВНОЕ. Код возвращается сам при каждой загрузке страницы.

    Закладка так не умеет: её надо нажимать заново. Если эта проверка
    покраснеет — вся причина делать программу, а не закладку, отпала.
    """
    chrome.inject_on_every_load(MARKER)

    for number in (1, 2, 3):
        chrome.navigate(f"{stand_server}/{STAND}?x={number}")
        assert chrome.evaluate("window.__proverkaVstavki") == 1, (
            f"загрузка №{number}: код не выполнился сам"
        )


def test_injection_can_be_removed(chrome, stand_server):
    """Вставку можно снять — иначе её не выключить без перезапуска браузера."""
    ident = chrome.inject_on_every_load(MARKER)
    chrome.navigate(f"{stand_server}/{STAND}?x=a")
    assert chrome.evaluate("window.__proverkaVstavki") == 1

    chrome.remove_injection(ident)
    chrome.navigate(f"{stand_server}/{STAND}?x=b")
    assert chrome.evaluate("typeof window.__proverkaVstavki") == "undefined"


def test_error_from_page_is_raised(chrome):
    """Ошибку со страницы не проглатываем: молчаливый сбой хуже громкого."""
    with pytest.raises(cdp.CdpError):
        chrome.evaluate("throw new Error('нарочно')")


# ── доставка настоящего разведчика программой ────────────────────────


def test_payload_strips_userscript_header():
    """Шапка ==UserScript== при вставке через CDP не нужна и убирается."""
    code = payload.load("razvedka.user.js")
    assert "==UserScript==" not in code
    assert "РАЗВЕДКА" in code


def test_program_delivers_working_recon(chrome, stand_server):
    """Сквозная проверка: программа доставляет разведчика, он работает.

    Это то, что уйдёт игроку первым: он запускает файл, заходит в игру и
    видит панель с отчётом. Если проверка красная — отправлять нечего.
    """
    chrome.inject_on_every_load(payload.load("razvedka.user.js"))
    chrome.navigate(f"{stand_server}/{STAND}?x=recon")

    report = _wait_for_report(chrome)

    assert "РАЗВЕДКА" in report
    assert "клеток #b1..#b204: 204 из 204" in report, "разведчик не увидел сад"
    assert "КОНТЕКСТ: фрейм garten" in report, "разведчик не дошёл до фрейма"
    assert chrome.evaluate("document.querySelectorAll('pre').length") == 1, (
        "панелей должно быть ровно одна"
    )


def _wait_for_report(conn, timeout: float = 10.0) -> str:
    """Ждёт появления панели: разведчик сначала дожидается фреймов."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if conn.evaluate("document.querySelectorAll('pre').length") >= 1:
            return conn.evaluate("document.querySelector('pre').textContent")
        time.sleep(0.25)
    raise AssertionError(f"панель разведчика не появилась за {timeout:.0f} с")


# ── вставка во ВСЕ вкладки, а не в одну ──────────────────────────────


def test_injection_reaches_a_newly_opened_tab(stand_server):
    """Помощник попадает и в НОВУЮ вкладку, открытую после запуска.

    Вход через портал upjers уходит на другой адрес и нередко открывает новую
    вкладку. Вставка живёт только в той вкладке, к которой подключились, —
    без обхода вкладок помощника там просто нет. Поймано 2026-09-09.
    """
    marker = "window.__vstavka = (window.__vstavka || 0) + 1;"
    profile = Path(tempfile.mkdtemp(prefix="si-shell-"))
    proc, port = browser.launch("about:blank", headless=True, profile=profile)
    tabs: dict = {}
    try:
        cdp.find_page(port)
        app._sync_tabs(port, tabs, marker)
        assert len(tabs) >= 1, "не подключились к первой вкладке"

        # Открываем вторую вкладку — как это сделал бы портал.
        first = cdp.find_page(port)
        with cdp.Cdp(first["webSocketDebuggerUrl"]) as conn:
            conn.call("Target.createTarget", {"url": f"{stand_server}/{STAND}?vtoraya=1"})

        deadline = time.monotonic() + 15
        second = None
        while time.monotonic() < deadline:
            app._sync_tabs(port, tabs, marker)
            for t in cdp.list_targets(port):
                if t.get("type") == "page" and "vtoraya=1" in (t.get("url") or ""):
                    second = t
                    break
            if second and len(tabs) >= 2:
                break
            time.sleep(0.4)

        assert second, "вторая вкладка не открылась"
        assert len(tabs) >= 2, f"обход вкладок её не подхватил: {len(tabs)}"

        with cdp.Cdp(second["webSocketDebuggerUrl"]) as conn:
            conn.navigate(f"{stand_server}/{STAND}?vtoraya=2")
            assert conn.evaluate("window.__vstavka") == 1,                 "во второй вкладке код не выполнился"
    finally:
        for c in tabs.values():
            c.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        browser.remove_profile(profile)


# ── подключение к уже работающему приложению ─────────────────────────
#
# Владелец запустил помощника, когда приложение upjers Home уже было открыто,
# и получил отказ: закройте, мол, сами через трей. Закрывать его
# принудительно нельзя — теряется вход в игру (проверено 2026-09-09).
# Но если приложение открывали МЫ, его отладочный порт уже работает, и
# помощнику нужен именно порт, а не собственный процесс: можно просто
# подключиться. Эти проверки закрывают тот путь.


def test_debug_port_of_finds_the_port_of_a_running_process(stand_server):
    """Порт работающего процесса находится по его номеру.

    Берём настоящий процесс с настоящим отладочным портом, а не подделку:
    ровно так выглядит уже открытое приложение upjers Home.
    """
    profile = Path(tempfile.mkdtemp(prefix="si-attach-"))
    proc, port = browser.launch(f"{stand_server}/{STAND}", headless=True, profile=profile)
    try:
        cdp.wait_for_port(port, timeout=60)
        found = browser.debug_port_of([proc.pid])
        assert found == port, f"нашли не тот порт: {found} вместо {port}"
        assert browser.debug_port_of([]) is None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        browser.remove_profile(profile)


def test_debug_port_of_ignores_a_process_without_a_port():
    """У чужого процесса без отладочного порта искать нечего.

    Номер берём заведомо не наш — собственный процесс проверок. Он живой,
    но браузерного порта у него нет.
    """
    import os
    assert browser.debug_port_of([os.getpid()]) is None


def test_attached_app_says_alive_until_the_port_dies(stand_server):
    """`poll()` молчит, пока приложение живо, и отвечает, когда оно ушло.

    Главному циклу от процесса нужно ровно это. Плюс `terminate()` не должен
    менять ответ `poll()`: у подключённого приложения он обязан быть пустым
    действием — приложение не наше, и остановить его мы не вправе.

    Чего эта проверка НЕ доказывает: что `terminate()` не убьёт приложение
    каким-нибудь посторонним способом. Убивать ему нечем — у `AttachedApp`
    нет ни номера процесса, ни его ручки, только номер порта.
    """
    profile = Path(tempfile.mkdtemp(prefix="si-attach2-"))
    proc, port = browser.launch(f"{stand_server}/{STAND}", headless=True, profile=profile)
    try:
        cdp.wait_for_port(port, timeout=60)
        attached = browser.AttachedApp(port)
        assert attached.poll() is None, "решил, что приложение уже ушло"

        attached.terminate()          # не должен трогать чужой запуск
        time.sleep(1.0)
        assert proc.poll() is None, "terminate() убил чужое приложение"
        assert attached.poll() is None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        browser.remove_profile(profile)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if attached.poll() is not None:
            break
        time.sleep(0.5)
    assert attached.poll() is not None, "не заметил, что приложение закрылось"


def test_show_app_window_finds_nothing_when_there_is_nothing():
    """Пустой список и чужой процесс — ничего не показываем и не падаем."""
    import os
    assert browser.show_app_window([]) == ""
    assert browser.show_app_window([os.getpid()]) == ""
    # Процесс с окнами есть, а подписи такой нет — тоже пусто.
    assert browser.show_app_window([os.getpid()], title_part="такого-окна-нет") == ""


# Эти две проверки открывают НАСТОЯЩЕЕ окно браузера и выводят его вперёд:
# иначе не докажешь, что помощник окно из трея не достаёт. На рабочем столе
# владельца такое всплывать не должно — он это и заметил. Поэтому по
# умолчанию они пропускаются, а на машине сборки (где `CI=true`) идут всегда.
# Запустить у себя намеренно: `SI_OKNA=1 python -m pytest -k show_app_window`.
s_oknami = pytest.mark.skipif(
    not (os.environ.get("CI") or os.environ.get("SI_OKNA")),
    reason="открывает видимое окно; на машине сборки идёт всегда, "
           "у себя — с SI_OKNA=1",
)


@s_oknami
def test_show_app_window_raises_a_visible_window(stand_server):
    """Обычное окно выводится вперёд."""
    profile = Path(tempfile.mkdtemp(prefix="si-window-"))
    proc, port = browser.launch(f"{stand_server}/{STAND}", headless=False, profile=profile)
    try:
        cdp.wait_for_port(port, timeout=60)
        time.sleep(3.0)   # окну нужно время появиться и получить подпись
        if not _windows_of(proc.pid, "Стенд"):
            pytest.skip("окно браузера не нашлось по подписи — проверять нечего")
        assert browser.show_app_window([proc.pid], title_part="Стенд") == "vpered"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        browser.remove_profile(profile)


@s_oknami
def test_show_app_window_leaves_a_tray_window_alone(stand_server):
    """ГЛАВНОЕ: спрятанное окно НЕ достаём — только сообщаем.

    Приложение прячется в трей своей логикой, и окно при этом не свёрнуто,
    а скрыто. Вытащенное снаружи, оно появляется РАЗОБРАННЫМ: рамка во весь
    экран, отрисовка в старом размере, справа чёрное поле — и не чинится
    ничем, ни разворотом, ни явным размером. Владелец это увидел
    2026-09-10, и лечение оказалось одно: не лезть.
    """
    import ctypes

    profile = Path(tempfile.mkdtemp(prefix="si-tray-"))
    proc, port = browser.launch(f"{stand_server}/{STAND}", headless=False, profile=profile)
    try:
        cdp.wait_for_port(port, timeout=60)
        time.sleep(3.0)
        hwnds = _windows_of(proc.pid, "Стенд")
        if not hwnds:
            pytest.skip("окно браузера не нашлось по подписи — проверять нечего")

        u32 = ctypes.WinDLL("user32", use_last_error=True)
        SW_HIDE = 0
        for h in hwnds:
            u32.ShowWindow(h, SW_HIDE)     # как приложение прячется в трей
        time.sleep(0.5)
        assert not any(u32.IsWindowVisible(h) for h in hwnds), "окно не спряталось"

        assert browser.show_app_window([proc.pid], title_part="Стенд") == "v-tree"
        time.sleep(0.5)
        assert not any(u32.IsWindowVisible(h) for h in hwnds),             "окно вытащили из трея — именно этого делать нельзя"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        browser.remove_profile(profile)


def _windows_of(pid: int, title_part: str) -> list[int]:
    """Окна процесса, в подписи которых есть кусок текста."""
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.WinDLL("user32", use_last_error=True)
    out: list[int] = []
    cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _l):
        owner = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            n = u32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value and title_part.lower() in buf.value.lower():
                out.append(hwnd)
        return True

    u32.EnumWindows(cb(each), 0)
    return out


def test_game_host_is_recognised():
    """Узел игры отличаем от портала и от пустого адреса.

    От этого зависит распознавание «страница есть, а игры на ней нет»:
    после потери сессии сервер отдаёт по адресу игры ПУСТУЮ страницу, и
    принять её за игру легко — 2026-09-10 я и принял.
    """
    assert app._na_uzle_igry("https://s5.ru.molehillempire.com/main.php")
    assert app._na_uzle_igry("https://www.sadowajaimperija.ru/main.php")
    assert app._na_uzle_igry("https://s1.wurzelimperium.de/main.php")
    assert not app._na_uzle_igry("https://ru.upjers.com/my-games")
    assert not app._na_uzle_igry("about:blank")
    assert not app._na_uzle_igry("")


def test_only_one_helper_takes_the_lock():
    """Второй запуск помощника права не получает.

    Два помощника разом уводят приложение в игру каждый по-своему, и
    получаются два игровых окна — владелец это и увидел 2026-09-10.
    Имя замка здесь своё, чтобы не мешать настоящему запуску.
    """
    import uuid

    imya = "SadovyPomoshnik-proverka-" + uuid.uuid4().hex[:8]
    assert app.take_single_run(imya) is True, "первый запуск не смог занять замок"
    assert app.take_single_run(imya) is False, "второй запуск занял замок повторно"
    # Другое имя — другой замок, мешать не должен.
    assert app.take_single_run(imya + "-drugoe") is True


def test_version_flag_answers_and_does_not_touch_the_log(tmp_path, monkeypatch, capsys):
    """`--version` отвечает в поток вывода и НЕ трогает журнал.

    Файл раздаётся страницей релиза, и «та ли версия скачалась» надо уметь
    спросить у самого файла. Ответ обязан идти в командную строку: у
    программы нет консоли, и `setup_output()`, не найдя её, уводит весь
    вывод в журнал — ответ уходил бы туда же, затирая журнал работающего
    помощника. Поэтому проверка следит и за порядком.
    """
    from si_helper import __version__

    zhurnal = tmp_path / "помощник.log"
    monkeypatch.setattr(app, "log_path", lambda: zhurnal)

    code = app.main(["--version"])
    assert code == 0
    vyvod = capsys.readouterr().out
    assert __version__ in vyvod, f"версии нет в ответе: {vyvod!r}"
    assert "Садовый помощник" in vyvod
    assert not zhurnal.exists(), "журнал тронут, хотя просили только версию"
