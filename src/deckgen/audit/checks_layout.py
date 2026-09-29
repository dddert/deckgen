"""Вёрстка (Приложение 1): границы, наложения, переполнение, обрез краем, выравнивание, поля, пропорции.
Эталон геометрии — слайд-образец: считаются только фигуры, которые генератор добавил или сдвинул/изменил."""
from __future__ import annotations

from itertools import combinations

from ..models import CheckCategory, Finding, Severity
from .base import AuditContext, check, finding

C = CheckCategory.layout


def _content(sv):
    """Содержательные фигуры: текст слотов, новые фигуры, графики/таблицы, картинки. Декоративный текст шаблона
    («0 0 0 / / /») — не контент."""
    slots = {t.shape_id for t in sv.ts.texts}
    return [s for s in sv.shapes if s.kind in ("frame", "pic")
            or (s.kind == "sp" and s.text.strip() and (s.id in slots or sv.is_new(s)))]


@check("layout.out_of_bounds", C, "Элемент вышел за границы слайда", fix="clamp_box")
def out_of_bounds(ctx: AuditContext) -> list[Finding]:
    out = []
    for sv in ctx.slides:
        for s in _content(sv):
            if not (sv.is_new(s) or sv.is_changed(s)) or s.rot:
                continue
            b = s.box
            if b.x < -0.005 or b.y < -0.005 or b.x2 > 1.005 or b.y2 > 1.005:
                out.append(finding("layout.out_of_bounds", sv.index, "Элемент выходит за край слайда", shape=s,
                                   severity=Severity.error))
    return out


@check("layout.overlap", C, "Два блока наложились друг на друга")
def overlap(ctx: AuditContext) -> list[Finding]:
    out = []
    for sv in ctx.slides:
        items = [s for s in _content(sv) if s.box.area < 0.5]
        for a, b in combinations(items, 2):
            if a.parent is not None and a.parent == b.parent and not (sv.is_new(a) or sv.is_new(b)):
                continue
            if not (sv.is_new(a) or sv.is_new(b) or sv.is_changed(a) or sv.is_changed(b)):
                continue
            inter = _text_extent(ctx, sv, a).inter(_text_extent(ctx, sv, b))
            small = min(a.box.area, b.box.area) or 1e-9
            r = inter / small
            if r <= 0.06:
                continue
            ta, tb = sv.ts.shape_boxes.get(a.id), sv.ts.shape_boxes.get(b.id)
            if ta is not None and tb is not None and ta.inter(tb) / (min(ta.area, tb.area) or 1e-9) >= r - 0.03:
                continue                                       # так было задумано в шаблоне
            out.append(finding("layout.overlap", sv.index, f"Блоки пересекаются на {r:.0%}", shape=a,
                               severity=Severity.error if r > 0.2 else Severity.warning, other=b.id, ratio=round(r, 3)))
    return out


def _text_extent(ctx: AuditContext, sv, s):
    """Для текста — фактически занятая часть рамки (текст сверху), а не вся рамка."""
    if s.kind != "sp" or not s.text.strip() or ctx.measurer is None:
        return s.box
    need = _need_h(ctx, s)
    tb = s.text_box
    h = min(tb.h, need / ctx.tm.design.slide_h_in) if s.anchor in ("t", None) else tb.h
    return tb.moved(h=max(0.005, h))


def _need_h(ctx: AuditContext, s) -> float:
    d = ctx.tm.design
    w = s.text_box.w * d.slide_w_in
    total = 0.0
    if not s.wrap:                                     # без переноса: высота = число строк
        w = 1e6
    for p in s.paras:
        lines, cur = [], []
        for r in p.runs + [None]:                      # строки абзаца (a:br) меряем каждую своим кеглем
            if r is None or r.text == "\n":
                lines.append(cur)
                cur = []
            else:
                cur.append(r)
        for ln in lines:
            runs = [r for r in ln if r.text.strip()]
            if not runs:
                size = ln[0].size if ln else (p.runs[0].size if p.runs else 12)
                total += size * 1.2 * p.line_spacing / 72 if len(lines) > 1 or not p.text.strip() else 0
                continue
            size = max(r.size for r in runs)
            main = max(runs, key=lambda r: len(r.text))
            total += ctx.measurer.height_in("".join(r.text for r in ln), main.family, size, w, main.bold, p.line_spacing)
    return total


