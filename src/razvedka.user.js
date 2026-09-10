// ==UserScript==
// @name         Садовая империя — разведка
// @namespace    si-helper
// @version      0.2.0
// @description  Только смотрит и показывает отчёт. Ничего в игре не делает: не поливает, не сажает, не продаёт, никуда ничего не отправляет.
// @author       для восстановления помощника
// @match        https://*.molehillempire.com/*
// @match        https://molehillempire.com/*
// @match        https://*.sadowajaimperija.ru/*
// @match        https://sadowajaimperija.ru/*
// @match        https://*.wurzelimperium.de/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/*
 * ЧТО ЭТОТ СКРИПТ ДЕЛАЕТ
 *   Перечисляет, какие объекты игра выкладывает на страницу, и показывает
 *   отчёт в рамке справа сверху. Отчёт нужно скопировать кнопкой и прислать.
 *
 * ЧЕГО ОН НЕ ДЕЛАЕТ (проверяется чтением кода — здесь нет таких вызовов)
 *   Не нажимает на клетки, не поливает, не сажает, не покупает, не продаёт.
 *   Не отправляет ничего в сеть: ни fetch, ни XMLHttpRequest, ни ajax игры.
 *   Не читает и не трогает логин с паролем.
 *
 * ПРО ФРЕЙМЫ
 *   Tampermonkey запускает скрипт в каждом фрейме страницы. Чтобы не наплодить
 *   несколько панелей, отчёт рисует только верхнее окно — оно же обходит все
 *   доступные фреймы. Фрейм рисует свою панель лишь тогда, когда верхнее окно
 *   до него не дотягивается (чужой origin) и иначе его никто не осмотрит.
 */

