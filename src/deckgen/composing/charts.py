"""Графики и таблицы в стиле шаблона: цвета серий из палитры, подписи — цветом текста шаблона под фон слайда,
шрифт — гарнитура шаблона. Лимиты аудита (≤5 серий, таблица ≤7×5) применяются здесь, а не постфактум.
"""
from __future__ import annotations

import re

from ..models import ChartSeries, ChartSpec, ColorRole, DataTable, DesignSystem, TableSpec, VisualFill, VizSpec
from ..ooxml.colors import contrast_ratio, distance, mix


def series_colors(design: DesignSystem, bg: str, n: int) -> list[str]:
    pool = [t.hex for t in design.palette if t.role == ColorRole.primary]
    pool += [t.hex for t in design.palette if t.role == ColorRole.accent]
    pool += [t.hex for t in design.palette if t.role in (ColorRole.neutral, ColorRole.surface)]
    ok: list[str] = []
    for c in pool:
        if contrast_ratio(c, bg) < 1.6 or any(distance(c, x) < 60 for x in ok):
            continue
        ok.append(c)
    if not ok:
        ok = [design.text_on_dark if contrast_ratio(design.text_on_dark, bg) > 3 else design.text_on_light]
    while len(ok) < n:                       # не хватает цветов палитры — оттенки основного (всё ещё «из палитры» по смыслу)
        ok.append(mix(ok[len(ok) % max(1, len(ok))], bg, 0.35 + 0.15 * (len(ok) // 3)))
    return ok[:n]


def _num(v) -> float | None:
    try:
        return float(str(v).replace(" ", "").replace(" ", "").replace(",", ".").replace("%", ""))
    except (TypeError, ValueError):
        return None


def numeric_cols(t: DataTable) -> list[int]:
    return [i for i in range(1, len(t.columns)) if t.rows and all(_num(r[i]) is not None for r in t.rows if i < len(r))]


def choose_chart_type(t: DataTable, cols: list[int]) -> str:
    first = [str(r[0]) for r in t.rows]
    if re.search(r"(19|20)\d\d|q[1-4]|кв|янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек|неделя|month|week", " ".join(first), re.I) \
            and all(re.search(r"\d|янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек", x, re.I) for x in first):
        return "line"
    if len(cols) == 1 and 2 <= len(t.rows) <= 5:
        s = sum(_num(r[cols[0]]) or 0 for r in t.rows)
        if abs(s - 100) < 2:
            return "doughnut"
    if max((len(x) for x in first), default=0) > 14 or len(t.rows) > 6:
        return "bar"
    return "column"


def comparable(t: DataTable, cols: list[int], focus: str) -> list[int]:
    """Серии разного масштаба (люди 420 и проценты 71) на одной оси не читаются — оставляем ближайшую к мысли."""
    if len(cols) <= 1:
        return cols
    peaks = [max(abs(_num(r[i]) or 0) for r in t.rows) or 1e-9 for i in cols]
    if max(peaks) / min(peaks) <= 8:
        return cols
    words = {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", focus.lower())}
    return [max(cols, key=lambda i: (len(words & {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", t.columns[i].lower())}), -cols.index(i)))]


def unit_of(col: str) -> str | None:
    m = re.search(r"\(([^)]+)\)|,\s*([^,]+)$", col)
    return (m.group(1) or m.group(2)).strip() if m else None


def make_visual(viz: VizSpec, t: DataTable, design: DesignSystem, bg: str, dark: bool, box, *, remove_ids=None,
                table_shape_id=None, max_series: int = 5, max_rows: int = 7, max_cols: int = 5, focus: str = "",
                font_size: float | None = None) -> VisualFill:
    text = design.text_on_dark if dark else design.text_on_light
    if contrast_ratio(text, bg) < 4.5:
        text = "FFFFFF" if contrast_ratio("FFFFFF", bg) > contrast_ratio("000000", bg) else "000000"
    body = design.role("body") or design.role("caption")
    fam = body.family if body else (design.fonts[0] if design.fonts else "Arial")
    size = font_size or max(9.0, min(16.0, (body.size_pt if body else 12) * (1.0 if design.slide_w_in > 11 else 1.2)))
    primary = next((c.hex for c in design.palette if c.role == ColorRole.primary), series_colors(design, bg, 1)[0])
    cols = numeric_cols(t)
    kind = viz.kind if cols else "table"
    vf = VisualFill(kind=kind, box=box, remove_ids=list(remove_ids or []), table_shape_id=table_shape_id, data_ref=t.id,
                    font_family=fam, font_size=size, text_color=text, grid_color=mix(text, bg, 0.78), header_fill=primary)
    if kind == "table":
        cols_idx = list(range(min(len(t.columns), max_cols)))
        vf.table = TableSpec(columns=[t.columns[i] for i in cols_idx],
                             rows=[[_fmt_cell(r[i]) if i < len(r) else "" for i in cols_idx] for r in t.rows[:max_rows]])
        return vf
    if viz.series:
        chosen = [i for i in cols if t.columns[i] in viz.series] or cols
    else:
        chosen = comparable(t, cols, focus)
    chosen = chosen[:max_series]
    ctype = viz.chart_type or choose_chart_type(t, chosen)
    if ctype in ("pie", "doughnut"):
        chosen = chosen[:1]
    colors = series_colors(design, bg, max(len(chosen), len(t.rows) if ctype in ("pie", "doughnut") else 0))
    unit = unit_of(t.columns[chosen[0]]) if chosen else None
    pct = all("%" in str(r[chosen[0]]) for r in t.rows) or (unit or "").strip() in ("%", "доля")
    vf.chart = ChartSpec(
        chart_type=ctype, categories=[str(r[0]) for r in t.rows],
        series=[ChartSeries(name=t.columns[i], values=[_num(r[i]) or 0.0 for r in t.rows], color=colors[k % len(colors)])
                for k, i in enumerate(chosen)],
        x_title=None if ctype in ("pie", "doughnut") else t.columns[0],
        y_title=None if ctype in ("pie", "doughnut") else (unit or (t.columns[chosen[0]] if len(chosen) == 1 else None)),
        legend=len(chosen) > 1 or ctype in ("pie", "doughnut"),
        data_labels=len(t.rows) * len(chosen) <= 24,
        number_format='0"%"' if pct and ctype in ("pie", "doughnut") else "General",
        point_colors=colors if ctype in ("pie", "doughnut") else [],
    )
    return vf


def _fmt_cell(v) -> str:
    if v is None:
        return ""
    s = str(v)
    n = _num(s)
    if n is not None and re.fullmatch(r"-?\d+\.\d+", s):
        return s.replace(".", ",")
    return s
