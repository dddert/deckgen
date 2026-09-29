"""Исправления по замечаниям аудита. Каждое меняет DeckPlan; после применения колода пересобирается и
перепроверяется. Детерминированные исправления работают всегда; текстовые (сократить/переписать) —
через модель, без неё — детерминированной обрезкой или не применяются (замечание остаётся видимым).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from pydantic import create_model

from ..composing.fitter import truncate_words
from ..content.grounding import source_numbers, strip_ungrounded
from ..llm.client import ChatMessage, LLMClient, LLMError
from ..models import ContentPack, DeckPlan, Finding, PlannedSlide, SlotRole, StyleOp, TemplateModel
from ..ooxml.colors import apca_lc, best_text_color, contrast_ratio, distance
from ..prompts import PromptRegistry


@dataclass
class FixContext:
    tm: TemplateModel
    pack: ContentPack | None
    llm: LLMClient | None
    prompts: PromptRegistry | None
    max_bullets: int = 6
    max_words: int = 15


Fix = Callable[[DeckPlan, Finding, FixContext], bool]
FIXES: dict[str, Fix] = {}


def fix(name: str):
    def deco(fn: Fix) -> Fix:
        FIXES[name] = fn
        return fn
    return deco


def _slide(plan: DeckPlan, f: Finding) -> PlannedSlide | None:
    return plan.slides[f.slide_index] if 0 <= f.slide_index < len(plan.slides) else None


def _tf(ps: PlannedSlide, sid: str | None):
    return next((t for t in ps.texts if t.shape_id == sid), None)


@fix("text_overflow")
def text_overflow(plan, f, c) -> bool:
    ps = _slide(plan, f)
    tf = _tf(ps, f.shape_id) if ps else None
    if tf is None:
        return False
    slot = c.tm.slides[ps.template_slide].slot(tf.shape_id)
    base = slot.size_pt if slot else (tf.size_pt or 14)
    cur = tf.size_pt or base
    sizes = sorted(set(c.tm.design.font_sizes) | {r.size_pt for r in c.tm.design.type_scale})
    lower = [s for s in sizes if base * 0.7 <= s < cur - 0.01]
    if lower:
        tf.size_pt = lower[-1]
        return True
    text = "\n".join(tf.paragraphs)
    limit = int(len(text) * 0.75)
    short = _shorten_llm(ps, [(tf.shape_id, text, limit)], c) or {tf.shape_id: truncate_words(text, limit)}
    tf.paragraphs = [p for p in short[tf.shape_id].split("\n") if p.strip()] or tf.paragraphs[:1]
    return True


@fix("leftover_placeholder")
def leftover_placeholder(plan, f, c) -> bool:
    ps = _slide(plan, f)
    if ps is None or f.shape_id is None:
        return False
    slot = c.tm.slides[ps.template_slide].slot(f.shape_id)
    ps.texts = [t for t in ps.texts if t.shape_id != f.shape_id]
    if slot is not None and slot.container:
        from ..models import TextFill
        ps.texts.append(TextFill(shape_id=f.shape_id, paragraphs=[""], role=slot.role))
    elif f.shape_id not in ps.delete_ids:
        ps.delete_ids.append(f.shape_id)
    return True


@fix("drop_slide")
def drop_slide(plan, f, c) -> bool:
    if not 0 <= f.slide_index < len(plan.slides) or len(plan.slides) <= 3:
        return False
    plan.slides.pop(f.slide_index)
    for i, s in enumerate(plan.slides):
        s.index = i
    return True


def _nearest(hexv: str, palette: list[str]) -> str:
    return min(palette, key=lambda p: distance(hexv, p))


@fix("off_palette_color")
def off_palette_color(plan, f, c) -> bool:
    ps = _slide(plan, f)
    if ps is None:
        return False
    pal = [t.hex for t in c.tm.design.palette]
    colors = f.evidence.get("colors", [])
    if f.shape_id is None and ps.visual and ps.visual.chart:
        for s in ps.visual.chart.series:
            if s.color in colors:
                s.color = _nearest(s.color, pal)
        ps.visual.chart.point_colors = [_nearest(x, pal) if x in colors else x for x in ps.visual.chart.point_colors]
        return True
    if f.shape_id is None:
        return False
    for col in colors:
        ps.style_ops.append(StyleOp(shape_id=f.shape_id, op="recolor", match=col, value=_nearest(col, pal)))
    return bool(colors)


@fix("low_contrast")
def low_contrast(plan, f, c) -> bool:
    ps = _slide(plan, f)
    bg = f.evidence.get("bg")
    if ps is None or f.shape_id is None or not bg:
        return False
    d = c.tm.design
    floor = float(f.evidence.get("min_lc", 55.0))
    best = best_text_color(bg, [d.text_on_dark, d.text_on_light])        # сначала — цвета текста самого шаблона
    if abs(apca_lc(best, bg)) < floor:
        best = best_text_color(bg, [d.text_on_dark, d.text_on_light, *[t.hex for t in d.palette], "FFFFFF", "111111"])
    if abs(apca_lc(best, bg)) <= float(f.evidence.get("lc", 0)) + 5:
        return False
    ps.style_ops = [o for o in ps.style_ops if not (o.shape_id == f.shape_id and o.op == "recolor")]
    ps.style_ops.append(StyleOp(shape_id=f.shape_id, op="recolor", value=best))
    return True


@fix("chart_labels")
def chart_labels(plan, f, c) -> bool:
    ps = _slide(plan, f)
    ch = ps.visual.chart if ps and ps.visual else None
    if ch is None:
        return False
    table = next((t for t in (c.pack.tables if c.pack else []) if t.id == ps.visual.data_ref), None)
    if ch.chart_type not in ("pie", "doughnut"):
        ch.x_title = ch.x_title or (table.columns[0] if table else "Категория")
        ch.y_title = ch.y_title or (ch.series[0].name if ch.series else "Значение")
    ch.legend = ch.legend or len(ch.series) > 1 or ch.chart_type in ("pie", "doughnut")
    return True


@fix("trim_bullets")
def trim_bullets(plan, f, c) -> bool:
    ps = _slide(plan, f)
    if ps is None:
        return False
    changed = False
    for tf in ps.texts:
        if tf.role == SlotRole.bullets and len(tf.paragraphs) > c.max_bullets:
            tf.paragraphs = tf.paragraphs[: c.max_bullets]
            changed = True
    return changed


@fix("shorten_text")
def shorten_text(plan, f, c) -> bool:
    ps = _slide(plan, f)
    tf = _tf(ps, f.shape_id) if ps else None
    if tf is None:
        return False
    long = [(i, p) for i, p in enumerate(tf.paragraphs) if len(p.split()) > c.max_words]
    if not long:
        return False
    res = _shorten_llm(ps, [(f"{tf.shape_id}_{i}", p, 90) for i, p in long], c)
    for i, p in long:
        new = (res or {}).get(f"{tf.shape_id}_{i}") or " ".join(p.split()[: c.max_words])
        tf.paragraphs[i] = " ".join(new.split()[: c.max_words]).rstrip(",;:")
    return True


@fix("strip_numbers")
def strip_numbers(plan, f, c) -> bool:
    ps = _slide(plan, f)
    if ps is None or c.pack is None:
        return False
    src = source_numbers(c.pack)
    targets = [t for t in ps.texts if f.shape_id is None or t.shape_id == f.shape_id]
    changed = False
    for tf in targets:
        if tf.role in (SlotRole.ordinal, SlotRole.footer):
            continue
        new = [strip_ungrounded(p, src) if tf.role != SlotRole.number else p for p in tf.paragraphs]
        if new != tf.paragraphs:
            tf.paragraphs = [p for p in new if p.strip()] or [""]
            changed = True
    return changed


@fix("snap_x")
def snap_x(plan, f, c) -> bool:
    ps = _slide(plan, f)
    g = f.evidence.get("guide")
    if ps is None or f.shape_id is None or g is None or f.box is None:
        return False
    tf = _tf(ps, f.shape_id)
    if tf is not None and tf.fit_box is not None:
        tf.fit_box = tf.fit_box.moved(x=g)
        return True
    if f.shape_id in ps.moves:
        ps.moves[f.shape_id] = ps.moves[f.shape_id].moved(x=g)
        return True
    if ps.visual is not None:
        ps.visual.box = ps.visual.box.moved(x=g)
        return True
    return False


@fix("clamp_box")
def clamp_box(plan, f, c) -> bool:
    ps = _slide(plan, f)
    if ps is None:
        return False
    if ps.visual is not None and f.box is not None:
        b = ps.visual.box
        x, y = max(0.01, b.x), max(0.01, b.y)
        ps.visual.box = b.moved(x=x, y=y, w=min(b.w, 0.99 - x), h=min(b.h, 0.99 - y))
        return True
    tf = _tf(ps, f.shape_id)
    if tf is not None and tf.fit_box is not None:
        b = tf.fit_box
        tf.fit_box = b.moved(w=min(b.w, 0.99 - b.x), h=min(b.h, 0.99 - b.y))
        return True
    return False


@fix("snap_font_size")
def snap_font_size(plan, f, c) -> bool:
    ps = _slide(plan, f)
    if ps is None or f.shape_id is None:
        return False
    scale = sorted(set(c.tm.design.font_sizes) | {r.size_pt for r in c.tm.design.type_scale})
    for sz in f.evidence.get("sizes", []):
        ps.style_ops.append(StyleOp(shape_id=f.shape_id, op="size", value=min(scale, key=lambda x: abs(x - sz))))
    return True


@fix("replace_font")
def replace_font(plan, f, c) -> bool:
    ps = _slide(plan, f)
    if ps is None or f.shape_id is None or not c.tm.design.fonts:
        return False
    ps.style_ops.append(StyleOp(shape_id=f.shape_id, op="font", value=c.tm.design.fonts[0]))
    return True


@fix("fit_image")
def fit_image(plan, f, c) -> bool:
    return False          # сборщик всегда обрезает картинку под рамку (srcRect); замечание — сигнал о чужой картинке шаблона


@fix("rewrite")
def rewrite(plan, f, c) -> bool:
    """Переписать тексты слайда по замечанию (вопросы 1–3, 7–9, 11 VLM-аудита) — только с моделью."""
    ps = _slide(plan, f)
    if ps is None or c.llm is None or c.prompts is None:
        return False
    t = c.tm.slides[ps.template_slide]
    slots = []
    for tf in ps.texts:
        slot = t.slot(tf.shape_id)
        if slot is None or tf.role in (SlotRole.ordinal, SlotRole.footer) or (slot.parts and len(slot.parts) > 1):
            continue
        slots.append({"key": f"s{tf.shape_id}", "sid": tf.shape_id, "label": tf.role.value,
                      "limit": f"≤{max(8, slot.max_chars)} зн.", "current": "\n".join(tf.paragraphs)})
    if not slots:
        return False
    titles = [s.title for s in plan.slides]
    try:
        schema = create_model("Rewrite", **{s["key"]: (str, ...) for s in slots})
        prompt = c.prompts.render("rewrite", finding=f.message, deck_title=titles[0] if titles else "",
                                  position=f.slide_index + 1,
                                  prev_title=titles[f.slide_index - 1] if f.slide_index > 0 else "—",
                                  next_title=titles[f.slide_index + 1] if f.slide_index + 1 < len(titles) else "—",
                                  facts=(c.pack.facts[:12] if c.pack else []), slots=slots)
        ans = c.llm.complete_json([ChatMessage("user", prompt)], schema, temperature=0.4, max_tokens=1200)
    except LLMError:
        return False
    src = source_numbers(c.pack) if c.pack else set()
    for s in slots:
        tf = _tf(ps, s["sid"])
        new = str(getattr(ans, s["key"])).strip()
        if tf is None or not new:
            continue
        new = strip_ungrounded(new, src) if src else new
        tf.paragraphs = [p.strip(" •-") for p in new.split("\n") if p.strip()] if tf.role == SlotRole.bullets else [new]
        if tf.role == SlotRole.title:
            ps.title = new
    return True


def _shorten_llm(ps: PlannedSlide, items: list[tuple[str, str, int]], c: FixContext) -> dict[str, str] | None:
    if c.llm is None or c.prompts is None:
        return None
    try:
        keys = {f"s{re.sub(r'[^0-9a-zA-Z_]', '_', k)}": (k, t, lim) for k, t, lim in items}
        schema = create_model("Short", **{k: (str, ...) for k in keys})
        prompt = c.prompts.render("shorten", title=ps.title,
                                  items=[{"key": k, "limit": lim, "length": len(t), "text": t} for k, (_, t, lim) in keys.items()])
        ans = c.llm.complete_json([ChatMessage("user", prompt)], schema, temperature=0.2, max_tokens=900)
        return {orig: str(getattr(ans, k)) for k, (orig, _, _) in keys.items()}
    except LLMError:
        return None


def apply_fixes(plan: DeckPlan, findings: list[Finding], selected: set[str] | None, c: FixContext) -> list[str]:
    """selected — id замечаний, check_id или fix_id (None — все с fix_id). Возвращает id применённых замечаний."""
    done = []
    order = sorted(findings, key=lambda f: (f.fix_id == "drop_slide", -f.slide_index))   # удаление слайдов — последним
    dropped: set[int] = set()
    for f in order:
        if not f.fix_id or f.fix_id not in FIXES:
            continue
        if selected is not None and not ({f.id, f.check_id, f.fix_id} & selected):
            continue
        if f.fix_id == "drop_slide" and f.slide_index in dropped:
            continue
        try:
            ok = FIXES[f.fix_id](plan, f, c)
        except Exception:  # noqa: BLE001 — одно неудачное исправление не должно ронять остальные
            ok = False
        if ok:
            done.append(f.id)
            if f.fix_id == "drop_slide":
                dropped.add(f.slide_index)
    return done
