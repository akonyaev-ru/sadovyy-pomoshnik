"""Минимальный клиент Chrome DevTools Protocol.

Зачем свой, а не Selenium или Playwright: нужны ровно четыре команды, а те
тянут за собой десятки мегабайт, которые потом лягут в `.exe` и добавят
поводов для ложных срабатываний антивируса.

Из внешнего нужен только `websocket-client`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

import websocket


class CdpError(RuntimeError):
    """Браузер ответил ошибкой на команду."""


class CdpTimeout(RuntimeError):
    """Браузер не ответил за отведённое время."""


def wait_for_port(port: int, timeout: float = 60.0) -> list[dict]:
    """Ждёт, пока Chrome поднимет отладочный порт, и возвращает список вкладок.

    Chrome открывает порт не мгновенно, поэтому обращаемся к нему в цикле,
    а не один раз сразу после запуска.

    Срок был 20 секунд — хватало на рабочей машине, где Chrome уже прогрет.
    На чистой машине сборки первый запуск холодный: 2026-09-10 выпуск
    остановился на первой же проверке, `Chrome не открыл отладочный порт за
    20 с`, при 78 прошедших. Ожидание не «поблажка проверке»: цикл выходит,
    как только порт отвечает, и на прогретой машине ничего не замедляет.
    """
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return list_targets(port)
        except (urllib.error.URLError, OSError, ConnectionError) as exc:
            last = exc
            time.sleep(0.15)
    raise CdpTimeout(
        f"Chrome не открыл отладочный порт {port} за {timeout:.0f} с. "
        f"Последняя ошибка: {last}"
    )


def list_targets(port: int) -> list[dict]:
    """Список открытых вкладок и служебных целей."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_page(port: int, timeout: float = 20.0) -> dict:
    """Находит первую настоящую вкладку (не расширение и не служебную)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for t in wait_for_port(port, timeout=timeout):
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                return t
        time.sleep(0.15)
    raise CdpTimeout("Chrome запустился, но ни одной вкладки так и не появилось")


class Cdp:
    """Соединение с одной вкладкой.

    Использование:
        with Cdp(target["webSocketDebuggerUrl"]) as cdp:
            cdp.call("Page.enable")
    """

    def __init__(self, ws_url: str, timeout: float = 20.0) -> None:
        # suppress_origin обязателен. Без него websocket-client подставляет
        # заголовок Origin, а Chrome отклоняет отладочное подключение с чужим
        # origin: «Rejected an incoming WebSocket connection… 403 Forbidden».
        # Обходной путь из большинства советов — запускать браузер с
        # --remote-allow-origins=*, но это расширяет его политику ради нашего
        # удобства. Не слать лишний заголовок — дешевле и безопаснее.
        self._ws = websocket.create_connection(
            ws_url,
            timeout=timeout,
            max_size=64 * 1024 * 1024,
            suppress_origin=True,
        )
        self._next_id = 0
        self._events: list[dict] = []

    # ── жизненный цикл ────────────────────────────────────────────────
    def __enter__(self) -> "Cdp":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:  # соединение и так рвётся — молчим осознанно
            pass

    # ── команды ───────────────────────────────────────────────────────
    def call(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        """Шлёт команду и ждёт ответ именно на неё.

        В сокет вперемешку приходят события, поэтому чужие сообщения
        складываются в `self._events`, а не отбрасываются: без этого ответ
        легко перепутать с событием.
        """
        self._next_id += 1
        msg_id = self._next_id
        self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._ws.settimeout(max(0.1, deadline - time.monotonic()))
            try:
                raw = self._ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            data = json.loads(raw)

            if data.get("id") != msg_id:
                self._events.append(data)
                continue

            if "error" in data:
                err = data["error"]
                raise CdpError(f"{method}: {err.get('message')} (код {err.get('code')})")
            return data.get("result", {})

        raise CdpTimeout(f"{method}: браузер не ответил за {timeout:.0f} с")

    # ── то, ради чего всё затевалось ──────────────────────────────────
    def inject_on_every_load(self, source: str) -> str:
        """Ставит скрипт, который выполняется при КАЖДОЙ загрузке страницы.

        Это и есть причина делать программу, а не закладку: закладку надо
        нажимать заново после каждой перезагрузки, а этот скрипт возвращается
        сам — как это делало расширение.

        Возвращает идентификатор, которым вставку можно снять.
        """
        self.call("Page.enable")
        res = self.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": source, "runImmediately": True},
        )
        return res.get("identifier", "")

    def remove_injection(self, identifier: str) -> None:
        self.call("Page.removeScriptToEvaluateOnNewDocument", {"identifier": identifier})

    def drain(self, budget: float = 0.05) -> int:
        """Вычитывает и выбрасывает накопившиеся события.

        Соединение с вкладкой держится открытым всё время работы: вставка
        `addScriptToEvaluateOnNewDocument` живёт только пока сессия
        подключена. Но браузер шлёт в это соединение события, и если их не
        забирать, буфер сокета переполняется и связь встаёт. Поэтому раз в
        секунду сливаем накопленное.
        """
        dropped = 0
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            self._ws.settimeout(0.01)
            try:
                self._ws.recv()
                dropped += 1
            except (websocket.WebSocketTimeoutException, OSError):
                break
            except websocket.WebSocketException:
                break
        return dropped

    def evaluate(self, expression: str, await_promise: bool = False, timeout: float = 20.0) -> Any:
        """Выполняет выражение на странице и возвращает его значение."""
        res = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
            timeout=timeout,
        )
        if "exceptionDetails" in res:
            details = res["exceptionDetails"]
            text = details.get("exception", {}).get("description") or details.get("text")
            raise CdpError(f"страница вернула ошибку: {text}")
        return res.get("result", {}).get("value")

    def navigate(self, url: str, timeout: float = 30.0) -> None:
        """Переходит по адресу и ждёт, пока страница действительно загрузится."""
        self.call("Page.enable")
        self.call("Page.navigate", {"url": url}, timeout=timeout)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._drain_for_event("Page.loadEventFired", deadline):
                return
        raise CdpTimeout(f"страница {url} не догрузилась за {timeout:.0f} с")

    def _drain_for_event(self, name: str, deadline: float) -> bool:
        """Ищет событие среди уже накопленных, иначе ждёт новое."""
        for i, ev in enumerate(self._events):
            if ev.get("method") == name:
                del self._events[i]
                return True
        self._ws.settimeout(max(0.1, deadline - time.monotonic()))
        try:
            data = json.loads(self._ws.recv())
        except websocket.WebSocketTimeoutException:
            return False
        if data.get("method") == name:
            return True
        self._events.append(data)
        return False
