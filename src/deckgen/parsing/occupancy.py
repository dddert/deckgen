"""Карта занятости под текстом: где текст может лежать, не налезая на графику, и какой под ним фон.

Прямоугольники фигур здесь не годятся (так ошибался и v1, и первая версия v2):
- карточка-картинка с 3D-шаром содержит рамку текста целиком, но шар занимает её нижнюю половину;
- кольцо диаграммы — прозрачная картинка: её рамка перекрывает цифру, а пиксели в центре пустые;
- паттерн обложки лежит в макете одной большой картинкой на полслайда.

Поэтому считаем по растру. Слои под текстом (фон слайда → декор макета → фигуры и картинки ниже по z,
с прозрачностью и обрезкой) сводятся в одну картинку ~480 px; по ней строятся карта краёв (лапласиан по
каналам: резкий край объекта даёт сильный отклик, плавный градиент и свечение — слабый) и карта верхнего
непрозрачного слоя. Над рамкой текста — сетка клеток ~5 px; клетка занята, если:
- поверх текста лежит непрозрачная графика;
- в клетке край объекта (паттерн обложки, обод шара на карточке, край кольца);
- это слой, в «дырке» которого стоит текст (кольцо диаграммы: пиксели в центре прозрачные);
- текст цвета text_color стал бы там нечитаемым (APCA), хотя в начале текста читается.
Бледный круг-подложка, зерно и мягкое свечение фона препятствием не считаются. Из точки привязки текста
наращиваем свободную область; рабочая рамка — наибольший прямоугольник этой области, содержащий точку
привязки. Цвет в точке привязки — фон для выбора цвета текста (APCA).
"""
from __future__ import annotations

from collections import OrderedDict, deque

from ..models import Box
from ..ooxml.colors import apca_lc, distance, hex_to_rgb, mix, rgb_to_hex
from ..ooxml.reader import PresentationReader, RShape

_RES = 480          # ширина растра слайда, px
_CELL = 5           # размер клетки сетки, px
_EDGE = 60          # отклик лапласиана (0..255), выше — край объекта; бледный круг-подложка ~30–50
_RANGE = 50         # ... и размах цвета в клетке (перцептивный, ~0..765): #EBF3F9 на белом ≈ 38 — не препятствие
_STEP = 45          # перепад средних цветов соседних клеток
_BG_AREA = 0.6      # фото/градиент на большую часть слайда — это фон, а не объект


# ============================================================================ точечные функции (аудит, отладка)

def _pixel(reader: PresentationReader, s: RShape, x: float, y: float) -> tuple[str, float] | None:
    """Цвет и непрозрачность пикселя картинки s в точке слайда (x, y)."""
    if not s.image_part or s.rot:
        return None
    im = reader.image(s.image_part)
    if im is None:
        return None
    u = (x - s.box.x) / max(1e-9, s.box.w)
    v = (y - s.box.y) / max(1e-9, s.box.h)
    if not (0 <= u <= 1 and 0 <= v <= 1):
        return None
    u, v = (1 - u if s.flip[0] else u), (1 - v if s.flip[1] else v)
    l, t, r, b = s.crop
    px = int(min(im.width - 1, max(0, (l + u * (1 - l - r)) * im.width)))
    py = int(min(im.height - 1, max(0, (t + v * (1 - t - b)) * im.height)))
    cr, cg, cb, ca = im.getpixel((px, py))
    return f"{cr:02X}{cg:02X}{cb:02X}", ca / 255


def _covers(s: RShape, x: float, y: float) -> bool:
    b = s.box
    if not b.contains_point(x, y):
        return False
    if s.geometry == "ellipse":
        rx, ry = b.w / 2, b.h / 2
        return ((x - b.cx) / max(rx, 1e-9)) ** 2 + ((y - b.cy) / max(ry, 1e-9)) ** 2 <= 1
    return True


def _is_pic(s: RShape) -> bool:
    return s.kind == "pic" or (s.fill_kind == "img" and bool(s.image_part))


def composite(x: float, y: float, bg: str, layers: list[RShape], reader: PresentationReader) -> str:
    """Итоговый цвет точки: слои снизу вверх, прозрачность учитывается."""
    c = bg
    for s in layers:
        if not _covers(s, x, y):
            continue
        if _is_pic(s):
            px = _pixel(reader, s, x, y)
            if px is None:
                continue
            col, a = px
            if a > 0.03:
                c = mix(c, col, a) if a < 0.97 else col
        elif s.fill and s.fill_kind in ("solid", "grad"):
            c = mix(c, s.fill, s.fill_alpha) if s.fill_alpha < 0.97 else s.fill
    return c


