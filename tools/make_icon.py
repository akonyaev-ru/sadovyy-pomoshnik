"""Делает значок программы из картинки-исходника.

Берёт PNG из `assets/`, приводит к квадрату, честно уменьшает до нужных
размеров и собирает `.ico`.

Почему всё вручную: `Pillow` в среде нет, а тащить её ради одной картинки
незачем — она осела бы и в сборке. Поэтому здесь свой разбор PNG (zlib плюс
снятие фильтров), своё усреднение при уменьшении и своя сборка ICO.

Зачем уменьшать самим, а не отдать Windows: значок показывается и в 16
пикселей — в списке задач, в заголовке окна. Растянутая туда большая картинка
мылится, а усреднение по площади держит форму.

Запуск:  python tools/make_icon.py [исходник.png]
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
OUT = ASSETS / "znachok.ico"
SIZES = (256, 128, 64, 48, 32, 16)


# ── чтение PNG ───────────────────────────────────────────────────────

def decode_png(data: bytes) -> tuple[int, int, bytearray]:
    """Возвращает ширину, высоту и пиксели RGBA подряд.

    Поддерживаются восьмибитные RGB и RGBA без чересстрочности — этого хватает
    для обычных значков. Всё прочее лучше честно отвергнуть, чем молча выдать
    мусор.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("это не PNG")

    width = height = 0
    colour = depth = 0
    idat = bytearray()
    i = 8
    while i < len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        tag = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + length]
        if tag == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or colour not in (2, 6) or interlace:
                raise ValueError(f"нужен 8-битный RGB или RGBA без чересстрочности "
                                 f"(здесь глубина {depth}, тип {colour}, чересстрочность {interlace})")
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        i += 12 + length

    channels = 4 if colour == 6 else 3
    raw = zlib.decompress(bytes(idat))
    stride = width * channels

    out = bytearray(width * height * 4)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]; pos += 1
        line = bytearray(raw[pos:pos + stride]); pos += stride

        # Снятие фильтров — как описано в спецификации PNG.
        if ftype == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 0xFF
        elif ftype == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif ftype == 3:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif ftype == 4:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                b = prev[x]
                c = prev[x - channels] if x >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 0xFF
        elif ftype != 0:
            raise ValueError(f"неизвестный фильтр строки: {ftype}")

        base = y * width * 4
        if channels == 4:
            out[base:base + stride] = line
        else:
            for x in range(width):
                out[base + x * 4:base + x * 4 + 3] = line[x * 3:x * 3 + 3]
                out[base + x * 4 + 3] = 255
        prev = line

    return width, height, out


# ── подготовка ───────────────────────────────────────────────────────

def to_square(px: bytearray, w: int, h: int, pad: float = 0.06) -> tuple[int, bytearray]:
    """Вписывает картинку в прозрачный квадрат с небольшими полями.

    Поля нужны, чтобы значок не упирался в края: Windows кое-где обрезает
    крайний пиксель, и без запаса лист выглядел бы подрезанным.
    """
    side = int(max(w, h) * (1 + pad * 2))
    out = bytearray(side * side * 4)
    ox, oy = (side - w) // 2, (side - h) // 2
    for y in range(h):
        src = y * w * 4
        dst = ((y + oy) * side + ox) * 4
        out[dst:dst + w * 4] = px[src:src + w * 4]
    return side, out


def resize(px: bytearray, side: int, size: int) -> bytearray:
    """Уменьшает квадрат усреднением по площади.

    Считаем в предумноженных на прозрачность значениях: иначе по краям листа
    подмешался бы цвет прозрачных пикселей и появилась бы тёмная кайма.
    """
    out = bytearray(size * size * 4)
    step = side / size
    for y in range(size):
        y0, y1 = int(y * step), max(int(y * step) + 1, int((y + 1) * step))
        for x in range(size):
            x0, x1 = int(x * step), max(int(x * step) + 1, int((x + 1) * step))
            r = g = b = a = n = 0
            for sy in range(y0, min(y1, side)):
                row = sy * side * 4
                for sx in range(x0, min(x1, side)):
                    i = row + sx * 4
                    alpha = px[i + 3]
                    r += px[i] * alpha
                    g += px[i + 1] * alpha
                    b += px[i + 2] * alpha
                    a += alpha
                    n += 1
            o = (y * size + x) * 4
            if a:
                out[o] = min(255, r // a)
                out[o + 1] = min(255, g // a)
                out[o + 2] = min(255, b // a)
                out[o + 3] = a // n
    return out


# ── запись ───────────────────────────────────────────────────────────

def png_bytes(size: int, px: bytearray) -> bytes:
    rows = [bytes(px[y * size * 4:(y + 1) * size * 4]) for y in range(size)]
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ico_bytes(images: list[tuple[int, bytes]]) -> bytes:
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = blobs = b""
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


def find_source() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    candidates = sorted(ASSETS.glob("*.png")) + sorted(ROOT.glob("*.png"))
    candidates = [c for c in candidates if c.name != OUT.with_suffix(".png").name]
    if not candidates:
        raise SystemExit(f"Не нашёл исходной картинки в {ASSETS} — положите туда .png")
    return candidates[0]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    src = find_source()
    print(f"исходник: {src.name}")
    w, h, px = decode_png(src.read_bytes())
    print(f"  разобран: {w}×{h}")

    side, square = to_square(px, w, h)
    print(f"  вписан в квадрат: {side}×{side}")

    images = []
    for size in SIZES:
        data = png_bytes(size, resize(square, side, size))
        images.append((size, data))
        print(f"  {size:>3}×{size:<3} — {len(data):>6} байт")

    ASSETS.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(ico_bytes(images))
    print(f"готово: {OUT.name} ({OUT.stat().st_size} байт)")

    preview = OUT.with_suffix(".png")
    preview.write_bytes(images[0][1])
    print(f"для просмотра: {preview.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
