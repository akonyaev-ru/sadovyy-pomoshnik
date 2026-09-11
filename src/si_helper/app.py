"""Программа-помощник: открывает игру и держит в ней вставленный код.

Что происходит при запуске:
  1. находим Chrome и поднимаем его со своей папкой профиля;
  2. подключаемся к нему по отладочному порту;
  3. ставим скрипт, который выполняется при КАЖДОЙ загрузке страницы;
  4. ждём, пока пользователь закроет браузер.

Пароль от игры программа не спрашивает, не видит и не хранит: пользователь
входит сам в обычном окне браузера.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from pathlib import Path

from . import __version__, browser, cdp, payload

# Куда открывать игру, если приложения upjers Home на машине нет и работаем
# через обычный браузер.
#
# НЕ публичная страница игры. Раньше здесь стоял `https://ru.molehillempire.com/`
# — витрина «играть бесплатно», где никто не вошёл: вход у upjers единый и
# передаётся ЧЕРЕЗ ПОРТАЛ, а с витрины игрок попадает в окно пароля. Тот же
# адрес однажды уже стоил владельцу потерянного входа. Ведём на портал, где
# он либо уже вошёл, либо войдёт как обычно.
GAME_URL = "https://ru.upjers.com/my-games"

SCRIPTS = {
    # То, что получает игрок по умолчанию: кнопки-помощники в игре.
    "panel": "panel.js",
    # Разведчик оставлен: пригодится, если игра изменится и надо будет
    # заново посмотреть, что она выкладывает на страницу.
    "razvedka": "razvedka.user.js",
}


LOG_NAME = "помощник.log"


def log_path() -> Path:
    return Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "SadovyPomoshnik" / LOG_NAME


def rotate_log(path: Path) -> None:
    """Прошлый журнал остаётся рядом как `помощник.1.log`.

    Журнал открывается на запись с нуля, и до 2026-09-11 каждый запуск
    затирал предыдущий. Игрок, у которого что-то не вышло, запускает
    помощника ещё раз — и журнал неудачного запуска пропадает раньше, чем
    его успеют прочитать. Ровно это случилось с первой живой проверкой:
    два окна, пустое и второе, а разбираться не по чему. Один прошлый
    запуск теперь всегда под рукой.
    """
    try:
        if path.exists():
            os.replace(path, path.with_name(path.stem + ".1" + path.suffix))
    except OSError:
        pass


def has_console() -> bool:
    """Есть ли у программы настоящее окно консоли.

    Проверять `sys.stdout is None` НЕДОСТАТОЧНО: у сборки без консоли поток
    может быть на месте, но писать в пустоту. Так и вышло — журнал не появился
    вовсе, а сообщения пропали. Спрашиваем у самой Windows.
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return True