def _opaque(o: RShape, x: float, y: float, reader: PresentationReader) -> bool:
    if _is_pic(o):
        px = _pixel(reader, o, x, y)
        return px is not None and px[1] > 0.25
    return o.fill_alpha > 0.25


def _layers(s: RShape, shapes: list[RShape], decor: list[RShape]) -> tuple[list[RShape], list[RShape]]:
    def graphic(o: RShape) -> bool:
        if o is s or o.kind in ("grp", "cxn") or o.box.area < 1e-5 or o.rot:
            return False                    # картинка на весь слайд тоже слой: прозрачный PNG с кольцом диаграммы
        return o.kind == "pic" or o.fill_kind in ("solid", "grad", "img")
    below = [o for o in decor if graphic(o)] + sorted((o for o in shapes if graphic(o) and o.z < s.z), key=lambda o: o.z)
    if s.fill_kind in ("solid", "grad", "img") and s.fill_alpha > 0.03:
        below.append(s)                     # собственная заливка фигуры с текстом (плашка, кружок) — фон текста
    above = [o for o in shapes if graphic(o) and o.z > s.z]
    return below, above


def anchor_point(s: RShape, tb: Box) -> tuple[float, float]:
    """Где начинается текст: по выравниванию абзаца и вертикальной привязке рамки."""
    align = s.paras[0].align if s.paras else "l"
    ax = tb.x + tb.w * (0.5 if align == "ctr" else 0.92 if align == "r" else 0.06)
    ay = tb.y + tb.h * {"ctr": 0.5, "b": 0.9}.get(s.anchor, 0.12)
    return ax, ay


# ============================================================================ растр слоёв