(function () {
	'use strict';

	// ── дождаться готовности страницы ────────────────────────────────
	// Скрипт доставляется тремя путями, и момент запуска у них разный:
	//   Tampermonkey — document-idle (DOM готов),
	//   закладка     — по нажатию (страница давно готова),
	//   программа    — document-start, DOM ещё ПУСТ.
	// Третий случай ломал всё: ни body, ни фреймов, панель вешать некуда.
	// Поймано проверкой test_program_delivers_working_recon.
	if (document.readyState === 'complete') {
		boot();
	} else {
		window.addEventListener('load', function () { boot(); }, { once: true });
	}

	function boot() {

	// ── рисовать ли панель именно здесь ──────────────────────────────
	var isTop = (window.top === window.self);
	var topReachable = false;
	try { topReachable = !!(window.top && window.top.document); } catch (e) { topReachable = false; }
	if (!isTop && topReachable) return; // верхнее окно уже осмотрит нас само

	// ── дождаться загрузки фреймов ───────────────────────────────────
	// Дефект, найденный на стенде: на document-idle фрейм ещё пуст
	// (его document — about:blank), и осмотр честно возвращал «игра тут
	// ничего не выкладывает», хотя на самом деле фрейм просто не успел
	// загрузиться. Ждём фреймы с непустым src, но не дольше 5 секунд.
	var waited = 0;
	(function waitForFrames() {
		var pending = 0;
		document.querySelectorAll('frame, iframe').forEach(function (fe) {
			if (!fe.getAttribute('src')) return;
			try {
				var href = fe.contentWindow && fe.contentWindow.location.href;
				if (!href || href === 'about:blank') pending++;
			} catch (e) { /* чужой origin — дожидаться нечего */ }
		});
		if (pending && waited < 5000) { waited += 200; return setTimeout(waitForFrames, 200); }
		start();
	})();

	function start() {

	var out = [];
	var say = function (s) { out.push(s === undefined ? '' : String(s)); };
	var pad = function (s, n) { s = String(s); while (s.length < (n || 20)) s += ' '; return s; };

	// ── общее ────────────────────────────────────────────────────────
	say('=== РАЗВЕДКА «Садовая империя» ===');
	say('версия разведчика: 0.2.0');
	say('время: ' + new Date().toISOString());
	say('адрес: ' + location.href);
	say('заголовок: ' + document.title);
	say('это верхнее окно: ' + (isTop ? 'да' : 'НЕТ (верх недоступен)'));
	say('менеджер скриптов: ' + (typeof GM_info !== 'undefined' && GM_info.scriptHandler
		? GM_info.scriptHandler + ' ' + (GM_info.version || '') : 'не определился'));
	say('ждали загрузки фреймов: ' + waited + ' мс');
	say('');

	// ── собираем контексты: сама страница + все доступные фреймы ──────
	var W = (typeof unsafeWindow !== 'undefined') ? unsafeWindow : window;
	var contexts = [{ label: 'страница', win: W, doc: document }];

	var frameEls = document.querySelectorAll('frame, iframe');
	say('--- ФРЕЙМЫ (в разметке: ' + frameEls.length + ', в window.frames: ' + window.frames.length + ') ---');
	if (!frameEls.length) {
		say('  фреймов нет — игра живёт прямо на странице');
	}
	for (var i = 0; i < frameEls.length; i++) {
		var fe = frameEls[i];
		var name = fe.name || fe.id || ('фрейм#' + i);
		var reach = 'недоступен (чужой origin)';
		try {
			if (fe.contentWindow && fe.contentWindow.document) {
				reach = 'доступен';
				contexts.push({ label: 'фрейм ' + name, win: fe.contentWindow, doc: fe.contentWindow.document });
			}
		} catch (e) { }
		say('  [' + i + '] ' + pad(fe.tagName.toLowerCase(), 7) + ' name=' + pad(fe.name || '—', 12) +
			' ' + reach + '  src=' + (fe.getAttribute('src') || '—'));
	}
	say('');

	// ── что ищем ─────────────────────────────────────────────────────
	var known = [
		// современный набор (по скрипту Anbauhelfer)
		'ajax', 'ajaxRequest', 'data_products', 'waehleGarten', 'watergarden',
		// набор 2012 года (CupIvan 5.2)
		'regal', 'garten', 'selectMode', 'selected', 'showAutoPlant',
		'startAutoPlant', 'cache_me', 'show_built', 'ajaxRequestCommon',
		'garten_prod', 'garten_kategorie', 'helfer_all',
		// вероятные соседи
		'gardenId', 'maxGarden', 'user', 'money', 'level', 'lang'
	];

	var describe = function (v) {
		if (v === null) return 'null';
		var t = typeof v;
		if (t === 'function') return 'function(' + v.length + ' арг.)';
		if (t === 'object') {
			var n = 0;
			try { for (var k in v) { n++; if (n > 9999) break; } } catch (e) { return 'object (не читается)'; }
			return 'object, ключей: ' + n + (Array.isArray(v) ? ' (массив)' : '');
		}
		if (t === 'string') return 'string: ' + JSON.stringify(v.slice(0, 60));
		return t + ': ' + String(v).slice(0, 60);
	};

	// чистый список имён браузерного окна — чтобы вычесть его и увидеть игру
	var cleanKeys = {};
	try {
		var probe = document.createElement('iframe');
		probe.style.display = 'none';
		document.documentElement.appendChild(probe);
		for (var ck in probe.contentWindow) cleanKeys[ck] = 1;
		probe.remove();
	} catch (e) { }

	// ── обход каждого контекста ──────────────────────────────────────
	contexts.forEach(function (ctx) {
		var CW = ctx.win, CD = ctx.doc;
		say('════════ КОНТЕКСТ: ' + ctx.label + ' ════════');
		try { say('  адрес: ' + CW.location.href); } catch (e) { say('  адрес: не читается'); }

		// известные объекты
		var found = 0, lines = [];
		known.forEach(function (name) {
			var has = false, val;
			try { has = (name in CW); val = CW[name]; } catch (e) { }
			if (has && val !== undefined) { found++; lines.push('  ЕСТЬ  ' + pad(name) + describe(val)); }
		});
		say('  --- известные объекты игры: найдено ' + found + ' из ' + known.length + ' ---');
		if (lines.length) { lines.forEach(say); }
		else { say('    ни одного — игра тут ничего не выкладывает'); }

		// разбор ajax
		try {
			if (CW.ajax && typeof CW.ajax === 'object') {
				var keys = [];
				for (var k in CW.ajax) keys.push(k + ':' + (typeof CW.ajax[k]));
				say('  --- разбор ajax ---');
				say('    свойства: ' + (keys.join(', ') || '—'));
			}
			if (typeof CW.ajaxRequest === 'function') {
				say('    ajaxRequest — аргументов: ' + CW.ajaxRequest.length + ', первые строки:');
				String(CW.ajaxRequest).split('\n').slice(0, 6).forEach(function (l) {
					say('      | ' + l.trim().slice(0, 110));
				});
			}
		} catch (e) { say('    ошибка разбора ajax: ' + e.message); }

		// сетка сада
		var n = 0, sample = null;
		for (var id = 1; id <= 204; id++) {
			var el = null;
			try { el = CD.getElementById('b' + id); } catch (e) { }
			if (el) { n++; if (!sample) sample = el; }
		}
		say('  --- сетка сада ---');
		if (n) {
			say('    клеток #b1..#b204: ' + n + ' из 204');
			say('    пример: tag=' + sample.tagName.toLowerCase() +
				' src=…' + String(sample.src || sample.getAttribute('src') || '—').slice(-60));
		} else {
			say('    клеток вида #b1..#b204 нет — адресация изменилась');
			['#garden', '.gardenField', '.field', 'img[id^="b"]', 'td[onclick]', '[class*=garten]']
				.forEach(function (sel) {
					var c = 0;
					try { c = CD.querySelectorAll(sel).length; } catch (e) { }
					if (c) say('    подсказка: ' + sel + ' → ' + c + ' шт.');
				});
		}

		// прочие глобальные имена
		try {
			var fns = [], objs = [];
			for (var gk in CW) {
				if (cleanKeys[gk]) continue;
				var gv;
				try { gv = CW[gk]; } catch (e) { continue; }
				if (typeof gv === 'function') fns.push(gk);
				else if (gv && typeof gv === 'object') objs.push(gk);
			}
			fns.sort(); objs.sort();
			say('  --- прочие глобальные имена: функций ' + fns.length + ', объектов ' + objs.length + ' ---');
			say('    функции: ' + (fns.slice(0, 120).join(', ') || '—') + (fns.length > 120 ? ' …и ещё ' + (fns.length - 120) : ''));
			say('    объекты: ' + (objs.slice(0, 80).join(', ') || '—') + (objs.length > 80 ? ' …и ещё ' + (objs.length - 80) : ''));
		} catch (e) { say('    не удалось сравнить с чистым окном: ' + e.message); }

		// подключённые скрипты
		try {
			var srcs = [];
			CD.querySelectorAll('script[src]').forEach(function (s) { srcs.push(s.getAttribute('src')); });
			say('  --- подключённые скрипты (' + srcs.length + ') ---');
			srcs.slice(0, 25).forEach(function (s) { say('    ' + s); });
		} catch (e) { }

		say('');
	});

	say('=== КОНЕЦ ОТЧЁТА ===');

	// ── показать отчёт с кнопкой «скопировать» ───────────────────────
	var text = out.join('\n');

	var box = document.createElement('div');
	box.setAttribute('style', [
		'position:fixed', 'z-index:2147483647', 'top:10px', 'right:10px',
		'width:540px', 'max-height:82vh', 'display:flex', 'flex-direction:column',
		'background:#12161c', 'color:#d6dee8', 'border:2px solid #2ea3a8',
		'border-radius:8px', 'font:12px/1.45 Consolas,monospace',
		'box-shadow:0 8px 30px rgba(0,0,0,.5)'
	].join(';'));

	var head = document.createElement('div');
	head.setAttribute('style', 'padding:8px 10px;background:#1b222b;border-bottom:1px solid #2ea3a8;display:flex;gap:8px;align-items:center;flex:0 0 auto');
	head.innerHTML = '<b style="color:#2ea3a8;flex:1">Разведка · отчёт готов</b>';

	var pre = document.createElement('pre');
	pre.textContent = text;
	pre.setAttribute('style', 'margin:0;padding:10px;overflow:auto;flex:1 1 auto;white-space:pre-wrap;word-break:break-word;user-select:text');

	var btnCopy = document.createElement('button');
	btnCopy.textContent = 'Скопировать';
	btnCopy.setAttribute('style', 'cursor:pointer;padding:5px 12px;border:0;border-radius:5px;background:#2ea3a8;color:#08121a;font-weight:bold;font-family:inherit');
	btnCopy.onclick = function () {
		var ok = function () { btnCopy.textContent = 'Скопировано ✓'; };
		var fallback = function () {
			try {
				var r = document.createRange(); r.selectNodeContents(pre);
				var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
				if (document.execCommand('copy')) { ok(); return; }
			} catch (e2) { }
			btnCopy.textContent = 'Выделено — нажми Ctrl+C';
		};
		try {
			if (navigator.clipboard && navigator.clipboard.writeText) {
				navigator.clipboard.writeText(text).then(ok, fallback);
			} else { fallback(); }
		} catch (e) { fallback(); }
	};

	var btnClose = document.createElement('button');
	btnClose.textContent = '✕';
	btnClose.setAttribute('style', 'cursor:pointer;padding:5px 10px;border:0;border-radius:5px;background:#2a333f;color:#d6dee8;font-family:inherit');
	btnClose.onclick = function () { box.remove(); };

	head.appendChild(btnCopy);
	head.appendChild(btnClose);
	box.appendChild(head);
	box.appendChild(pre);
	(document.body || document.documentElement).appendChild(box);

	// продублируем в консоль — на случай, если рамку что-то перекроет
	try { console.log(text); } catch (e) { }

	} // конец start()

	} // конец boot()
})();