def setup_output() -> None:
    """Готовит вывод: в консоль, если она есть, иначе в файл.

    Молча терять сообщения нельзя: по ним разбираются, что пошло не так, и
    оттуда же берётся замер полосы помощников.
    """
    if sys.stdout is None or sys.stderr is None or not has_console():
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        rotate_log(path)
        stream = open(path, "w", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
        return
    setup_console()


def show_error(text: str) -> None:
    """Показывает окно с ошибкой — единственный способ докричаться без консоли."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None, text, "Садовый помощник", 0x10)
    except Exception:
        pass


def show_wait(text: str) -> None:
    """Окно с одной кнопкой «ОК», которое ЖДЁТ нажатия.

    Нужно там, где от человека требуется действие, а потом — продолжение
    работы: «закройте приложение через трей и нажмите ОК». `show_error`
    для этого не годится по смыслу (значок ошибки и выход), `show_note` —
    по устройству (не ждёт).
    """
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None, text, "Садовый помощник", 0x40)   # 0x40 — значок «i», кнопка ОК
    except Exception:
        pass


_ZAMOK = None       # держим ручку мьютекса живой, пока живёт программа


def take_single_run(name: str = "SadovyPomoshnik-odin-zapusk") -> bool:
    """Занимает право быть единственным работающим помощником.

    ЗАЧЕМ. Два помощника, запущенных разом, оба ведут приложение в игру —
    и владелец 2026-09-10 получил два игровых окна. Приложение тут ни при
    чём: это мы дублировали работу. Такое легко устроить случайно, дважды
    щёлкнув по значку.

    Держим именованный мьютекс Windows: систему просить не о чем, она сама
    отпустит его при завершении процесса, даже аварийном. Не вышло занять —
    работать не мешаем: лучше два помощника, чем ни одного.

    Имя вынесено в параметр, чтобы это можно было проверить, не мешая
    настоящему запуску.
    """
    global _ZAMOK
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        ruchka = k32.CreateMutexW(None, False, name)
        if not ruchka:
            return True
        if k32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
            k32.CloseHandle(ruchka)
            return False
        _ZAMOK = ruchka
        return True
    except Exception:
        return True


def show_note(text: str) -> None:
    """Показывает окно с сообщением, НЕ останавливая работу помощника.

    Отличие от `show_error`: то окно показывают перед выходом, и ждать
    нажатия можно. Здесь помощник продолжает работать, поэтому окно
    открывается в отдельной нити — иначе он бы стоял, пока человек не
    нажмёт «ОК».
    """
    def _pokazat() -> None:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, text, "Садовый помощник", 0x40)   # 0x40 — значок «i»
        except Exception:
            pass

    try:
        threading.Thread(target=_pokazat, daemon=True).start()
    except Exception:
        pass


def setup_console() -> None:
    """Готовит консоль Windows к русскому выводу.

    Без этого собранный .exe падал на первой же строке: консоль по умолчанию
    в cp1251, а в заголовке была линия «─» (U+2500), которой в cp1251 нет —
    `UnicodeEncodeError` и «Failed to execute script». Поймано `check_exe.py`.

    Ставим кодовую страницу UTF-8 и на всякий случай велим потоку заменять
    непечатаемое, а не падать: сообщение с испорченным символом всё равно
    полезнее, чем аварийный выход.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:  # консоли может не быть вовсе — не повод падать
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _say(text: str = "") -> None:
    print(text, flush=True)


def _head(script_name: str, url: str) -> None:
    _say()
    _say("  Садовый помощник " + __version__)
    _say("  " + "-" * 52)
    _say(f"  режим:   {script_name}")
    _say(f"  адрес:   {url}")
    _say()


def run(
    url: str,
    script_name: str,
    chrome_path: Path | None = None,
    port: int | None = None,
    profile: Path | None = None,
    host_rules: str | None = None,
    target: str = "auto",
    go_straight: bool = True,
    force_close: bool = False,
    app_override: Path | None = None,
) -> int:
    try:
        source = payload.load(SCRIPTS[script_name])
    except payload.PayloadNotFound as exc:
        _say(f"  ОШИБКА: {exc}")
        return 2

    # Куда селиться: в приложение upjers Home или в отдельный Chrome.
    # Путь, названный явно (`--app`), сильнее любого поиска.
    poisk: list[str] = []
    if target == "browser":
        app_path = None
    elif app_override is not None:
        app_path = app_override if browser._is_upjers_exe(app_override) else None
        poisk.append(f"указан явно: {app_override}"
                     + ("" if app_path else " — такого файла нет"))
    else:
        app_path = browser.find_upjers(poisk)

    if app_path:
        _head(script_name, "приложение upjers Home")

        # Электронные приложения оставляют процессы жить и после закрытия окна:
        # главный, отрисовка, графика, служебные. Пока жив хоть один, замок на
        # один экземпляр не даст новому запуску открыть отладочный порт —
        # и помощник не появится. Поэтому прибираем сами, по просьбе владельца.
        # Электронные приложения оставляют процессы жить и после закрытия
        # окна: главный, отрисовка, графика, служебные. Пока жив хоть один,
        # замок на один экземпляр не даст новому запуску открыть отладочный
        # порт — и помощник не появится.
        busy = browser.upjers_running()
        proc = None
        if busy:
            # СНАЧАЛА ПРОБУЕМ ПОДКЛЮЧИТЬСЯ, А НЕ ЗАКРЫВАТЬ.
            # Если приложение открывали мы, его отладочный порт уже открыт —
            # помощнику нужен именно порт, а не собственный процесс. Тогда
            # ни закрывать, ни открывать заново не надо, и вход в игру цел.
            # Раньше в этом месте программа просто отказывалась работать.
            # Явное указание сильнее умолчания: если владелец сам попросил
            # `--force-close`, подключаться не пытаемся. Иначе этим ключом
            # нельзя было бы выправить приложение, которое работает, порт
            # отдаёт, а показывает ерунду — а это и есть случай, ради
            # которого он существует.
            already = None if force_close else browser.debug_port_of(busy)
            if already:
                _say(f"  приложение: {app_path}")
                _say()
                _say(f"  Приложение уже открыто — подключаюсь к нему.")
                _say(f"  ({len(busy)} процессов — это один запуск, у таких")
                _say("  приложений их всегда несколько.)")
                _say("  Закрывать и открывать заново не нужно: вход в игру цел.")
                _say()
                proc = browser.AttachedApp(already)
                port = already
                # Показать окно ОБЯЗАТЕЛЬНО. Приложение прячется в трей, своего
                # окна у программы нет, консоли тоже — без этого человек
                # запускает помощника и не видит ровно ничего. Владелец так и
                # написал: «открыл приложение и ничего не произошло», хотя всё
                # отработало.
                # Показать окно ИЛИ сказать словами — обязательно.
                # Спрятанное в трей окно снаружи не достаём: приложение
                # прячет его своей логикой, и вытащенное силой остаётся
                # разобранным (рамка во весь экран, отрисовка старого
                # размера). Владелец такое и увидел 2026-09-10.
                gde = browser.show_app_window(busy)
                if gde == "vpered":
                    _say("  Окно игры вывел вперёд.")
                elif gde == "v-tree":
                    _say("  Приложение свёрнуто в трей — окно оттуда не достаю:")
                    _say("  вытащенное силой, оно рисуется криво. Откройте его")
                    _say("  значком upjers у часов; помощник уже работает.")
                    show_note(
                        "Помощник подключился к игре и уже работает." + chr(10) + chr(10)
                        + "Окно игры свёрнуто в трей — откройте его" + chr(10)
                        + "значком upjers у часов, внизу справа."
                    )
                else:
                    _say("  Окно приложения найти не удалось: откройте его")
                    _say("  сами значком upjers у часов. Помощник уже работает.")
                _say()
            elif not force_close:
                # ПОЧЕМУ НЕ ЗАКРЫВАЕМ САМИ.
                # Приложение прячется в трей, а не выходит: штатно его не
                # выключить, остаётся только убить. Но убитый Electron НЕ
                # успевает сохранить вход — проверено 2026-09-09: после этого
                # игра встречает окном входа, а сервер игры отдаёт пустую
                # страницу. Терять вход игрока при каждом запуске нельзя.
                #
                # ПОЧЕМУ ЖДЁМ, А НЕ ВЫХОДИМ. Раньше здесь программа
                # показывала ошибку и завершалась: «закройте и запустите
                # помощника снова». Для игрока это лишний круг, на котором
                # легко запутаться. Теперь окно ждёт: закрыл приложение —
                # нажал «ОК» — помощник открывает его сам, уже со своим
                # портом. Второго запуска не нужно.
                _say(f"  Приложение upjers Home уже работает ({len(busy)} процессов —")
                _say("  это один запуск, у таких приложений их всегда несколько).")
                _say()
                _say("  Подключиться к нему не выходит: его запускали не мы, и")
                _say("  отладочного порта у него нет — встроиться некуда.")
                _say("  Прошу закрыть его через значок у часов и жду «ОК».")
                show_wait(
                    "Игра уже открыта, но запускали её не через помощника," + chr(10)
                    + "и подключиться к ней он не может." + chr(10) + chr(10)
                    + "Закройте её: правой кнопкой по значку upjers" + chr(10)
                    + "у часов (внизу справа) → «Выход»." + chr(10) + chr(10)
                    + "Потом нажмите ОК — помощник откроет игру сам."
                )
                # Приложению нужно несколько секунд, чтобы действительно выйти.
                for _ in range(20):
                    busy = browser.upjers_running()
                    if not busy:
                        break
                    time.sleep(0.75)
                if busy:
                    _say(f"  После «ОК» приложение всё ещё работает ({len(busy)} процессов).")
                    show_error(
                        "Игра всё ещё открыта." + chr(10) + chr(10)
                        + "Закройте её через значок upjers у часов" + chr(10)
                        + "(правой кнопкой → «Выход») и запустите" + chr(10)
                        + "помощника ещё раз."
                    )
                    return 6
                _say("  Приложение закрыто. Открываю его сам.")
                _say()
            else:
                _say(f"  Закрываю приложение принудительно ({len(busy)} процессов).")
                _say("  ВНИМАНИЕ: вход в игру при этом теряется — так устроено")
                _say("  приложение, и это не наша ошибка, но и не наше право по умолчанию.")
                closed = browser.close_upjers()
                left = browser.upjers_running()
                if left:
                    _say(f"  ОШИБКА: {len(left)} процессов закрыть не удалось.")
                    return 6
                _say(f"  Закрыто процессов: {closed}. Открываю заново.")
                _say()

        if proc is None:
            _say(f"  приложение: {app_path}")
            _say()
            _say("  Запускаю приложение…")
            proc, port = browser.launch_app(app_path, port=port)
    else:
        if target != "browser":
            # БЕЗ ТИХОГО CHROME. Раньше `auto` без приложения молча открывал
            # отдельный браузер — пустое окно, в котором игрок войти в игру
            # не умеет: его вход живёт в приложении. Первая живая проверка
            # 2026-09-11 закончилась ровно так: «открылось окно, где ничего
            # не было». Теперь говорим прямо, где искали, и останавливаемся.
            # Отдельный браузер остаётся только по явному `--target browser`.
            _say("  ОШИБКА: приложение upjers Home на этом компьютере не найдено.")
            _say("  Где искал:")
            for line in poisk:
                _say("    " + line)
            _say("  Помощник работает только через приложение: в нём ваш вход в игру.")
            show_error(
                "Не нашёл приложение upjers Home на этом компьютере." + chr(10) + chr(10)
                + "Помощник работает только через него:" + chr(10)
                + "в нём сохранён ваш вход в игру." + chr(10) + chr(10)
                + "Где искал:" + chr(10)
                + chr(10).join("  " + line for line in poisk) + chr(10) + chr(10)
                + "Если игра у вас установлена — откройте её сами," + chr(10)
                + "дождитесь сада и запустите помощника ещё раз:" + chr(10)
                + "он найдёт открытую игру и запомнит, где она."
            )
            return 3
        try:
            exe = chrome_path or browser.find_chrome()
        except browser.BrowserNotFound as exc:
            _say(f"  ОШИБКА: {exc}")
            return 3

        _head(script_name, url)
        _say(f"  Chrome:  {exe}")
        _say(f"  профиль: {browser.profile_dir()}")
        _say()
        _say("  Запускаю браузер…")
        proc, port = browser.launch(url, chrome=exe, port=port, profile=profile,
                                    host_rules=host_rules)

    try:
        cdp.wait_for_port(port, timeout=60)
    except cdp.CdpTimeout as exc:
        _say(f"  ОШИБКА: {exc}")
        _say()
        _say("  Чаще всего это значит, что окно помощника уже открыто.")
        _say("  Закройте прежнее окно браузера и запустите заново.")
        proc.terminate()
        return 4

    tabs: dict[str, cdp.Cdp] = {}
    measured: set[str] = set()
    opened: dict[str, dict] = {}
    states: dict[str, str] = {}
    straight = bool(app_path and go_straight)
    where = "приложение" if app_path else "окно браузера"
    _say("  Готово. Помощник появится в игре сам —")
    _say("  на любой странице и после каждой перезагрузки.")
    _say()
    if straight:
        _say("  Захожу в «Садовую империю» сам — ждать выбора игры не нужно.")
    else:
        _say("  Дальше: войдите в игру как обычно и откройте свой сад.")
    _say(f"  Закройте {where}, когда закончите.")
    _say()

    try:
        while proc.poll() is None:
            _sync_tabs(port, tabs, source, measured, opened, straight, states)
            for conn in list(tabs.values()):
                try:
                    conn.drain()
                except Exception:
                    pass
            time.sleep(1.0)
    except KeyboardInterrupt:
        _say()
        _say("  Останавливаюсь по Ctrl+C…")
        proc.terminate()
    finally:
        for conn in tabs.values():
            conn.close()

    _say("  Закрыто. До встречи.")
    return 0


MEASURE_JS = r"""
(function () {
  var strip = document.getElementById('wimpareaDiv');
  if (!strip) return null;
  var s = strip.getBoundingClientRect();
  var out = ['ПОЛОСА ПОМОЩНИКОВ #wimpareaDiv: ' +
             Math.round(s.width) + '×' + Math.round(s.height)];
  function bg(e) {
    var b = getComputedStyle(e).backgroundImage || '';
    var m = /\/([^\/\)"']+\.(?:gif|png|jpg))/i.exec(b);
    return m ? m[1] : '';
  }
  var all = strip.querySelectorAll('*');
  for (var i = 0; i < all.length && i < 60; i++) {
    var e = all[i], r = e.getBoundingClientRect();
    if (!r.width && !r.height) continue;
    out.push('  ' + e.tagName.toLowerCase() +
      (e.id ? '#' + e.id : '') +
      (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).join('.') : '') +
      '  x=' + Math.round(r.left - s.left) +
      ' y=' + Math.round(r.top - s.top) +
      ' ' + Math.round(r.width) + '×' + Math.round(r.height) +
      (bg(e) ? '  [' + bg(e) + ']' : ''));
  }
  // Наши гномы — по одному: общая накладка лежит на всю полосу и о
  // расстановке ничего не говорит.
  var mine = document.querySelectorAll('#si-helper [data-si-button]');
  for (var k = 0; k < mine.length; k++) {
    var mr = mine[k].getBoundingClientRect();
    out.push('  НАШ ' + mine[k].getAttribute('data-si-button') +
             '  x=' + Math.round(mr.left - s.left) +
             ' y=' + Math.round(mr.top - s.top) +
             ' ' + Math.round(mr.width) + '×' + Math.round(mr.height));
  }
  if (!mine.length) out.push('  НАШИХ ГНОМОВ НА СТРАНИЦЕ НЕТ');
  return out.join(String.fromCharCode(10));
})()
"""


CLICK_GAME_JS = r"""
(function () {
  if (!/upjers\.com$/i.test(location.hostname)) return '';   // не портал — не мешаем

  // На «Моих играх» вход в игру идёт ссылкой вида /play/<номер>. Номер свой у
  // каждого игрока, поэтому ищем по названию, а не по адресу. Именно эта
  // ссылка передаёт вход из портала в игру — без неё игра встречает паролем.
  var want = /садовая\s*импери|molehill|wurzel/i;
  var links = document.querySelectorAll('a[href*="/play/"]');
  for (var i = 0; i < links.length; i++) {
    var a = links[i];
    var text = (a.textContent || '') + ' ' + (a.getAttribute('title') || '');
    if (!want.test(text)) continue;
    a.removeAttribute('target');    // пусть откроется в этом же окне
    a.click();
    return 'вхожу в игру: ' + (a.textContent || '').trim().slice(0, 30)
           + ' (' + a.getAttribute('href') + ')';
  }

  // Ссылок нет — возможно, мы на другой странице портала.
  if (!/\/my-games/i.test(location.pathname)) return 'НУЖНЫ-МОИ-ИГРЫ';
  return '';
})()
"""

MY_GAMES_URL = "https://ru.upjers.com/my-games"

# Узлы самой игры. Список тот же, что у границы в `panel.js`: помощник
# работает только на них, и заходить заново имеет смысл тоже только на них.
UZLY_IGRY = ("molehillempire", "sadowajaimperija", "wurzelimperium")


def _na_uzle_igry(adres: str) -> bool:
    low = adres.lower()
    return any(u in low for u in UZLY_IGRY)


def _open_game_once(conn, opened: dict, tid: str, enabled: bool) -> None:
    """Уводит окно приложения со списка игр прямо в «Садовую империю».

    ПОЧЕМУ НАЖАТИЕМ, А НЕ ПЕРЕХОДОМ ПО АДРЕСУ.
    Сперва я просто переводил окно на `https://ru.molehillempire.com/` — адрес
    был взят с ПУБЛИЧНОЙ страницы портала, где никто не вошёл. У upjers вход
    единый, и переход в игру несёт с собой сессию; голый переход её терял, и
    владелец вместо своего сада попадал на окно входа.
    Поэтому делаем ровно то же, что делает человек: находим на странице ссылку
    в игру и нажимаем её.

    ЗАПАСНОЙ ПУТЬ — ПЕРЕХОД ПО ТОЙ ЖЕ ССЫЛКЕ.
    Нажатие иногда не уводит со страницы вовсе: поймано на живой игре
    2026-09-10 дважды, и оба раза окно так и оставалось на «Моих играх».
    Поэтому нажатие больше не считается делом сделанным: если через несколько
    секунд мы всё ещё на портале, переходим по адресу ЭТОЙ ЖЕ ссылки.
    Вход при этом не теряется — `/play/<номер>` и есть передача входа
    (доводит до `port_logw.php` и дальше в сад, проверено). Терял его другой
    адрес, публичный `ru.molehillempire.com`, — вот его брать нельзя.

    Пробуем несколько раз: список игр подгружается не мгновенно. Как только
    ушли с портала — больше не вмешиваемся.
    """
    if not enabled:
        return
    state = opened.setdefault(
        tid, {"tries": 0, "done": False, "went": False, "ssylka": "", "kogda": 0.0}
    )
    if state["done"] or state["tries"] > 25:
        return
    state["tries"] += 1

    try:
        here = str(conn.evaluate("location.href") or "")
    except Exception:
        return
    if not here or here == "about:blank":
        return
    if "upjers.com" not in here:
        # Мы НЕ на портале. Обычно это значит «уже в игре» — но не всегда.
        # После потери игровой сессии сервер отдаёт по тому же адресу ПУСТУЮ
        # страницу: ни разметки, ни `gardenjs`. Внешне это не окно пароля, и
        # принять её за игру легко — я и принял 2026-09-10. Поэтому смотрим
        # не на адрес, а на присутствие самой игры, и если её нет — заходим
        # заново через «Мои игры».
        if not _na_uzle_igry(here):
            state["done"] = True
            return
        try:
            est_igra = bool(conn.evaluate("typeof window.gardenjs !== 'undefined'"))
        except Exception:
            return
        if est_igra:
            state["done"] = True
            return
        state["pusto"] = state.get("pusto", 0) + 1
        if state["pusto"] >= 8 and not state["went"]:
            state["went"] = True
            try:
                conn.call("Page.navigate", {"url": MY_GAMES_URL}, timeout=20)
                _say("  Игра отдала пустую страницу — захожу заново.")
            except Exception:
                pass
        return

    # Уже нажимали, а мы всё ещё на портале — значит нажатие не увело.
    # Второй раз не нажимаем: идём по адресу той же ссылки.
    if state["ssylka"]:
        if time.monotonic() - state["kogda"] < 6.0:
            return
        adres = state["ssylka"]
        state["ssylka"] = ""
        if adres.startswith("/"):
            adres = "https://ru.upjers.com" + adres
        try:
            conn.call("Page.navigate", {"url": adres}, timeout=20)
            _say("  Нажатие не увело со страницы — захожу по ссылке напрямую.")
        except Exception:
            pass
        return

    try:
        result = str(conn.evaluate(CLICK_GAME_JS) or "")
    except Exception:
        return

    if result == "НУЖНЫ-МОИ-ИГРЫ":
        # Портал открылся не на «Моих играх» — переходим туда и на следующем
        # круге нажмём ссылку в игру.
        if state["went"]:
            return
        state["went"] = True
        try:
            conn.call("Page.navigate", {"url": MY_GAMES_URL}, timeout=20)
            _say("  Открываю «Мои игры»…")
        except Exception:
            pass
        return

    if result:
        # НЕ ставим "done": нажатие могло не сработать. Уход с портала мы
        # заметим сами наверху — по адресу окна.
        m = re.search(r"\((/[^)]+)\)\s*$", result)
        state["ssylka"] = m.group(1) if m else ""
        state["kogda"] = time.monotonic()
        _say("  " + result)


def _measure_once(conn, printed: set, tid: str) -> None:
    """Печатает устройство полосы помощников — один раз на вкладку.

    Расставлять помощников вслепую не выходит: игру агент не видит, а игрок
    тратит заход на каждый промах. Программа сама снимает мерку с настоящей
    страницы и печатает её сюда — остаётся скопировать и прислать.
    """
    if tid in printed:
        return
    try:
        text = conn.evaluate(MEASURE_JS)
    except Exception:
        return
    if not text:
        return
    printed.add(tid)
    _say()
    _say("  ── ЗАМЕР ПОЛОСЫ ПОМОЩНИКОВ (скопируйте и пришлите) ──")
    for line in str(text).splitlines():
        _say("  " + line)
    _say("  ── конец замера ──")
    _say()


STATE_JS = r"""
(function () {
  var s = window.SI_HELPER;
  if (s && s.sostoyanie) return String(s.sostoyanie);
  if (typeof window.gardenjs === 'undefined') return 'страница без игры: ' + location.hostname;
  return 'игра есть, помощника на странице нет';
})()
"""


def _report_state(conn, states: dict, tid: str) -> None:
    """Пишет в журнал, что панель сама о себе говорит, — когда это меняется.

    Зачем. Единственное, что возвращается с чужой машины, — журнал. Пока в
    нём был только замер полосы, по нему нельзя было понять главного:
    появился ли помощник, спрятался ли и почему, сколько садов увидел.
    Панель теперь выкладывает своё состояние в `window.SI_HELPER`, а
    программа записывает его при каждой перемене.
    """
    try:
        text = str(conn.evaluate(STATE_JS) or "").strip()
    except Exception:
        return
    if not text or states.get(tid) == text:
        return
    states[tid] = text
    _say(f"  панель: {text}")


def _sync_tabs(port: int, tabs: dict, source: str, printed: set | None = None,
               opened: dict | None = None, go_straight: bool = False,
               states: dict | None = None) -> None:
    """Держит вставку во ВСЕХ вкладках, а не в одной.

    Вставка `addScriptToEvaluateOnNewDocument` живёт только в той вкладке, к
    которой подключились, и только пока подключение открыто. А вход через
    портал upjers уводит на другой адрес и нередко открывает новую вкладку —
    в неё помощник просто не попадал. Поэтому раз в секунду смотрим список
    вкладок: в новые вставляем код, за закрытыми прибираем.
    """
    try:
        targets = cdp.list_targets(port)
    except Exception:
        return  # браузер закрывается — на следующем круге разберёмся

    alive = set()
    for t in targets:
        # `page` — обычная вкладка и оболочка приложения; `webview` — то, во
        # что приложение upjers Home грузит саму игру. Без второго типа
        # помощник в приложении не появится вовсе.
        if t.get("type") not in ("page", "webview") or not t.get("webSocketDebuggerUrl"):
            continue
        tid = t.get("id")
        if not tid:
            continue
        alive.add(tid)
        if tid in tabs:
            continue
        try:
            conn = cdp.Cdp(t["webSocketDebuggerUrl"], timeout=10)
            conn.inject_on_every_load(source)
            tabs[tid] = conn
        except Exception:
            pass  # вкладка могла закрыться прямо сейчас — не беда

    if opened is not None:
        for tid, conn in list(tabs.items()):
            _open_game_once(conn, opened, tid, go_straight)

    if printed is not None:
        for tid, conn in list(tabs.items()):
            _measure_once(conn, printed, tid)

    if states is not None:
        for tid, conn in list(tabs.items()):
            _report_state(conn, states, tid)

    for tid in list(tabs):
        if tid not in alive:
            tabs.pop(tid).close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="si-helper",
        description="Помощник для браузерной игры «Садовая империя».",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="показать версию и выйти",
    )
    parser.add_argument("--url", default=GAME_URL, help="адрес, который открыть")
    parser.add_argument(
        "--script",
        default="panel",
        choices=sorted(SCRIPTS),
        help="какой код вставлять в страницу",
    )
    parser.add_argument("--chrome", type=Path, default=None, help="путь к chrome.exe")
    parser.add_argument(
        "--app",
        type=Path,
        default=None,
        help="путь к «upjers Home.exe», если помощник сам его не находит",
    )
    parser.add_argument(
        "--no-straight",
        action="store_true",
        help="не заходить в игру самим: оставить портал upjers как есть",
    )
    parser.add_argument(
        "--force-close",
        action="store_true",
        help="закрывать работающее приложение принудительно (ТЕРЯЕТ ВХОД В ИГРУ)",
    )
    parser.add_argument(
        "--target",
        choices=("auto", "app", "browser"),
        default="auto",
        help="куда селиться: auto — приложение upjers, если есть, иначе браузер",
    )
    parser.add_argument(
        "--host-rules",
        default=None,
        help="подмена разрешения имён для проверок (--host-resolver-rules браузера)",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=None,
        help="своя папка профиля браузера (нужна для независимых проверок)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="номер отладочного порта (по умолчанию свободный; нужен для проверок)",
    )
    args = parser.parse_args(argv)

    if args.version:
        # Зачем это нужно. Файл раздаётся страницей релиза, и «та ли версия
        # скачалась» надо уметь спросить у самого файла, а не сверять хеши
        # руками. Флаг набирают в командной строке — туда и отвечаем.
        #
        # Окном НЕ отвечаем, хотя соблазн был: у программы нет консоли, и
        # первая версия показывала окно, если её нет. Оно оказалось модальным
        # и повисло навсегда, потому что нажать «ОК» было некому. Двойным
        # щелчком доводы всё равно не передашь.
        #
        # ВАЖЕН ПОРЯДОК: это стоит ДО `setup_output()`. Тот, не найдя
        # консоли, уводит весь вывод в файл журнала — и ответ уходил бы
        # туда же, заодно затирая журнал работающего помощника. Поймано
        # сразу же: `--version` молчал, а строка оказывалась в журнале.
        print(f"Садовый помощник {__version__}")
        return 0

    setup_output()


    # Один помощник за раз — но только когда он ведёт ПРИЛОЖЕНИЕ upjers Home.
    # Замок затем и нужен, что двое ведущих уводят приложение в игру каждый
    # по-своему и получаются два игровых окна. Запуск со своим браузером и
    # своей папкой профиля (`--target browser`) не мешает никому: он ведёт
    # собственный Chrome. Без этой оговорки проверка сборки не могла
    # запуститься, пока у игрока открыт помощник, — поймано 2026-09-10.
    if args.target != "browser" and not take_single_run():
        _say()
        _say("  Помощник уже запущен — второй раз открывать не нужно.")
        _say("  Если кажется, что он не работает, закройте прежний запуск")
        _say("  и откройте снова.")
        show_error(
            "Помощник уже запущен." + chr(10) + chr(10)
            + "Второй раз открывать его не нужно: два помощника" + chr(10)
            + "мешают друг другу и открывают лишние окна игры."
        )
        return 7

    return run(args.url, args.script, args.chrome, args.port, args.profile,
               args.host_rules, args.target, not args.no_straight, args.force_close,
               args.app)


if __name__ == "__main__":
    sys.exit(main())