class _Raster:
    """Сведённые слои: rgb (фон под текстом), edges (края объектов), ids (верхний непрозрачный слой)."""

    def __init__(self, reader: PresentationReader, slide_bg: str, layers: list[RShape]):
        from PIL import Image, ImageChops, ImageFilter
        self.W = _RES
        self.H = max(1, round(_RES * reader.slide_h_in / max(1e-6, reader.slide_w_in)))
        canvas = Image.new("RGBA", (self.W, self.H), (*hex_to_rgb(slide_bg), 255))
        self.ids = Image.new("L", (self.W, self.H), 0)
        self.index: dict[int, RShape] = {}
        for k, o in enumerate(layers[:250], start=1):
            piece, pos = self.layer(reader, o)
            if piece is None:
                continue
            canvas.alpha_composite(piece, pos)
            if o.box.area <= _BG_AREA:
                self.index[k] = o
                self.ids.paste(k, (*pos, pos[0] + piece.width, pos[1] + piece.height),
                               piece.getchannel("A").point(lambda v: 255 if v > 64 else 0))
        self.rgb = canvas.convert("RGB")
        chans = [c.filter(ImageFilter.FIND_EDGES) for c in self.rgb.split()]
        edges = ImageChops.lighter(ImageChops.lighter(chans[0], chans[1]), chans[2])
        self.edges = Image.new("L", (self.W, self.H), 0)     # рамка растра — не край объекта
        self.edges.paste(edges.crop((1, 1, self.W - 1, self.H - 1)), (1, 1))

    def layer(self, reader: PresentationReader, o: RShape):
        from PIL import Image, ImageDraw
        W, H = self.W, self.H
        X0, Y0 = round(o.box.x * W), round(o.box.y * H)
        iw, ih = max(1, round(o.box.w * W)), max(1, round(o.box.h * H))
        if _is_pic(o):
            src = reader.image(o.image_part)
            if src is None:
                return None, None
            l, t, r, b = (max(0.0, v) for v in o.crop)
            sw, sh = src.size
            box = (int(l * sw), int(t * sh), int(min(sw, (1 - r) * sw)), int(min(sh, (1 - b) * sh)))
            if box[2] <= box[0] or box[3] <= box[1]:
                return None, None
            im = src.crop(box).resize((iw, ih), Image.Resampling.BILINEAR)
            if o.flip[0]:
                im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if o.flip[1]:
                im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        elif o.fill and o.fill_kind in ("solid", "grad"):
            a = int(round(255 * max(0.0, min(1.0, o.fill_alpha))))
            im = Image.new("RGBA", (iw, ih), (*hex_to_rgb(o.fill), a))
            if o.geometry in ("ellipse", "roundRect"):
                m = Image.new("L", (iw, ih), 0)
                d = ImageDraw.Draw(m)
                if o.geometry == "ellipse":
                    d.ellipse((0, 0, iw - 1, ih - 1), fill=a)
                else:
                    d.rounded_rectangle((0, 0, iw - 1, ih - 1), radius=min(iw, ih) // 6, fill=a)
                im.putalpha(m)
        else:
            return None, None
        sx0, sy0 = max(0, -X0), max(0, -Y0)
        dx0, dy0 = max(0, X0), max(0, Y0)
        cw, ch = min(iw - sx0, W - dx0), min(ih - sy0, H - dy0)
        if cw <= 0 or ch <= 0:
            return None, None
        return im.crop((sx0, sy0, sx0 + cw, sy0 + ch)), (dx0, dy0)

    def px(self, b: Box) -> tuple[int, int, int, int]:
        x0 = min(self.W - 1, max(0, int(b.x * self.W)))
        y0 = min(self.H - 1, max(0, int(b.y * self.H)))
        x1 = min(self.W, max(x0 + 1, int(round(b.x2 * self.W))))
        y1 = min(self.H, max(y0 + 1, int(round(b.y2 * self.H))))
        return x0, y0, x1, y1

    def color_at(self, x: float, y: float) -> str:
        from PIL import Image
        cx, cy = min(self.W - 1, max(0, int(x * self.W))), min(self.H - 1, max(0, int(y * self.H)))
        box = (max(0, cx - 2), max(0, cy - 2), min(self.W, cx + 3), min(self.H, cy + 3))
        r, g, b = self.rgb.crop(box).resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        return rgb_to_hex(r, g, b)

    def id_at(self, x: float, y: float) -> int:
        return self.ids.getpixel((min(self.W - 1, max(0, int(x * self.W))), min(self.H - 1, max(0, int(y * self.H)))))


def _above_mask(ras: _Raster, reader: PresentationReader, layers: list[RShape]):
    """Непрозрачная графика ПОВЕРХ текста."""
    from PIL import Image, ImageChops
    im = Image.new("L", (ras.W, ras.H), 0)
    for o in layers:
        piece, pos = ras.layer(reader, o)
        if piece is None:
            continue
        a = piece.getchannel("A").point(lambda v: 255 if v > 64 else 0)
        region = im.crop((*pos, pos[0] + a.width, pos[1] + a.height))
        im.paste(ImageChops.lighter(region, a), pos)
    return im


def _cached(reader: PresentationReader, key, make):
    c = getattr(reader, "_occ_cache", None)
    if c is None:
        c = OrderedDict()
        reader._occ_cache = c
    if key not in c:
        c[key] = make()
        while len(c) > 16:
            c.popitem(last=False)
    c.move_to_end(key)
    return c[key]


# ============================================================================ сетка над рамкой

def _grid(s: RShape, tb: Box, shapes: list[RShape], decor: list[RShape], reader: PresentationReader, slide_bg: str,
          text_color: str | None, min_lc: float):
    """-> ((rows, cols, grid, anchor), backdrop); grid[r][c] — средний цвет свободной клетки или None (занята).
    Первый элемент None, если рядом с рамкой нет графики."""
    from PIL import Image, ImageFilter
    below, above = _layers(s, shapes, decor)
    below = [o for o in below if o.box.inter(tb) > 0 or o is s]
    above = [o for o in above if o.box.inter(tb) > 0]
    ras = _cached(reader, ("r", slide_bg, tuple(id(o) for o in below)), lambda: _Raster(reader, slide_bg, below))
    ax, ay = anchor_point(s, tb)
    backdrop = ras.color_at(ax, ay)
    if not above and not [o for o in below if o is not s]:
        return None, backdrop
    x0, y0, x1, y1 = ras.px(tb)
    cols = max(2, min(64, round((x1 - x0) / _CELL)))
    rows = max(2, min(40, round((y1 - y0) / _CELL)))
    k = max(3, min(9, int((x1 - x0) / cols) | 1))         # окно максимума ≈ клетка (нечётное)
    colors = ras.rgb.crop((x0, y0, x1, y1)).resize((cols, rows), Image.Resampling.BOX)
    edges = ras.edges.crop((x0, y0, x1, y1)).filter(ImageFilter.MaxFilter(k)).resize((cols, rows), Image.Resampling.NEAREST)
    crop = ras.rgb.crop((x0, y0, x1, y1))              # размах цвета в клетке: резкий, но бледный край не в счёт
    hi = crop.filter(ImageFilter.MaxFilter(k)).resize((cols, rows), Image.Resampling.NEAREST)
    lo = crop.filter(ImageFilter.MinFilter(k)).resize((cols, rows), Image.Resampling.NEAREST)
    ids = ras.ids.crop((x0, y0, x1, y1)).resize((cols, rows), Image.Resampling.NEAREST)
    over = None
    if above:
        m = _cached(reader, ("m", tuple(id(o) for o in above)), lambda: _above_mask(ras, reader, above))
        over = m.crop((x0, y0, x1, y1)).filter(ImageFilter.MaxFilter(k)).resize((cols, rows), Image.Resampling.NEAREST)
    # слой, в «дырке» которого стоит текст: его рамка содержит точку привязки, а пиксель там прозрачный
    home = ras.id_at(ax, ay)
    holes = {k2 for k2, o in ras.index.items() if k2 != home and o is not s and o.box.contains_point(ax, ay)}
    readable = text_color is not None and abs(apca_lc(text_color, backdrop)) >= min_lc
    ar = min(rows - 1, max(0, int((ay - tb.y) / max(1e-9, tb.h) * rows)))
    ac = min(cols - 1, max(0, int((ax - tb.x) / max(1e-9, tb.w) * cols)))
    grid: list[list[str | None]] = []
    for r in range(rows):
        row: list[str | None] = []
        for c in range(cols):
            col = rgb_to_hex(*colors.getpixel((c, r)))
            if over is not None and over.getpixel((c, r)) > 0:
                row.append(None)                          # поверх текста лежит графика
            elif ids.getpixel((c, r)) in holes:
                row.append(None)                          # кольцо диаграммы вокруг цифры
            elif edges.getpixel((c, r)) > _EDGE and (r, c) != (ar, ac) \
                    and distance(rgb_to_hex(*hi.getpixel((c, r))), rgb_to_hex(*lo.getpixel((c, r)))) > _RANGE:
                row.append(None)                          # край объекта: паттерн, обод шара, край кольца
            elif readable and abs(apca_lc(text_color, col)) < min_lc * 0.8:
                row.append(None)                          # здесь текст перестал бы читаться
            else:
                row.append(col)
        grid.append(row)
    return (rows, cols, grid, (ar, ac)), backdrop


def free_box(s: RShape, tb: Box, shapes: list[RShape], decor: list[RShape], reader: PresentationReader,
             slide_bg: str, text_color: str | None = None, min_lc: float = 45.0) -> tuple[Box, str]:
    """-> (рабочая рамка текста без наложения на графику, цвет фона в точке привязки)."""
    g, backdrop = _grid(s, tb, shapes, decor, reader, slide_bg, text_color, min_lc)
    if g is None:
        return tb, backdrop
    rows, cols, grid, (ar, ac) = g
    if grid[ar][ac] is None:
        return tb, backdrop                               # сам образец стоит на графике — это замысел
    free = [[False] * cols for _ in range(rows)]
    free[ar][ac] = True
    q = deque([(ar, ac)])
    while q:
        r, c = q.popleft()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and not free[nr][nc] and grid[nr][nc] is not None:
                if distance(grid[nr][nc], grid[r][c]) < _STEP:
                    free[nr][nc] = True
                    q.append((nr, nc))
    if sum(map(sum, free)) >= rows * cols * 0.97:
        return tb, backdrop                               # вся рамка свободна
    best = _largest_rect(free, ar, ac)
    if best is None:
        return tb, backdrop
    r0, c0, r1, c1 = best
    cw, ch = tb.w / cols, tb.h / rows
    fb = Box(x=tb.x + c0 * cw, y=tb.y + r0 * ch, w=(c1 - c0 + 1) * cw, h=(r1 - r0 + 1) * ch)
    if s.paras and s.paras[0].align == "ctr":           # центрированный текст не должен уехать вбок
        half = min(tb.cx - fb.x, fb.x2 - tb.cx)
        fb = Box(x=tb.cx - half, y=fb.y, w=2 * half, h=fb.h) if half > 0 else fb
    if s.anchor == "ctr":
        half = min(tb.cy - fb.y, fb.y2 - tb.cy)
        fb = Box(x=fb.x, y=tb.cy - half, w=fb.w, h=2 * half) if half > 0 else fb
    if fb.area < tb.area * 0.12:
        return tb, backdrop                               # почти ничего не осталось — это замысел (текст по картинке)
    return fb, backdrop


def on_picture(s: RShape, tb: Box, shapes: list[RShape], decor: list[RShape], reader: PresentationReader) -> bool:
    """Начало текста стоит на картинке (карточка-рендер с объектом), а не на фоне или плашке."""
    below, _ = _layers(s, shapes, decor)
    ax, ay = anchor_point(s, tb)
    for o in reversed(below):
        if o.box.area > _BG_AREA or not _covers(o, ax, ay) or not _opaque(o, ax, ay, reader):
            continue
        return _is_pic(o)
    return False


def blocked_ratio(s: RShape, box: Box, shapes: list[RShape], decor: list[RShape], reader: PresentationReader,
                  slide_bg: str) -> float:
    """Доля клеток прямоугольника, занятых графикой (аудит готового файла: где фактически стоит текст)."""
    g, _ = _grid(s, box, shapes, decor, reader, slide_bg, None, 45.0)
    if g is None:
        return 0.0
    rows, cols, grid, _ = g
    return sum(1 for row in grid for col in row if col is None) / max(1, rows * cols)


def nowrap_extent(s: RShape, tb: Box, shapes: list[RShape], decor: list[RShape], reader: PresentationReader,
                  slide_bg: str, grow: float = 1.8, text_color: str | None = None) -> Box:
    """Строка без переноса выходит за рамку по выравниванию. Сколько места до графики: рамка расширяется
    в grow раз в сторону переполнения и ограничивается свободной областью; центрированная строка растёт
    в обе стороны одинаково — берём меньший из запасов."""
    align = s.paras[0].align if s.paras else "l"
    w = min(1.0, tb.w * grow)
    x = tb.x if align not in ("ctr", "r") else (tb.x2 - w if align == "r" else tb.cx - w / 2)
    x = min(max(0.0, x), 1.0 - w)
    fb, _ = free_box(s, Box(x=x, y=tb.y, w=w, h=tb.h), shapes, decor, reader, slide_bg, text_color=text_color)
    if align == "ctr":
        half = max(tb.w / 2, min(tb.cx - fb.x, fb.x2 - tb.cx))
        return Box(x=tb.cx - half, y=tb.y, w=2 * half, h=tb.h)
    if align == "r":
        return Box(x=min(fb.x, tb.x), y=tb.y, w=tb.x2 - min(fb.x, tb.x), h=tb.h)
    return Box(x=tb.x, y=tb.y, w=max(fb.x2, tb.x2) - tb.x, h=tb.h)


def _largest_rect(free: list[list[bool]], ar: int, ac: int) -> tuple[int, int, int, int] | None:
    """Наибольший прямоугольник из свободных клеток, содержащий (ar, ac). Сетка маленькая — перебор с префиксами."""
    rows, cols = len(free), len(free[0])
    pre = [[0] * (cols + 1) for _ in range(rows + 1)]
    for r in range(rows):
        for c in range(cols):
            pre[r + 1][c + 1] = pre[r][c + 1] + pre[r + 1][c] - pre[r][c] + (1 if free[r][c] else 0)

    def full(r0, c0, r1, c1) -> bool:
        n = (r1 - r0 + 1) * (c1 - c0 + 1)
        return pre[r1 + 1][c1 + 1] - pre[r0][c1 + 1] - pre[r1 + 1][c0] + pre[r0][c0] == n

    best, area = None, 0
    for r0 in range(ar + 1):
        for r1 in range(ar, rows):
            c0 = ac
            while c0 > 0 and full(r0, c0 - 1, r1, ac):
                c0 -= 1
            if not full(r0, c0, r1, ac):
                continue
            c1 = ac
            while c1 < cols - 1 and full(r0, c0, r1, c1 + 1):
                c1 += 1
            a = (r1 - r0 + 1) * (c1 - c0 + 1)
            if a > area:
                best, area = (r0, c0, r1, c1), a
    return best