@check("layout.text_overflow", C, "Текст не поместился в свою рамку", fix="text_overflow")
def text_overflow(ctx: AuditContext) -> list[Finding]:
    out = []
    H = ctx.tm.design.slide_h_in
    for sv in ctx.slides:
        for s in sv.text_shapes():
            need = _need_h(ctx, s)
            have = s.text_box.h * H
            slot = sv.ts.slot(s.id)
            if s.autofit == "shape":                            # рамка растёт сама — но не ниже карточки/следующего блока
                limit = slot.grow_box if slot is not None and slot.grow_box is not None else None
                have = max(have, (limit.h if limit else 1 - s.text_box.y) * H)
            if slot is not None and slot.demo_text.strip() and ctx.measurer is not None:
                demo = ctx.measurer.height_in(slot.demo_text, slot.family, slot.size_pt,
                                              1e6 if slot.nowrap else s.text_box.w * ctx.tm.design.slide_w_in, slot.bold,
                                              slot.line_spacing)
                have = max(have, demo)                          # дизайнер сам выпустил цифру за рамку — это замысел
            if need > have * 1.12 + 0.02:
                sev = Severity.warning if s.autofit == "norm" else Severity.error
                out.append(finding("layout.text_overflow", sv.index, f"Текст требует {need:.2f} in при рамке {have:.2f} in",
                                   shape=s, severity=sev, need_in=round(need, 3), have_in=round(have, 3)))
    return out


@check("layout.text_cut", C, "Текст обрезан краем слайда")
def text_cut(ctx: AuditContext) -> list[Finding]:
    out = []
    H = ctx.tm.design.slide_h_in
    for sv in ctx.slides:
        for s in sv.text_shapes():
            bottom = s.text_box.y + _need_h(ctx, s) / H
            if (s.box.x2 > 1.01 or bottom > 1.0 or s.box.x < -0.01) and (sv.is_new(s) or sv.is_changed(s) or bottom > 1.0):
                out.append(finding("layout.text_cut", sv.index, "Текст уходит за край слайда", shape=s, severity=Severity.error))
    return out


@check("layout.alignment", C, "Блоки не выровнены по направляющим макета", fix="snap_x")
def alignment(ctx: AuditContext) -> list[Finding]:
    out = []
    for sv in ctx.slides:
        guides = sorted({round(b.x, 4) for b in sv.ts.shape_boxes.values()} | {round(b.x2, 4) for b in sv.ts.shape_boxes.values()})
        for s in _content(sv):
            if not (sv.is_new(s) or sv.is_changed(s)):
                continue
            near = [g for g in guides if 0.004 < abs(g - s.box.x) < 0.015]
            if near and not any(abs(g - s.box.x) <= 0.004 for g in guides):
                out.append(finding("layout.alignment", sv.index, f"Левый край {s.box.x:.3f} почти совпадает с направляющей {near[0]:.3f}",
                                   shape=s, guide=near[0]))
    return out


@check("layout.margins", C, "Контент заходит в поля у краёв")
def margins(ctx: AuditContext) -> list[Finding]:
    out = []
    m = ctx.tm.design.safe_area
    for sv in ctx.slides:
        for s in _content(sv):
            if not (sv.is_new(s) or sv.is_changed(s)) or s.box.area > 0.5:
                continue
            b, t = s.box, sv.ts.shape_boxes.get(s.id)
            left, right = min(m.x, t.x if t else 1) - 0.015, max(m.x2, t.x2 if t else 0) + 0.015
            bottom = max(min(0.985, m.y2 + 0.06), t.y2 + 0.01 if t else 0)
            if b.x < left or b.x2 > right or b.y2 > bottom:
                out.append(finding("layout.margins", sv.index, "Элемент заходит в поля шаблона", shape=s))
    return out


