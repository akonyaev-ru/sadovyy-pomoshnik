# Отчёт разведчика с живой игры — 2026-09-09

Снят программой (`.exe` 0.1.0, разведчик 0.2.0) на **тестовом** аккаунте
владельца. Сервер `s5.ru.molehillempire.com`, страница `main.php?page=garden`.

> ⚠️ Аккаунт тестовый и, вероятно, низкого уровня. Часть возможностей игры
> может быть ещё не открыта — отсутствие чего-то в отчёте не всегда значит, что
> этого нет в игре вообще.

## Что подтвердилось

- **Программа работает на живой игре.** Панель появилась сама, значит вставка
  через `Page.addScriptToEvaluateOnNewDocument` дошла до настоящей страницы.
  `менеджер скриптов: не определился` — верно, код доставлен не Tampermonkey.
- Адрес: `https://s<N>.ru.molehillempire.com/main.php?page=garden`.
  Шаблон `@match https://*.molehillempire.com/*` сработал.
- Проверка `page('page=garden')` из кода 2012 года по-прежнему осмысленна.

## Объекты игры на странице — 10 из 23

| Объект | Что это | Было в 2012 |
|---|---|---|
| `ajax` | 8 свойств: `token`, `setToken`, `request`, `error`, `evalScript`, `showMessage`, `updatePlayer`, `toPayment` | да, `ajax.request` перехватывали |
| `garten` | 452 ключа — состояние сада | да |
| `regal` | 50 ключей — полка с семенами | да |
| `data_products` | 522 ключа — справочник овощей | нет (новое) |
| `watergarden` | 48 ключей — **водный сад есть** | нет |
| `level` | 437 ключей | нет |
| `selectMode` | функция, 4 аргумента (в 2012 звали с тремя) | да |
| `selected` | строка `"2"` — выбранный режим | да |
| `waehleGarten` | функция, 1 аргумент — переключение садов | нет |
| `ajaxRequestCommon` | функция, 5 аргументов | да |

**Не нашлись:** `showAutoPlant`, `startAutoPlant`, `cache_me`, `show_built`,
`garten_prod`, `garten_kategorie`, `helfer_all`, `ajaxRequest`, `gardenId`,
`maxGarden`, `user`, `money`, `lang`. Часть из них в 2012 жила внутри фрейма
сада; сейчас фреймов с игрой нет, и они, вероятно, спрятаны внутрь `garten`
(452 ключа) или `gardenjs`.

## Главная неизвестность: адресация клеток

```
клеток вида #b1..#b204 нет — адресация изменилась
подсказка: img[id^="b"] → 2 шт.
```

Схема 2012 года (`#b1` … `#b204` плюс проверка картинки курсора) **больше не
работает**. Это единственное, что мешает написать полив.

Зацепки из списка глобальных имён: `berechneFelder` («посчитать поля»),
`gardenjs`, `Field`, `Decogarden` / `decogarden` / `decogarden2`,
`farmchecker`, `farmchecker_types`.

## Чем игра написана

**Prototype.js + Scriptaculous** — из глобальных имён видны `$`, `$$`, `$A`,
`$F`, `$H`, `$R`, `$w`, `Class`, `Ajax`, `Enumerable`, `Draggable`, `Droppables`,
`Effect`, `Insertion`, `Position`, `Sortable`, `Template`, `Try`.
Всего игра выложила на страницу **198 функций и 235 объектов**.

Подключённые скрипты:

```
js/scriptaculous/prototype.js?v=20260908104316
js/scriptaculous/scriptaculous_combined.js?v=20260908104316
js/wurzel_all.js?v=20260908104316        ← основной код игры
https://up-cookiemon.wavecdn.net/?puregameid=2&lang=ru
https://up-portal-assets.wavecdn.net/assets/_js/toolbar.js
```

Метка версии `20260908` — игру правили 2026-09-08, то есть она живая и
поддерживается.

## Фреймы

Восемь штук, но игры в них нет: `greenhouse`, `abfalleimer`, `tinyframe`,
`stadtframe`, `notizblock`, `multiframe`, `videoIframe` — все `about:blank`
(создаются заранее, наполняются по необходимости), а `adserveriframe` —
реклама. **Сад целиком на главной странице.**

Побочное наблюдение: разведчик прождал загрузки фреймов все отведённые 5000 мс,
потому что пустые `about:blank` он считал незагруженными. Для будущих версий —
не ждать фреймы без осмысленного `src`.
