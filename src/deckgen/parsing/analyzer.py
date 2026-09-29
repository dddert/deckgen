"""Слайд-образец шаблона -> TemplateSlide: слоты текста, элементы (карточки), картинки, место под график, тип.

Ключевое решение v2: единица генерации — клон слайда-образца, а не пустой layout. Поэтому здесь
не «вырезаем» стиль, а понимаем, какие фигуры несут контент и сколько в них влезает; всё остальное
(подложки, градиенты, декор, иконки) при клонировании остаётся таким, как сделал дизайнер.

Ничего не привязано к датасету: роли и типы выводятся из структуры (плейсхолдеры, повторы фигур,
вложенность, размеры, числовые заглушки); ключевые слова — только подсказка.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import (Box, ItemGroup, PictureRole, PictureSlot, SlideKind, SlotPart, SlotRole, TemplateSlide,
                      TextSlot, VisualArea)
from ..ooxml.colors import is_dark
from ..ooxml.reader import PresentationReader, RShape, RSlide
from .fonts import TextMeasurer
from .occupancy import free_box, nowrap_extent, on_picture

_NUMERIC = re.compile(r"^[\s~≈<>+\-−]*(?:\d+(?:[.,]\d+)?|[xхXХ]{1,4})\s*(?:%|[xхXХ]{0,3}%|млн|млрд|тыс\.?|₽|\$|k|K|M|х|x|\*)?\s*$")
_ORDINAL = re.compile(r"^\s*0?\d{1,2}[.)]?\s*$")
_DATE = re.compile(r"^(дата|date|\d{1,2}[.:/]\d{2}([.:/]\d{2,4})?|q[1-4]|[1-4]\s*кв|(19|20)\d\d|янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек)", re.I)
_DECOR_TEXT = re.compile(r"^[0\s/\\.·•○●|_\-+]+$")
_MEDIA_HINT = re.compile(r"вставить\s*(фото|qr|картин|изображ|логотип)|^qr[\s-]*(code|код)|^иллюстрац|^фото$|^скриншот|^image$|^photo$", re.I)
_NAME_HINT = re.compile(r"имя\s+(фамилия|спикера)|^фио\b|^name surname", re.I)
_ROLE_HINT = re.compile(r"^должност|^позиция|^регалии|^position", re.I)
_CHART_WORDS = re.compile(r"график|диаграмм|гистограмм|chart", re.I)
_CODE_FONTS = ("courier", "consolas", "mono", "menlo", "code")

# подсказки по тексту заголовка образца (приоритетнее структуры) ...
_TITLE_HINTS: list[tuple[SlideKind, re.Pattern]] = [
    (SlideKind.thanks, re.compile(r"спасибо|thank", re.I)),
    (SlideKind.qa, re.compile(r"\bq\s*&\s*a\b|^вопросы\??$", re.I)),
    (SlideKind.agenda, re.compile(r"оглавлени|содержани|agenda|повестк", re.I)),
    (SlideKind.title, re.compile(r"название презентации|тему презентации|title slide|титул", re.I)),
    (SlideKind.section, re.compile(r"разделител|название раздела|section", re.I)),
    (SlideKind.process, re.compile(r"таймлайн|timeline|этап|дорожн|roadmap|шаг|нумерац", re.I)),
    (SlideKind.quote, re.compile(r"цитат|quote", re.I)),
    (SlideKind.cta, re.compile(r"call to action|призыв", re.I)),
    (SlideKind.team, re.compile(r"визитк|спикер|команд", re.I)),
    (SlideKind.chart, _CHART_WORDS),
]
# ... по имени макета (виртуальные образцы из PowerPoint-шаблонов: «Title Slide», «Two Content» ...)
_LAYOUT_HINTS: list[tuple[SlideKind, re.Pattern]] = [
    (SlideKind.title, re.compile(r"^title slide|титульн|^обложка", re.I)),
    (SlideKind.section, re.compile(r"section|раздел", re.I)),
    (SlideKind.two_columns, re.compile(r"two content|comparison|две|сравнен|2 колон", re.I)),
    (SlideKind.image, re.compile(r"picture|рисунок|фото|изображ", re.I)),
    (SlideKind.quote, re.compile(r"quote|цитат", re.I)),
    (SlideKind.thanks, re.compile(r"end|final|финал|спасибо|thank", re.I)),
]
# ... и по всему тексту слайда (слабее)
_BODY_HINTS: list[tuple[SlideKind, re.Pattern]] = [
    (SlideKind.thanks, re.compile(r"спасибо|thank", re.I)),
    (SlideKind.cta, re.compile(r"qr[\s-]*(code|код)|call to action", re.I)),
]


@dataclass
class _Unit:
    """Фигура/группа, которая может быть элементом повторяющейся группы."""
    shape: RShape
    members: list[RShape]
    has_text: bool
    font: float


def analyze_slide(rs: RSlide, measurer: TextMeasurer, slide_w_in: float, slide_h_in: float,
                  logo_media: set[str], max_shapes: int = 70, photos: set[str] | None = None,
                  layout_decor: list[RShape] | None = None, virtual: bool = False,
                  photo_like: set[str] | None = None, reader: PresentationReader | None = None) -> TemplateSlide:
    photos = photos or set()
    all_decor = [d for d in (layout_decor or []) if d.box.visible() and d.kind != "grp"]
    layout_decor = [d for d in all_decor if d.box.area < 0.45]
    shapes = [s for s in rs.shapes if s.box.visible()]
    by_id = {s.id: s for s in shapes}
    ts = TemplateSlide(index=rs.index, layout_part=rs.layout_part, layout_name=rs.layout_name, kind=SlideKind.other,
                       all_shape_ids=[s.id for s in shapes], shape_boxes={s.id: s.box for s in shapes})
    ts.bg_color = rs.background.color or "FFFFFF"
    ts.dark = is_dark(ts.bg_color)
    slide_text = " ".join(s.text for s in shapes if s.kind == "sp").lower()
    chart_slide = bool(_CHART_WORDS.search(slide_text))

    # ---- 1. текстовые фигуры, заглушки медиа, декоративный текст
    media: list[tuple[RShape, str]] = []
    text_shapes: list[RShape] = []
    decor_text: set[str] = set()
    for s in shapes:
        if s.kind != "sp" or s.ph_type in ("dt", "ftr", "sldNum", "hdr"):
            continue
        txt = s.text
        if not txt:
            if s.ph_type in ("title", "ctrTitle", "subTitle", "body", "obj"):
                text_shapes.append(s)          # пустой плейсхолдер — тоже слот
            continue
        if _DECOR_TEXT.match(txt) and len(txt) >= 3 and not _ORDINAL.match(txt):
            decor_text.add(s.id)
            continue                           # «0 0 0 / / /» — декор
        if _MEDIA_HINT.search(txt.strip()):
            media.append((s, txt))
            continue
        text_shapes.append(s)
    text_ids = {s.id for s in text_shapes}

    # ---- 2. картинки (до элементов: пример графика не должен стать «карточкой»)
    photo_layout = bool(re.search(r"фото|photo|картин|изображ|скриншот|мокап|screenshot", rs.layout_name, re.I))
    for s in shapes:
        if s.kind == "pic" or (s.kind == "sp" and s.fill_kind == "img" and not s.text):
            role = _picture_role(s, logo_media, slide_text, chart_slide, photos)
            if photo_layout and role == PictureRole.decor and s.box.area > 0.04:
                role = PictureRole.photo       # макет «Паттерн + фото», «1 фото»: большие картинки — место под фото
            ts.pictures.append(PictureSlot(shape_id=s.id, role=role, box=s.box, media=s.image_part))
    for s, _ in media:
        frame = _media_frame(s, shapes)
        ts.pictures.append(PictureSlot(shape_id=frame.id if frame else s.id, role=PictureRole.photo,
                                       box=frame.box if frame else s.box, also=[s.id] if frame else []))
    for s in shapes:
        if s.ph_type == "pic" and s.kind == "sp" and not any(p.shape_id == s.id for p in ts.pictures):
            ts.pictures.append(PictureSlot(shape_id=s.id, role=PictureRole.photo, box=s.box))
    chart_boxes = [p.box for p in ts.pictures if p.role == PictureRole.chart_example]
    chart_ids = {p.shape_id for p in ts.pictures if p.role == PictureRole.chart_example}
    in_chart = {s.id for s in text_shapes if any(b.contains_point(s.box.cx, s.box.cy) for b in chart_boxes)}

    # ---- 3. повторяющиеся элементы
    title_shape = _pick_title([s for s in text_shapes if s.id not in in_chart])
    units = _units(shapes, text_ids, title_shape, {p.shape_id for p in ts.pictures if p.role == PictureRole.chart_example} | in_chart)
    items = _items(units, text_ids)
    item_of = {sid: it.index for it in items for sid in it.shape_ids}
    for p in ts.pictures:
        p.item = item_of.get(p.shape_id)

    # ---- 4. текстовые слоты
    sizes = [s.main_run.size for s in text_shapes if s.main_run]
    body_size = _median(sizes) if sizes else 14.0
    for s in text_shapes:
        if s.id in in_chart:
            continue
        slot = _slot(s, title_shape, item_of.get(s.id), body_size, items, by_id, shapes + layout_decor, measurer,
                     slide_w_in, slide_h_in, decor_text, chart_ids | in_chart,
                     occ=(shapes, all_decor, reader, ts.bg_color) if reader is not None else None)
        ts.texts.append(slot)
    seq = sorted((int(t.demo_text.strip().rstrip(".)")) for t in ts.texts
                  if t.role in (SlotRole.ordinal, SlotRole.number) and _ORDINAL.match(t.demo_text or "")))
    numbered = len(seq) >= 3 and seq == list(range(seq[0], seq[0] + len(seq)))   # «1 2 3 … 8» — нумерация списка
    for t in ts.texts:                         # одиночное «01» вне ряда — показатель, а не номер шага
        if t.role in (SlotRole.ordinal, SlotRole.number) and _ORDINAL.match(t.demo_text or "") and numbered:
            t.role = SlotRole.ordinal
        elif t.role == SlotRole.ordinal and t.item is None:
            t.role = SlotRole.number

    ts.items = items
    ts.layout_photos = any(d.kind == "pic" and (d.image_part in photos or d.image_part in (photo_like or set()))
                           and 0.04 < d.box.area < 0.95 for d in all_decor)
    ts.visual = _visual_area(shapes, ts, media, in_chart)
    if ts.visual is None and not items and title_shape is not None:
        ts.visual = _free_area(title_shape, shapes, layout_decor, ts)
    if ts.visual is not None and title_shape is not None and ts.visual.source != "table":
        top = title_shape.text_box.y + min(title_shape.text_box.h, 0.2) + 0.02
        if ts.visual.box.y < top < ts.visual.box.y2 - 0.15:
            b = ts.visual.box
            ts.visual.box = Box(x=b.x, y=top, w=b.w, h=b.y2 - top)
    ts.text_capacity = sum(t.max_chars for t in ts.texts if t.role not in (SlotRole.ordinal, SlotRole.footer))
    ts.density = "low" if ts.text_capacity < 220 else "high" if ts.text_capacity > 700 else "medium"
    ts.kind, ts.confidence, ts.name = _classify(rs, ts, title_shape, slide_text, virtual)
    ts.usable, ts.reason = _usable(shapes, ts, max_shapes)
    return ts


# ----------------------------------------------------------------------------- units & items

def _units(shapes: list[RShape], text_ids: set[str], title: RShape | None, skip: set[str]) -> list[_Unit]:
    kids: dict[str | None, list[RShape]] = {}
    for s in shapes:
        kids.setdefault(s.parent, []).append(s)

    def desc(s: RShape) -> list[RShape]:
        out = []
        for c in kids.get(s.id, []):
            out.append(c)
            out += desc(c)
        return out

    def build(parent: str | None):
        for s in kids.get(parent, []):
            if s.id in skip or (title is not None and s.id == title.id):
                continue
            if s.kind == "grp":
                d = desc(s)
                n_text = sum(1 for m in d if m.id in text_ids)
                if s.box.area > 0.12 or n_text > 6:       # большая группа — контейнер, а не карточка
                    yield from build(s.id)
                    continue
                mem = [s] + d
            else:
                mem = [s]
            if s.box.area > 0.45 or s.box.area < 0.00004:
                continue
            if s.kind == "sp" and s.text and s.id not in text_ids:
                continue                               # декоративный текст
            fonts = [m.main_run.size for m in mem if m.id in text_ids and m.main_run]
            yield _Unit(s, mem, any(m.id in text_ids for m in mem), max(fonts) if fonts else 0.0)

    return list(build(None))


def _similar(a: _Unit, b: _Unit) -> bool:
    sa, sb = a.shape, b.shape
    if sa.kind != sb.kind or (sa.geometry or "") != (sb.geometry or "") or a.has_text != b.has_text:
        return False
    if abs(sa.box.w - sb.box.w) > 0.015 or abs(sa.box.h - sb.box.h) > 0.02:
        return False
    if a.has_text and abs(a.font - b.font) > 1.01:
        return False
    return not (sa.kind == "grp" and len(a.members) != len(b.members))


def _clusters(units: list[_Unit]) -> list[list[_Unit]]:
    clusters: list[list[_Unit]] = []
    for u in units:
        for c in clusters:
            if _similar(c[0], u):
                c.append(u)
                break
        else:
            clusters.append([u])
    return [c for c in clusters if len(c) >= 2 and all(
        a.shape.box.inter(b.shape.box) < 0.15 * min(a.shape.box.area, b.shape.box.area)
        for i, a in enumerate(c) for b in c[i + 1:])]


def _reading_order(boxes: list[Box]) -> list[int]:
    idx = sorted(range(len(boxes)), key=lambda i: boxes[i].cy)
    rows: list[list[int]] = []
    for i in idx:
        if rows and abs(boxes[rows[-1][0]].cy - boxes[i].cy) < max(0.03, boxes[i].h * 0.35):
            rows[-1].append(i)
        else:
            rows.append([i])
    return [i for row in rows for i in sorted(row, key=lambda j: boxes[j].x)]


def _items(units: list[_Unit], text_ids: set[str]) -> list[ItemGroup]:
    clusters = _clusters(units)

    def contains_others(c: list[_Unit]) -> bool:
        return any(o is not u and o not in c and u.shape.box.contains_point(o.shape.box.cx, o.shape.box.cy)
                   for u in c for o in units)

    ranked = sorted(clusters, key=lambda c: (2 <= len(c) <= 8, contains_others(c) or c[0].shape.kind == "grp",
                                             c[0].has_text, sum(u.shape.box.area for u in c)), reverse=True)
    for primary in ranked:
        if not 2 <= len(primary) <= 8:
            continue
        res = _build_items(primary, clusters, units)
        if not res:
            continue
        with_text = all(any(sid in text_ids for sid in it.shape_ids) for it in res)
        rich = len(res) >= 3 or all(len(it.shape_ids) >= 2 for it in res)
        if with_text and rich:
            return res
    return []


def _build_items(primary: list[_Unit], clusters: list[list[_Unit]], units: list[_Unit]) -> list[ItemGroup]:
    primary = [primary[i] for i in _reading_order([u.shape.box for u in primary])]
    groups: list[set[str]] = [{m.id for m in u.members} for u in primary]
    roots: list[list[str]] = [[u.shape.id] for u in primary]
    boxes = [u.shape.box for u in primary]
    assigned = set().union(*groups)
    for u in units:                                   # всё, что центром внутри подложки
        if u in primary or u.shape.id in assigned:
            continue
        for gi, b in enumerate(boxes):
            if b.contains_point(u.shape.box.cx, u.shape.box.cy, pad=0.004):
                groups[gi] |= {m.id for m in u.members}
                roots[gi].append(u.shape.id)
                assigned |= {m.id for m in u.members}
                break
    same_row = max(b.cy for b in boxes) - min(b.cy for b in boxes) < 0.05
    same_col = max(b.cx for b in boxes) - min(b.cx for b in boxes) < 0.05
    for c in clusters:                                # параллельные ряды: номер над подписью, дата над описанием
        if c is primary:
            continue
        for u in c:
            if u.shape.id in assigned:
                continue
            ub = u.shape.box
            best, best_ov = None, 0.0
            for gi, b in enumerate(boxes):
                if same_row:
                    ov = max(0.0, min(ub.x2, b.x2) - max(ub.x, b.x)) / max(ub.w, 1e-6)
                elif same_col:
                    ov = max(0.0, min(ub.y2, b.y2) - max(ub.y, b.y)) / max(ub.h, 1e-6)
                else:
                    d = ((ub.cx - b.cx) ** 2 + (ub.cy - b.cy) ** 2) ** 0.5
                    ov = 1 / (1 + 10 * d) if d < 0.2 else 0
                if ov > best_ov:
                    best, best_ov = gi, ov
            if best is not None and best_ov >= 0.5:
                groups[best] |= {m.id for m in u.members}
                roots[best].append(u.shape.id)
                assigned |= {m.id for m in u.members}
    all_shapes = {m.id: m for u in units for m in u.members}
    items, row_y = [], []
    for i, g in enumerate(groups):
        bx = [all_shapes[s].box for s in g if s in all_shapes]
        x, y = min(b.x for b in bx), min(b.y for b in bx)
        box = Box(x=x, y=y, w=max(b.x2 for b in bx) - x, h=max(b.y2 for b in bx) - y)
        row = next((k for k, ry in enumerate(row_y) if abs(ry - box.cy) < 0.05), None)
        if row is None:
            row_y.append(box.cy)
            row = len(row_y) - 1
        items.append(ItemGroup(index=i, box=box, shape_ids=sorted(g, key=lambda s: all_shapes[s].z), roots=roots[i], row=row))
    for row in {it.row for it in items}:
        for col, it in enumerate(sorted((it for it in items if it.row == row), key=lambda it: it.box.x)):
            it.col = col
    return items


# ----------------------------------------------------------------------------- text slots

def _pick_title(text_shapes: list[RShape]) -> RShape | None:
    phs = [s for s in text_shapes if s.ph_type in ("title", "ctrTitle")]
    if phs:
        return max(phs, key=lambda s: s.box.area)
    cands = [s for s in text_shapes if s.main_run and s.box.y < 0.45 and len(s.text) <= 90]
    if not cands:
        return None
    best = max(cands, key=lambda s: (s.main_run.size, -s.box.y))
    others = [s.main_run.size for s in text_shapes if s is not best and s.main_run]
    if best.main_run.size >= 20 and (not others or best.main_run.size >= 1.3 * _median(others)):
        return best
    return None


def _segments(s: RShape) -> list[tuple[str, float, bool, str, str | None]]:
    """Сегменты текста: абзацы, внутри — строки через a:br. (текст, кегль, bold, разделитель, цвет)."""
    out = []
    for pi, p in enumerate(s.paras):
        cur, first = [], True
        for r in p.runs + [None]:
            if r is None or r.text == "\n":
                txt = "".join(x.text for x in cur).strip()
                if txt:
                    main = max(cur, key=lambda x: len(x.text.strip()))
                    out.append((txt, main.size, main.bold, "p" if first and pi > 0 else ("br" if not first else "p"),
                                main.color))
                cur, first = [], (False if txt else first)
                continue
            cur.append(r)
    return out


def _slot(s: RShape, title: RShape | None, item: int | None, body_size: float, items: list[ItemGroup],
          by_id: dict[str, RShape], shapes: list[RShape], m: TextMeasurer, W: float, H: float,
          decor: set[str] = frozenset(), ignore: set[str] = frozenset(), occ=None) -> TextSlot:
    mr = s.main_run                     # у пустого плейсхолдера — стиль, унаследованный от макета
    size = mr.size if mr else (32.0 if s.ph_type in ("title", "ctrTitle") else 16.0)
    fam = mr.family if mr else "Arial"
    bold = mr.bold if mr else False
    ls = s.paras[0].line_spacing if s.paras else 1.0
    tb = _limit_below(s, _limit_container(s, _in_slide(s.text_box), shapes), shapes, decor)
    bg, limited, on_pic = None, False, False
    tc, lc = None, 45.0
    if occ is not None:                 # по пикселям: текст не должен ложиться на шар, кольцо, паттерн макета
        slide_shapes, all_decor, reader, slide_bg = occ
        tc = mr.color if mr else None
        lc = 45.0 if size >= 24 or (bold and size >= 18) else 55.0
        fb, bg = free_box(s, tb, [o for o in slide_shapes if o.id not in ignore], all_decor, reader, slide_bg,
                          text_color=tc, min_lc=lc)
        on_pic = bool(s.text.strip()) and on_picture(s, fb, slide_shapes, all_decor, reader)
        if on_pic:
            # текст на картинке-карточке: светлый верх 3D-объекта на светлой карточке по цвету не отличить,
            # поэтому высота — как у текста дизайнера с запасом (≥ 2 строк), а не вся рамка поверх объекта
            n_demo = _demo_lines(s.text, m, fam, size, bold, s.text_box.w * W)      # как набрал дизайнер
            cap = max(n_demo * 1.25, 2) * size * 1.2 * ls / 72 / H
            if fb.h > cap + 0.005:
                y = fb.y if s.anchor in ("t", None) else (fb.cy - cap / 2 if s.anchor == "ctr" else fb.y2 - cap)
                fb = Box(x=fb.x, y=y, w=fb.w, h=cap)
        limited = fb.w < tb.w - 0.005 or fb.h < tb.h - 0.005
        tb = fb
    else:
        tb = _avoid_decor(s, tb, shapes, decor)
    grow = None if on_pic else _grow_box(s, tb, items[item].box if item is not None else None, shapes)
    if grow is not None and occ is not None:
        grow, _ = free_box(s, grow, [o for o in occ[0] if o.id not in ignore], occ[1], occ[2], occ[3], text_color=tc, min_lc=lc)
        grow = grow if grow.h > tb.h + 0.005 else None
    role = _role(s, title, item, body_size, items, by_id)
    wide = _widen(s, tb, shapes, decor | ignore) if role in (SlotRole.title, SlotRole.subtitle) and item is None else None
    if wide is not None and occ is not None:
        wide, _ = free_box(s, wide, [o for o in occ[0] if o.id not in ignore], occ[1], occ[2], occ[3], text_color=tc, min_lc=lc)
        wide = wide if wide.w > tb.w * 1.15 else None
    if (not s.wrap or role == SlotRole.number) and occ is not None and wide is None:   # «10%» в кольце: докуда можно расти
        wide = nowrap_extent(s, tb, [o for o in occ[0] if o.id not in ignore], occ[1], occ[2], occ[3], text_color=tc)
    cap = grow or (wide if s.wrap else None) or tb
    chars, lines = m.capacity_chars(fam, size, cap.w * W * (1.0 if s.wrap else 1.6), cap.h * H, bold, ls)
    if not s.wrap:                         # строка без переноса: ёмкость — по ширине демо-текста с небольшим запасом
        chars = max(chars, int(len(s.text.split("\n")[0]) * 1.3) + 1)
    parts: list[SlotPart] = []
    segs = _segments(s)
    if role not in (SlotRole.bullets, SlotRole.title) and len(segs) >= 2 and len({(round(x[1]), x[2]) for x in segs}) > 1:
        used_h = 0.0
        for i, (txt, sz, b, _sep, col) in enumerate(segs):
            if i < len(segs) - 1:
                n_lines = max(1, round(m.lines(txt, fam, sz, cap.w * W, b)))
                c, _ = m.capacity_chars(fam, sz, cap.w * W, n_lines * sz * 1.2 * ls / 72 + 0.01, b, ls)
                used_h += n_lines * sz * 1.2 * ls / 72
            else:
                c, _ = m.capacity_chars(fam, sz, cap.w * W, max(sz * 1.25 / 72, cap.h * H - used_h), b, ls)
            prole = SlotRole.number if _NUMERIC.match(txt) else SlotRole.heading if (b or sz > min(x[1] for x in segs)) and i == 0 \
                else SlotRole.body
            parts.append(SlotPart(role=prole, size_pt=sz, bold=b, max_chars=max(4, c), demo=txt, color=col))
    container = (s.fill_kind in ("solid", "grad", "img") or s.line is not None) and (
        s.box.area > 0.03 or any(o is not s and s.box.contains_point(o.box.cx, o.box.cy) for o in shapes if o.depth >= s.depth))
    return TextSlot(shape_id=s.id, role=role, box=tb, text_box=s.text_box, grow_box=grow, wide_box=wide, item=item,
                    demo_text=s.text,
                    size_pt=size, family=fam, bold=bold, color=mr.color if mr else None, line_spacing=ls,
                    paragraphs=max(1, len([p for p in s.paras if p.text.strip()])),
                    max_chars=chars, max_lines=lines, autofit=s.autofit, parts=parts, container=container,
                    nowrap=not s.wrap, bg_color=bg, limited=limited)


def _demo_lines(text: str, m: TextMeasurer, fam: str, size: float, bold: bool, w_in: float) -> int:
    """Строк в тексте дизайнера. Без файла гарнитуры меряем прокси-шрифтом, он шире (Arial против Play):
    строка, которая у дизайнера помещалась, «не влезает» на 5–15% — такую считаем одной строкой."""
    import math
    n = 0
    for line in (x for x in text.split("\n") if x.strip()):
        n += max(1, math.ceil(m.width_in(line.strip(), fam, size, bold) / max(1e-6, w_in * 1.15)))
    return max(1, n)


def _role(s: RShape, title: RShape | None, item: int | None, body_size: float, items, by_id) -> SlotRole:
    txt = s.text.strip()
    first = txt.split("\n")[0].strip() if txt else ""
    if title is not None and s.id == title.id:
        return SlotRole.title
    if s.ph_type == "subTitle":
        return SlotRole.subtitle
    if _NAME_HINT.search(first):
        return SlotRole.name
    if _ROLE_HINT.search(first):
        return SlotRole.caption
    if first and _ORDINAL.match(first) and len(txt) <= 3:
        return SlotRole.ordinal
    if first and _NUMERIC.match(first):
        return SlotRole.number
    paras = [p for p in s.paras if p.text.strip()]
    if len(paras) >= 2 and len({(round(p.runs[0].size), p.runs[0].bold) for p in paras if p.runs}) == 1:
        return SlotRole.bullets
    if not txt and s.ph_type in ("body", "obj"):
        if title is not None and title.box.y2 <= s.box.y + 0.01 and s.box.y - title.box.y2 < 0.12 and s.box.h < 0.15:
            return SlotRole.subtitle                # пустая узкая строка под заголовком обложки — подзаголовок, не список
        return SlotRole.bullets
    size = s.main_run.size if s.main_run else 16.0
    if _DATE.match(first) and len(txt) <= 16:
        return SlotRole.label
    if s.fill_kind == "solid" and len(txt) <= 16 and s.box.h < 0.07:
        return SlotRole.label                       # «таблетка» с тегом
    if item is not None:
        sib = [by_id[x] for x in items[item].shape_ids if x in by_id and by_id[x].kind == "sp" and by_id[x].main_run
               and by_id[x].text.strip()]
        sizes = sorted({round(x.main_run.size, 1) for x in sib})
        if len(txt) <= 40 and (bool(s.main_run and s.main_run.bold) or (len(sizes) > 1 and round(size, 1) == sizes[-1])):
            return SlotRole.heading
        return SlotRole.body
    tsize = title.main_run.size if title is not None and title.main_run else 30
    if title is not None and title.box.y2 <= s.box.y and s.box.y - title.box.y2 < 0.12 and size < tsize \
            and len(txt) <= 80 and s.box.w > 0.2:
        return SlotRole.subtitle
    if size <= 11 and len(txt) <= 80:
        return SlotRole.caption
    return SlotRole.body


def _limit_container(s: RShape, tb: Box, shapes: list[RShape]) -> Box:
    """Текст в подложке карточки, внутри которой лежат другие фигуры: ёмкость — до первой из них."""
    inner = [o for o in shapes if o is not s and o.kind in ("sp", "pic") and (o.text or o.kind == "pic")
             and tb.contains_point(o.box.cx, o.box.cy) and o.box.y > tb.y + 0.005]
    if not inner:
        return tb
    top = min(o.box.y for o in inner)
    return tb.moved(h=max(0.02, top - tb.y - 0.008))


def _limit_below(s: RShape, tb: Box, shapes: list[RShape], decor: set[str] = frozenset()) -> Box:
    """Высокая рамка заголовка, в нижнюю часть которой залезает контент (таблица, карточки): ёмкость — до него."""
    tops = [o.box.y for o in shapes
            if o is not s and o.id not in decor and o.kind in ("sp", "pic", "frame") and o.box.area < 0.5
            and tb.y + 0.03 < o.box.y < tb.y2 and o.box.x < tb.x2 - 0.01 and o.box.x2 > tb.x + 0.01
            and not o.box.contains(tb) and (o.text or o.kind != "sp" or o.fill_kind in ("solid", "grad", "img"))]
    if not tops:
        return tb
    return tb.moved(h=max(0.03, min(tops) - tb.y - 0.01))


def _avoid_decor(s: RShape, tb: Box, shapes: list[RShape], decor: set[str]) -> Box:
    """Широкая рамка плейсхолдера, в правую часть которой заходит паттерн/картинка макета: текст — левее неё."""
    right = tb.x2
    for o in shapes:
        if o is s or o.id in decor or o.kind not in ("pic", "sp") or o.box.area > 0.8 or o.box.area < 0.004:
            continue
        if o.kind == "sp" and (o.text or o.fill_kind in (None, "none")):
            continue
        if o.box.contains(tb) or tb.contains(o.box, pad=0):
            continue
        ov_y = min(o.box.y2, tb.y2) - max(o.box.y, tb.y)
        if ov_y > min(0.02, tb.h * 0.3) and tb.x + tb.w * 0.4 < o.box.x < right:
            right = o.box.x - 0.012
    return tb.moved(w=right - tb.x) if right < tb.x2 - 0.01 else tb


def _widen(s: RShape, tb: Box, shapes: list[RShape], decor: set[str]) -> Box | None:
    """Заголовок-образец «Графики» сидит в рамке под своё слово. Расширяем по свободной полосе до поля шаблона
    (центрированный — симметрично), чтобы заголовок-вывод не резался. decor — что можно не учитывать
    (декоративный текст, пример графика, который всё равно заменится)."""
    band = [o for o in shapes if o is not s and o.id not in decor and o.kind != "grp" and o.box.area < 0.45
            and not o.box.contains(tb) and o.box.y < tb.y2 - 0.005 and o.box.y2 > tb.y + 0.005]
    margin = max(0.03, min(tb.x, 0.08))
    right = min([o.box.x for o in band if o.box.x >= tb.x2 - 0.005] + [1 - margin]) - 0.01
    centered = bool(s.paras) and s.paras[0].align == "ctr"
    if centered:
        left = max([o.box.x2 for o in band if o.box.x2 <= tb.x + 0.005] + [margin]) + 0.01
        half = min(right - tb.cx, tb.cx - left)
        new = Box(x=tb.cx - half, y=tb.y, w=2 * half, h=tb.h)
    else:
        new = tb.moved(w=right - tb.x)
    for o in shapes:                         # расширенная рамка не должна зайти ни на один блок контента
        if o is s or o.id in decor or o.kind == "grp" or o.box.area > 0.45 or o.box.contains(tb):
            continue
        if o.kind == "sp" and not o.text and o.fill_kind in (None, "none") and o.line is None:
            continue
        if new.inter(o.box) > tb.inter(o.box) + 1e-5:     # расширение не должно увеличить наложение ни на один блок
            if centered or o.box.x <= tb.x2:
                return None
            new = new.moved(w=o.box.x - 0.01 - new.x)
    return new if new.w > tb.w * 1.15 else None


def _media_frame(s: RShape, shapes: list[RShape]) -> RShape | None:
    """Подпись «Вставить фото» лежит на белой рамке — рамка и есть место картинки."""
    if s.fill_kind in ("solid", "grad", "img") and s.box.area > 0.01:
        return None                            # подпись сама лежит в белой рамке — рамка это она
    cands = [o for o in shapes if o is not s and o.kind in ("sp", "pic") and not o.text and o.box.area < 0.45
             and o.box.contains_point(s.box.cx, s.box.cy) and o.box.area >= s.box.area * 0.9
             and ((abs(o.box.cx - s.box.cx) < 0.06 and abs(o.box.cy - s.box.cy) < 0.08)      # подпись по центру рамки
                  or o.box.area <= s.box.area * 6)]
    return min(cands, key=lambda o: o.box.area) if cands else None


def _in_slide(tb: Box, edge: float = 0.03) -> Box:
    """Рамка образца за краем слайда (подпись «33%» у дизайнера короткая и помещается в видимую часть):
    наш текст длиннее и переносился бы по невидимой ширине — режем рамку по краю слайда с полем."""
    x0, y0 = max(tb.x, edge) if tb.x < 0 else tb.x, max(tb.y, edge) if tb.y < 0 else tb.y
    x1 = min(tb.x2, 1 - edge) if tb.x2 > 1 else tb.x2
    y1 = min(tb.y2, 1 - edge) if tb.y2 > 1 else tb.y2
    if x1 - x0 < tb.w * 0.3 or y1 - y0 < tb.h * 0.3:
        return tb
    return Box(x=x0, y=y0, w=x1 - x0, h=y1 - y0)


def _grow_box(s: RShape, tb: Box, item_box: Box | None, shapes: list[RShape]) -> Box | None:
    """spAutoFit: рамка растёт вниз — ёмкость до низа карточки или до следующей фигуры под текстом."""
    if s.autofit != "shape":
        return None
    limit = min(item_box.y2 if item_box is not None else 0.93, 0.95)
    for o in shapes:
        empty = o.kind == "sp" and not o.text and o.fill_kind in (None, "none") and o.ph_type is None
        if o is s or o.kind not in ("sp", "pic", "frame") or empty:
            continue                        # пустой плейсхолдер (подзаголовок обложки) — препятствие: туда пойдёт текст
        if o.box.area > 0.3 or o.box.contains(tb):
            continue
        if o.box.y > tb.y + 0.01 and o.box.x < tb.x2 and o.box.x2 > tb.x:
            limit = min(limit, o.box.y - 0.01)
    return tb.moved(h=limit - tb.y) if limit > tb.y2 else None


# ----------------------------------------------------------------------------- pictures & visuals

def _picture_role(s: RShape, logo_media: set[str], slide_text: str, chart_slide: bool, photos: set[str]) -> PictureRole:
    if s.image_part and s.image_part in logo_media:
        return PictureRole.logo
    if chart_slide and 0.05 < s.box.area < 0.8:
        return PictureRole.chart_example
    if s.image_part in photos and 0.04 < s.box.area < 0.8:
        return PictureRole.photo               # фото на полслайда — демо-контент, а не фон
    if s.box.area > 0.45:
        return PictureRole.background
    if s.ph_type == "pic":
        return PictureRole.photo
    if s.box.area < 0.012:
        return PictureRole.icon
    if re.search(r"скриншот|мокап|screenshot", slide_text) and s.box.area > 0.05:
        return PictureRole.photo
    if s.image_part in photos and s.box.area > 0.04:
        return PictureRole.photo               # непрозрачное фото (JPEG) — демо-контент дизайнера, не декор
    return PictureRole.decor


def _visual_area(shapes: list[RShape], ts: TemplateSlide, media, in_chart: set[str]) -> VisualArea | None:
    for s in shapes:
        if s.kind == "frame" and s.chart_part:
            return VisualArea(box=s.box, replace_ids=[s.id], source="chart")
    for s in shapes:
        if s.kind == "frame" and s.table:
            return VisualArea(box=s.box, table_shape_id=s.id, source="table")
    ex = [p for p in ts.pictures if p.role == PictureRole.chart_example]
    if ex:
        return VisualArea(box=_union([p.box for p in ex]), replace_ids=[p.shape_id for p in ex] + sorted(in_chart),
                          source="picture")
    for s in shapes:                           # пустой плейсхолдер графика/таблицы/объекта из макета
        if s.kind == "sp" and s.ph_type in ("chart", "tbl", "dgm", "media", "clipArt") and not s.text and s.box.area > 0.08:
            return VisualArea(box=s.box, replace_ids=[s.id], source="placeholder")
    big = [(s, t) for s, t in media if s.box.area > 0.08 and not re.search(r"qr", t, re.I)]
    if big:
        s = max(big, key=lambda st: st[0].box.area)[0]
        return VisualArea(box=s.box, replace_ids=[s.id], source="placeholder")
    photos = [p for p in ts.pictures if p.role == PictureRole.photo and p.box.area > 0.1 and p.item is None]
    if photos:
        p = max(photos, key=lambda p: p.box.area)
        return VisualArea(box=p.box, replace_ids=[p.shape_id, *p.also], source="placeholder")
    bodies = [t for t in ts.texts if t.role in (SlotRole.body, SlotRole.bullets) and t.item is None
              and (t.grow_box or t.box).area > 0.12 and (t.grow_box or t.box).h >= 0.3 and (t.grow_box or t.box).w >= 0.35]
    if bodies and not ts.items:
        b = max(bodies, key=lambda t: (t.grow_box or t.box).area)
        return VisualArea(box=b.grow_box or b.box, replace_ids=[b.shape_id], source="body")
    return None


# ----------------------------------------------------------------------------- kind

def _classify(rs: RSlide, ts: TemplateSlide, title: RShape | None, slide_text: str,
              virtual: bool = False) -> tuple[SlideKind, float, str]:
    title_txt = (title.text if title else "").lower()
    hint = next((k for k, rx in _TITLE_HINTS if rx.search(title_txt)), None)
    if hint is None:
        hint = next((k for k, rx in _BODY_HINTS if rx.search(slide_text)), None)
    if hint is None and virtual:            # имя макета — сигнал только для виртуальных образцов (Google называет
        hint = next((k for k, rx in _LAYOUT_HINTS if rx.search(rs.layout_name or "")), None)   # всё «N_Титульный слайд»)
    n = len(ts.items)
    content = [t for t in ts.texts if t.role not in (SlotRole.title, SlotRole.subtitle, SlotRole.footer)]
    item_numbers = [t for t in ts.texts if t.item is not None and (t.role == SlotRole.number or any(p.role == SlotRole.number for p in t.parts))]
    item_ordinals = [t for t in ts.texts if t.item is not None and t.role == SlotRole.ordinal]
    names = [t for t in ts.texts if t.role == SlotRole.name]
    connectors = any(s.kind == "cxn" or (s.geometry or "").lower().endswith("arrow") for s in rs.shapes)
    big_title = bool(title and title.main_run and title.main_run.size >= 28)

    if ts.visual and ts.visual.source == "table":
        return SlideKind.table, 0.9, "таблица"
    if ts.visual and (ts.visual.source == "chart" or (ts.visual.source == "picture" and hint == SlideKind.chart)):
        return SlideKind.chart, 0.85, "график"
    if hint in (SlideKind.thanks, SlideKind.qa, SlideKind.agenda, SlideKind.title, SlideKind.cta):
        return hint, 0.85, hint.value
    if hint == SlideKind.two_columns and n < 2:
        return SlideKind.two_columns, 0.7, "две колонки"
    if rs.index == 0 and ts.texts:
        return SlideKind.title, 0.8, "титул"
    if hint == SlideKind.section and n == 0:
        return SlideKind.section, 0.8, "разделитель"
    if names and n <= 1 and big_title and len(content) <= 3:
        return SlideKind.title, 0.6, "титул со спикером"
    if n >= 2:
        if hint == SlideKind.process or item_ordinals or connectors:
            n_conn = sum(1 for s in rs.shapes if s.kind == "cxn")
            return SlideKind.process, 0.35 if n_conn >= 4 else 0.75, f"{n} шага" + (" (схема)" if n_conn >= 4 else "")
        if hint == SlideKind.team or len(names) >= 2:
            return SlideKind.team, 0.7, f"команда × {n}"
        if len(item_numbers) >= n * 0.5:
            return SlideKind.factoids, 0.75, f"{n} цифры"
        return SlideKind.cards, 0.7, f"{n} карточки"
    numbers = [t for t in ts.texts if t.role == SlotRole.number or any(p.role == SlotRole.number for p in t.parts)]
    if numbers and len(numbers) <= 6:
        return SlideKind.factoids, 0.65, f"{len(numbers)} цифры"
    if hint in (SlideKind.quote, SlideKind.team, SlideKind.process) or (
            hint == SlideKind.section and sum(t.max_chars for t in content) < 300):
        return hint, 0.6, hint.value
    if len(content) <= 1 and big_title and sum(t.max_chars for t in content) < 160 \
            and not any(p.role == PictureRole.photo for p in ts.pictures):
        return SlideKind.section, 0.55, "разделитель"
    if ts.visual and ts.visual.source in ("picture", "placeholder"):
        return SlideKind.image, 0.6, "иллюстрация + текст"
    bodies = [t for t in content if t.role in (SlotRole.body, SlotRole.bullets)]
    if len(bodies) == 2 and abs(bodies[0].box.w - bodies[1].box.w) < 0.05 and abs(bodies[0].box.y - bodies[1].box.y) < 0.1:
        return SlideKind.two_columns, 0.6, "две колонки"
    if bodies:
        return SlideKind.text, 0.55, "заголовок + текст"
    if title is not None:
        return SlideKind.section, 0.4, "заголовок"
    return SlideKind.other, 0.3, "прочее"


def _usable(shapes: list[RShape], ts: TemplateSlide, max_shapes: int) -> tuple[bool, str]:
    icons = sum(1 for p in ts.pictures if p.role == PictureRole.icon)
    if re.search(r"vertical|вертикальн", ts.layout_name or "", re.I):
        return False, "вертикальный текст"
    if len(shapes) > max_shapes or icons > 24:
        return False, "служебный слайд (библиотека иконок/логотипов)"
    fams = {r.family.lower() for s in shapes for r in s.runs}
    if any(any(k in f for k in _CODE_FONTS) for f in fams):
        return False, "слайд с кодом"
    if not ts.texts:
        return False, "нет текстовых слотов"
    if any(s.diagram for s in shapes):
        return False, "SmartArt образца"
    labels = sum(1 for t in ts.texts if t.role == SlotRole.label)
    if labels > 8 and labels > len(ts.texts) * 0.5:
        return False, "диаграмма Ганта / сетка подписей"
    loose_labels = sum(1 for t in ts.texts if t.item is None and t.role in (SlotRole.label, SlotRole.caption))
    loose_numbers = sum(1 for t in ts.texts if t.item is None and t.role == SlotRole.number)
    if loose_labels > 12 or loose_numbers > 8 or len(ts.texts) > 26:
        return False, "сложная схема (Гант/календарь): много несвязанных подписей"
    return True, ""


def _free_area(title: RShape, shapes: list[RShape], decor: list[RShape], ts: TemplateSlide) -> VisualArea | None:
    """Самый большой пустой прямоугольник под заголовком (сетка 48×27) — место под график на шаблоне,
    где нет ни одного «графического» образца. Так график встаёт и в незнакомый шаблон без слайдов-диаграмм."""
    gw, gh = 48, 27
    top = title.box.y2 + 0.03
    x0, x1, y1 = max(0.04, title.box.x - 0.01), 0.96, 0.92
    blocked = [[False] * gw for _ in range(gh)]
    text_ids = {t.shape_id for t in ts.texts}
    for o in list(shapes) + list(decor):
        if o is title or o.kind == "grp" or o.box.area > 0.45 or not o.box.visible():
            continue
        if o.kind == "sp" and not o.text and o.fill_kind in (None, "none") and o.line is None and o.id not in text_ids:
            continue
        b = o.box
        for gy in range(gh):
            cy = (gy + 0.5) / gh
            if not (b.y - 0.01 <= cy <= b.y2 + 0.01):
                continue
            for gx in range(gw):
                cx = (gx + 0.5) / gw
                if b.x - 0.01 <= cx <= b.x2 + 0.01:
                    blocked[gy][gx] = True
    for gy in range(gh):
        for gx in range(gw):
            cx, cy = (gx + 0.5) / gw, (gy + 0.5) / gh
            if not (x0 <= cx <= x1 and top <= cy <= y1):
                blocked[gy][gx] = True
    best, heights = (0, None), [0] * gw
    for gy in range(gh):                                  # максимальный прямоугольник в гистограмме, построчно
        heights = [0 if blocked[gy][gx] else heights[gx] + 1 for gx in range(gw)]
        stack: list[int] = []
        for gx in range(gw + 1):
            hcur = heights[gx] if gx < gw else 0
            while stack and heights[stack[-1]] >= hcur:
                h = heights[stack.pop()]
                left = stack[-1] + 1 if stack else 0
                area = h * (gx - left)
                if area > best[0]:
                    best = (area, (left, gy - h + 1, gx - left, h))
            stack.append(gx)
    if best[1] is None:
        return None
    lx, ly, w, h = best[1]
    box = Box(x=lx / gw + 0.01, y=ly / gh + 0.01, w=w / gw - 0.02, h=h / gh - 0.02)
    if box.area < 0.18 or box.w < 0.35 or box.h < 0.3:
        return None
    return VisualArea(box=box, source="free")


def _union(boxes: list[Box]) -> Box:
    x, y = min(b.x for b in boxes), min(b.y for b in boxes)
    return Box(x=x, y=y, w=max(b.x2 for b in boxes) - x, h=max(b.y2 for b in boxes) - y)


def _median(v: list[float]) -> float:
    s = sorted(v)
    return s[len(s) // 2] if s else 0.0
