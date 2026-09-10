"""Снимок стенда с панелью — для взгляда глазами.

Панель работает только на страницах игры, поэтому обычная панель браузера её
не покажет: `localhost` не игровой узел. Здесь браузер поднимается с подменой
разрешения имён, как в проверках, и снимок делается через отладочный протокол.

Запуск:  python tools/snapshot.py [имя_файла.png] [что_нажать] [что_добавить_в_адрес]
Пример:  python tools/snapshot.py vid.png Подсветить
Пример:  python tools/snapshot.py vodnyy.png "" wg=1
"""

from __future__ import annotations

import base64
import functools
import http.server
import shutil
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from si_helper import browser, cdp, payload  # noqa: E402

GAME_HOST = "s5.ru.molehillempire.com"
STAND = "tools/stand-game.html"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "vid.png"
    press = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
    extra = sys.argv[3] if len(sys.argv) > 3 else ""

    handler = functools.partial(QuietHandler, directory=str(ROOT))
    profile = Path(tempfile.mkdtemp(prefix="si-snap-"))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        web_port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        proc, port = browser.launch(
            "about:blank", headless=True, profile=profile,
            host_rules=f"MAP {GAME_HOST} 127.0.0.1",
        )
        try:
            target = cdp.find_page(port)
            with cdp.Cdp(target["webSocketDebuggerUrl"]) as conn:
                conn.inject_on_every_load(payload.load("panel.js"))
                query = "?panel=0" + (("&" + extra) if extra else "")
                conn.navigate(f"http://{GAME_HOST}:{web_port}/{STAND}{query}")
                time.sleep(2.0)   # даём картинкам игры догрузиться с хранилища

                # Ряд помощников живёт внизу; подводим его к глазам, иначе
                # снимок покажет пустой сад, а панель окажется за краем.
                # Листок попрошайки открывается так же, как его открывает
                # игрок, — её же `wimparea.show`.
                wimp = ""
                if "wimp=" in extra:
                    wimp = extra.split("wimp=")[1].split("&")[0]
                    conn.evaluate("wimparea.show(" + wimp + "); true")
                    time.sleep(1.0)

                # В водном саду смотреть надо на его собственное поле.
                if wimp:
                    anchor = "einkaufszettel"
                elif "wg=1" in extra:
                    anchor = "watergarden_container"
                else:
                    anchor = "wimpareaDiv"
                conn.evaluate(
                    "(function(){var e=document.getElementById('" + anchor + "');"
                    "if(e)e.scrollIntoView({block:'center'});})(); true"
                )
                time.sleep(1.2)

                if press:
                    conn.evaluate(
                        "(function(){var b=document.querySelectorAll("
                        "'#si-helper [data-si-button]');for(var i=0;i<b.length;i++){"
                        "if((b[i].title||'').indexOf('" + press + "')===0){b[i].click();return}}})()"
                    )
                    time.sleep(1.2)

                loaded = conn.evaluate(
                    "[].map.call(document.querySelectorAll('#si-helper [data-si-button]'),"
                    "function(e){return (e.getAttribute('src')||'').split('/').pop()"
                    "+(e.complete&&e.naturalWidth>0?' ✓':' ✗')})"
                )
                print("картинки кнопок:", loaded)

                shot = conn.call("Page.captureScreenshot", {"format": "png"}, timeout=30)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(base64.b64decode(shot["data"]))
                print(f"снимок: {out}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
            httpd.shutdown()
            shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