@check("layout.image_aspect", C, "Картинка растянута, пропорции нарушены", fix="fit_image")
def image_aspect(ctx: AuditContext) -> list[Finding]:
    out = []
    d = ctx.tm.design
    for sv in ctx.slides:
        for s in sv.shapes:
            if s.kind != "pic" or not s.image_px or not (sv.is_new(s) or sv.is_changed(s)):
                continue
            l, t, r, b = s.crop
            src = (s.image_px[0] * (1 - l - r)) / max(1, s.image_px[1] * (1 - t - b))
            dst = (s.box.w * d.slide_w_in) / max(1e-6, s.box.h * d.slide_h_in)
            if abs(src / dst - 1) > 0.03:
                out.append(finding("layout.image_aspect", sv.index, f"Пропорции {src:.2f} при рамке {dst:.2f}", shape=s,
                                   severity=Severity.error))
    return out


@check("layout.text_over_graphics", C, "Текст лёг на графику (картинку, кольцо диаграммы, паттерн макета)",
       fix="text_overflow")
def text_over_graphics(ctx: AuditContext) -> list[Finding]:
    """Готовый файл: где фактически стоит текст (высота по метрикам, строка без переноса — по ширине) и что под ним.
    Клетка «на графике» — если сверху лежит непрозрачная фигура, в клетке край объекта или кольцо вокруг цифры
    (та же карта занятости, что и при разборе шаблона). Слоты, где дизайнер сам положил текст на картинку, не проверяются: у них
    карта занятости шаблона не сузила рамку (slot.limited = False)."""
    from ..parsing.occupancy import blocked_ratio
    out = []
    if ctx.reader is None or ctx.measurer is None:
        return out
    W, H = ctx.tm.design.slide_w_in, ctx.tm.design.slide_h_in
    for sv in ctx.slides:
        for s in sv.text_shapes():
            slot = sv.ts.slot(s.id)
            if slot is not None and not slot.limited and not sv.is_new(s):
                continue
            ext = _placed_text(ctx, s, W, H)
            r = blocked_ratio(s, ext, sv.shapes, sv.decor, ctx.reader, sv.rs.background.color or "FFFFFF")
            if r > 0.06:
                out.append(finding("layout.text_over_graphics", sv.index, f"{r:.0%} текста лежит на графике", shape=s,
                                   severity=Severity.error if r > 0.15 else Severity.warning, ratio=round(r, 3)))
    return out


def _placed_text(ctx: AuditContext, s, W: float, H: float):
    """Прямоугольник, который текст занимает на слайде: по привязке рамки и выравниванию абзаца."""
    from ..models import Box
    tb = s.text_box
    h = min(max(0.005, _need_h(ctx, s) / H), 1.0)
    y = tb.y if s.anchor in ("t", None) else (tb.y + (tb.h - h) / 2 if s.anchor == "ctr" else tb.y2 - h)
    w = tb.w
    if not s.wrap:                                     # строка без переноса может быть шире рамки
        widest = 0.0
        for p in s.paras:
            runs = [r for r in p.runs if r.text.strip()]
            if runs:
                main = max(runs, key=lambda r: len(r.text))
                widest = max(widest, ctx.measurer.width_in(p.text, main.family, max(r.size for r in runs), main.bold) / W)
        w = max(0.005, widest)
    align = s.paras[0].align if s.paras else "l"
    x = tb.x if s.wrap or align not in ("ctr", "r") else (tb.cx - w / 2 if align == "ctr" else tb.x2 - w)
    return Box(x=x, y=y, w=w, h=h)
