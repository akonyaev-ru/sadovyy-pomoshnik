"""Собирает из разведчика закладку (bookmarklet) и страницу для её установки.

Зачем: закладка не требует ни Tampermonkey, ни любого другого расширения.
Пользователь открывает собранную страницу, перетаскивает ссылку на панель
закладок и нажимает её, находясь в игре.

Запуск:  python tools/build_bookmarklet.py
"""

from pathlib import Path
from urllib.parse import quote
import html
import re

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "razvedka.user.js"
OUT_PAGE = ROOT / "tools" / "ustanovka-zakladki.html"
OUT_RAW = ROOT / "tools" / "razvedka.bookmarklet.txt"


def strip_userscript_header(code: str) -> str:
    """Убирает блок ==UserScript== — в закладке он бессмыслен."""
    return re.sub(
        r"//\s*==UserScript==.*?//\s*==/UserScript==\s*",
        "",
        code,
        flags=re.DOTALL,
    )


def build() -> None:
    code = strip_userscript_header(SRC.read_text(encoding="utf-8"))

    # Полное кодирование: гарантирует, что ни один символ не разорвёт адрес.
    # Переводы строк сохраняются как %0A, поэтому строчные комментарии «//»
    # внутри кода остаются безопасными — без этого всё после «//» умерло бы.
    url = "javascript:" + quote(code, safe="")

    OUT_RAW.write_text(url, encoding="utf-8")

    page = PAGE_TEMPLATE.replace("__HREF__", html.escape(url, quote=True))
    page = page.replace("__SIZE__", f"{len(url):,}".replace(",", " "))
    OUT_PAGE.write_text(page, encoding="utf-8")

    print(f"код после чистки шапки: {len(code):>7} байт")
    print(f"адрес закладки:         {len(url):>7} символов")
    print(f"записано: {OUT_RAW.name}, {OUT_PAGE.name}")


PAGE_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>Разведка «Садовой империи» — установка закладки</title>
<style>
	body { margin:0; padding:28px; background:#12161c; color:#d6dee8;
		font:15px/1.6 system-ui, Segoe UI, sans-serif; }
	.wrap { max-width:640px; margin:0 auto; }
	h1 { font-size:20px; color:#2ea3a8; margin:0 0 4px; }
	.sub { color:#8b98a8; margin:0 0 24px; }
	ol { padding-left:22px; }
	li { margin:0 0 14px; }
	.drag { display:inline-block; margin:6px 0; padding:10px 22px; background:#2ea3a8;
		color:#08121a !important; font-weight:bold; border-radius:6px;
		text-decoration:none; cursor:grab; }
	.note { margin-top:26px; padding:14px 16px; background:#1b222b;
		border-left:3px solid #2ea3a8; border-radius:0 6px 6px 0; color:#a9b6c5; font-size:14px; }
	code { background:#1b222b; padding:2px 6px; border-radius:4px; color:#8fd6d9; }
	.small { color:#6e7d8d; font-size:13px; }
</style>

<div class="wrap">
	<h1>Разведка «Садовой империи»</h1>
	<p class="sub">Без расширений. Обычная закладка в браузере.</p>

	<ol>
		<li>Показать панель закладок, если её не видно: <code>Ctrl+Shift+B</code>.</li>
		<li><b>Перетащить мышью</b> синюю кнопку на панель закладок:<br>
			<a class="drag" href="__HREF__">Разведка</a><br>
			<span class="small">Именно перетащить, а не нажать здесь — здесь она ничего
			не покажет, потому что это не игра.</span></li>
		<li>Открыть игру и зайти в свой сад.</li>
		<li>Нажать закладку <b>«Разведка»</b> на панели.</li>
		<li>Справа сверху появится тёмная рамка с отчётом. Нажать
			<b>«Скопировать»</b> и прислать текст.</li>
	</ol>

	<div class="note">
		<b>Что делает эта закладка.</b> Только смотрит, что игра выкладывает на
		страницу, и показывает отчёт. Она не поливает, не сажает, не покупает,
		не продаёт и никуда ничего не отправляет — ни одного такого вызова в коде
		нет. Пароль ей не нужен и не виден.
	</div>

	<p class="small" style="margin-top:20px">Размер закладки: __SIZE__ символов.
		Если Chrome откажется её сохранять из-за длины — скажите, соберу
		укороченный вариант.</p>
</div>
"""


if __name__ == "__main__":
    build()
