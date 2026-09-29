"""Плотность (Приложение 1): буллеты, длина буллета, размер таблицы, число серий, заполнение слайда."""
from __future__ import annotations

from ..models import Box, CheckCategory, Finding, Severity
from .base import SERVICE_KINDS, AuditContext, check, finding

C = CheckCategory.density


def _bullets(s) -> list[str]:
    paras = [p for p in s.paras if p.text.strip()]
    if len(paras) >= 2 or any(p.bullet for p in paras):
        return [p.text.strip() for p in paras if p.bullet or len(paras) >= 2]
    return []


@check("density.bullets_count", C, "Больше 6 буллетов на слайде", fix="trim_bullets")
def bullets_count(ctx: AuditContext) -> list[Finding]:
    out = []
    for sv in ctx.slides:
        n = sum(len(_bullets(s)) for s in sv.text_shapes() if sv.ts.slot(s.id) is not None and sv.ts.slot(s.id).role.value == "bullets")
        if n > ctx.cfg.max_bullets:
            out.append(finding("density.bullets_count", sv.index, f"{n} пунктов списка (лимит {ctx.cfg.max_bullets})", count=n))
    return out


@check("density.bullet_length", C, "Буллет длиннее 15 слов", fix="shorten_text")
def bullet_length(ctx: AuditContext) -> list[Finding]:
    out = []
    for sv in ctx.slides:
        for s in sv.text_shapes():
            long = [b for b in _bullets(s) if len(b.split()) > ctx.cfg.max_words_per_bullet]
            if long:
                out.append(finding("density.bullet_length", sv.index, f"Пункт из {len(long[0].split())} слов", shape=s,
                                   words=len(long[0].split())))
    return out


@check("density.table_size", C, "Таблица больше 7 строк или 5 колонок")
def table_size(ctx: AuditContext) -> list[Finding]:
    out = []
    for sv in ctx.slides:
        for s in sv.shapes:
            if s.table:
                rows, cols = len(s.table) - 1, max(len(r) for r in s.table)
                if rows > ctx.cfg.table_max_rows or cols > ctx.cfg.table_max_cols:
                    out.append(finding("density.table_size", sv.index, f"Таблица {rows}×{cols}", shape=s, severity=Severity.error))
    return out


@check("density.chart_series", C, "Больше 5 серий на диаграмме")
def chart_series(ctx: AuditContext) -> list[Finding]:
    out = []
    for i, ps in enumerate(ctx.plan.slides):
        if ps.visual and ps.visual.chart and len(ps.visual.chart.series) > ctx.cfg.chart_max_series:
            out.append(finding("density.chart_series", i, f"{len(ps.visual.chart.series)} серий", box=ps.visual.box,
                               severity=Severity.error))
    return out


@check("density.fill_ratio", C, "Слайд заполнен меньше чем на четверть или больше чем на три четверти")
def fill_ratio(ctx: AuditContext) -> list[Finding]:
    out = []
    H = ctx.tm.design.slide_h_in
    for sv in ctx.slides:
        if sv.kind in SERVICE_KINDS:
            continue
        boxes = []
        for s in sv.shapes:
            if s.kind == "grp" or (s.box.area > 0.5 and s.kind != "frame"):
                continue
            if s.kind == "sp" and s.text.strip() and ctx.measurer is not None:
                from .checks_layout import _need_h
                h = min(s.box.h, _need_h(ctx, s) / H + (s.box.h - s.text_box.h))
                boxes.append(s.box.moved(h=max(0.01, h)))
            elif s.kind in ("frame",) or (s.kind == "pic" and s.box.area > 0.01):
                boxes.append(s.box)
            elif s.kind == "sp" and s.fill_kind in ("solid", "grad") and s.box.area > 0.02:
                boxes.append(s.box)                                   # подложки карточек
        ratio = _union(boxes)
        if ratio < ctx.cfg.fill_ratio_min or ratio > ctx.cfg.fill_ratio_max:
            out.append(finding("density.fill_ratio", sv.index, f"Заполнение {ratio:.0%}", ratio=round(ratio, 3),
                               severity=Severity.info if ratio > ctx.cfg.fill_ratio_max else Severity.warning))
    return out


def _union(boxes: list[Box], grid: int = 80) -> float:
    cells = set()
    for b in boxes:
        x0, y0 = max(0, int(b.x * grid)), max(0, int(b.y * grid))
        x1, y1 = min(grid, int(b.x2 * grid) + 1), min(grid, int(b.y2 * grid) + 1)
        cells.update((x, y) for x in range(x0, x1) for y in range(y0, y1))
    return len(cells) / grid ** 2
