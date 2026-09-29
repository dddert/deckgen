"""Каркас + разобранный шаблон -> DeckPlan одного варианта.

Для каждого слайда каркаса: образец (selector) -> лишние элементы удаляются, оставшиеся равномерно
перераспределяются -> тексты под слоты (writer, параллельно) -> подгонка кеглем из шкалы шаблона (fitter)
-> сокращение того, что не влезло (shorten) -> график/таблица на место примера -> картинки.
Незаполненные слоты не остаются с текстом-заглушкой: фигура удаляется (или очищается, если это подложка).
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from ..llm.client import LLMClient
from ..models import (Box, ContentPack, DeckPlan, DesignSystem, ImageFill, Outline, OutlineSlide, PictureRole,
                      PlannedSlide, SlotRole, StyleOp, TemplateModel, TemplateSlide, TextFill, TextSlot)
from ..ooxml.colors import apca_lc, best_text_color, hex_to_rgb, readable_tint
from ..parsing.fonts import TextMeasurer
from ..prompts import PromptRegistry
from ..settings import Settings
from . import charts
from .fitter import fit, truncate_words
from .selector import select_slides
from .variants import get as get_variant
from .writer import shorten, write_slide


def compose_variant(outline: Outline, tm: TemplateModel, pack: ContentPack, variant: str, s: Settings,
                    llm: LLMClient | None, prompts: PromptRegistry | None, images, log=lambda m: None) -> DeckPlan:
    t0 = time.perf_counter()
    v = get_variant(variant)
    d = tm.design
    W, H = d.slide_w_in, d.slide_h_in
    measurer = TextMeasurer(d.font_files)
    sizes = sorted(set(d.font_sizes) | {r.size_pt for r in d.type_scale})
    slides = outline.slides
    chosen = select_slides(slides, tm, v, images.available if images else False)
    keeps = [_keep(o, t) for o, t in zip(slides, chosen)]
    deck = {"title": outline.title, "total": len(slides), "titles": [o.title for o in slides]}

    with ThreadPoolExecutor(max_workers=max(1, s.llm.max_concurrency)) as ex:
        written = list(ex.map(lambda i: write_slide(slides[i], chosen[i], keeps[i], pack, v, deck, llm, prompts),
                              range(len(slides))))
    t_write = time.perf_counter()

    planned: list[PlannedSlide] = []
    overflow: list[list[tuple[str, str, int]]] = []
    for i, (o, t, keep, (texts, _mode)) in enumerate(zip(slides, chosen, keeps, written)):
        ps, over = _plan_slide(i, o, t, keep, texts, pack, tm, s, measurer, sizes, images, W, H)
        planned.append(ps)
        overflow.append(over)

    # сокращение непоместившегося — одним параллельным раундом
    todo = [i for i, over in enumerate(overflow) if over]
    if todo and s.composing.shorten_rounds > 0:
        with ThreadPoolExecutor(max_workers=max(1, s.llm.max_concurrency)) as ex:
            short = list(ex.map(lambda i: shorten(slides[i], overflow[i], llm, prompts), todo))
        for i, res in zip(todo, short):
            _apply_short(planned[i], chosen[i], res, dict((a, c) for a, _, c in overflow[i]), measurer, sizes, s, W, H)

    modes = {m for _, m in written}
    return DeckPlan(
        deck_id=uuid.uuid4().hex[:10], variant=variant, template_id=d.template_id, template_file=d.source_file,
        content_pack_id=pack.id, slides=planned, prompt_versions=prompts.versions() if prompts else {},
        timings={"select_write": t_write - t0, "compose": time.perf_counter() - t0},
        llm_mode="llm" if modes == {"llm"} else "offline" if modes <= {"offline"} else "mixed",
    )


def _keep(o: OutlineSlide, t: TemplateSlide) -> int:
    if not t.n_items:
        return 0
    want = o.n_items or len(o.points) or t.n_items
    if o.points:
        want = min(want, len(o.points))       # содержания меньше, чем элементов в макете — лишние убираем
    return max(1, min(t.n_items, want))


def _plan_slide(i: int, o: OutlineSlide, t: TemplateSlide, keep: int, texts: dict[str, list[str]], pack: ContentPack,
                tm: TemplateModel, s: Settings, m: TextMeasurer, sizes: list[float], images, W: float, H: float):
    ps = PlannedSlide(index=i, outline_index=o.index, template_slide=t.index, layout_part=t.layout_part,
                      kind=o.kind, title=o.title)
    # --- элементы: лишние удалить, оставшиеся перераспределить
    removed = [it for it in t.items if it.index >= keep]
    for it in removed:
        ps.delete_ids += it.shape_ids
    if removed and s.composing.reflow_items:
        ps.moves = _reflow(t, keep)
    # --- график / таблица
    visual_ids: set[str] = set()
    table = next((x for x in pack.tables if o.viz and x.id == o.viz.data_ref), None)
    if o.viz is not None and table is not None and t.visual is not None:
        va = t.visual
        remove = list(va.replace_ids)
        tpl_table = va.table_shape_id if (o.viz.kind == "table" or not charts.numeric_cols(table)) else None
        if va.table_shape_id and tpl_table is None:
            remove.append(va.table_shape_id)
        ps.visual = charts.make_visual(o.viz, table, tm.design, t.bg_color, t.dark, va.box, remove_ids=remove,
                                       table_shape_id=tpl_table, max_series=s.audit.chart_max_series,
                                       max_rows=s.audit.table_max_rows, max_cols=s.audit.table_max_cols,
                                       focus=f"{o.title} {o.message}")
        visual_ids = set(remove) | ({tpl_table} if tpl_table else set())
        vb = ps.visual.box
        example = va.source in ("picture", "placeholder")
        protect = {x.shape_id for x in t.texts if x.role in (SlotRole.title, SlotRole.subtitle, SlotRole.footer)}
        protect |= {p.shape_id for p in t.pictures if p.role in (PictureRole.logo, PictureRole.background)}
        for sid, b in t.shape_boxes.items():      # зона графика: подписи, выноски, линии примера не нужны
            if sid in protect or sid in visual_ids or b.area > 0.5:
                continue
            if vb.contains_point(b.cx, b.cy, pad=0.01):
                visual_ids.add(sid)
                ps.delete_ids.append(sid)
        if va.source == "picture":                # легенда примера (DAU/MAU и т.п.) описывает чужой график
            for it in t.items:
                ps.delete_ids += [x for x in it.shape_ids if x not in ps.delete_ids and x not in protect]
                visual_ids |= set(it.shape_ids)
        del example
    elif t.visual is not None and t.visual.source in ("picture", "chart"):
        ps.delete_ids += [x for x in t.visual.replace_ids if x not in ps.delete_ids]   # пример графика без данных — убрать
        visual_ids = set(t.visual.replace_ids)
    # --- тексты
    over: list[tuple[str, str, int]] = []
    for slot in t.texts:
        if slot.shape_id in ps.delete_ids or slot.shape_id in visual_ids or slot.role == SlotRole.footer:
            continue
        if slot.role == SlotRole.ordinal:
            demo = slot.demo_text.strip()
            if slot.item is None:                     # нумерация вне карточек — как у дизайнера
                val = demo
            else:
                n = slot.item + 1
                val = f"{n:02d}" if demo.startswith("0") else str(n)
            ps.texts.append(TextFill(shape_id=slot.shape_id, paragraphs=[val], role=slot.role))
            _readable(ps, slot, slot.size_pt, tm.design)
            continue
        vals = [x for x in texts.get(slot.shape_id, []) if x is not None]
        if not any(x.strip() for x in vals):
            if slot.container and slot.item is not None and _minor_plate(t, slot):
                ps.delete_ids += _plate_with_content(t, slot)   # пустая строка-плашка в колонке — вместе с иконкой
            elif slot.container:
                ps.texts.append(TextFill(shape_id=slot.shape_id, paragraphs=[""], role=slot.role))
            else:
                ps.delete_ids.append(slot.shape_id)
                if slot.role == SlotRole.name:
                    ps.delete_ids += _avatar_near(t, slot)
            continue
        if slot.parts and len(slot.parts) > 1:
            vals = (vals + [""] * len(slot.parts))[: len(slot.parts)]
        min_scale = min(0.7, s.composing.min_font_scale) if slot.role == SlotRole.title else s.composing.min_font_scale
        f = fit(vals, slot, m, sizes, min_scale, W, H)
        nowrap = False
        if not f.fits and slot.role == SlotRole.number:
            bare = [_bare_number(v) for v in vals]         # «2,3 ч» не влезает в кружок — число, единица есть в подписи
            fb = fit(bare, slot, m, sizes, s.composing.min_font_scale, W, H)
            if fb.fits:
                vals, f = bare, fb
            elif len("".join(vals)) <= 8:
                nowrap, f = True, fit(vals, slot.model_copy(update={"nowrap": True}), m, sizes,
                                      s.composing.min_font_scale, W, H)   # одна строка шире рамки — как «10%» в образце
        if not f.fits and slot.role == SlotRole.bullets:
            while len(vals) > 1 and not f.fits:          # сначала убираем хвост списка, потом сокращаем
                vals = vals[:-1]
                f = fit(vals, slot, m, sizes, s.composing.min_font_scale, W, H)
        ps.texts.append(TextFill(shape_id=slot.shape_id, paragraphs=vals, size_pt=f.size_pt, role=slot.role,
                                 fit_box=_shape_box(t, slot, f.fit_box), nowrap=nowrap or slot.nowrap,
                                 text_area=_text_area(t, slot)))
        _readable(ps, slot, f.size_pt or slot.size_pt, tm.design)
        if not f.fits and not slot.parts:
            over.append((slot.shape_id, "\n".join(vals), max(8, int(f.max_chars * 0.9))))
    # --- картинки
    for p in t.pictures:
        if p.role != PictureRole.photo or p.shape_id in visual_ids or p.shape_id in ps.delete_ids:
            continue
        if p.item is not None and p.item >= keep:
            continue
        got = images.get(o, (p.box.w * W) / max(1e-6, p.box.h * H), t.dark) if images else None
        if got:
            ps.images.append(ImageFill(shape_id=p.shape_id, path=got[0], source=got[1]))
        else:
            ps.delete_ids.append(p.shape_id)
        ps.delete_ids += [x for x in p.also if x not in ps.delete_ids]
    return ps, over


def _minor_plate(t: TemplateSlide, slot) -> bool:
    """Плашка занимает малую часть карточки (строка списка), а не является самой карточкой."""
    it = next((i for i in t.items if i.index == slot.item), None)
    box = t.shape_boxes.get(slot.shape_id)
    return it is not None and box is not None and box.area < 0.35 * it.box.area


def _plate_with_content(t: TemplateSlide, slot) -> list[str]:
    box = t.shape_boxes.get(slot.shape_id)
    out = [slot.shape_id]
    if box is None:
        return out
    text_ids = {x.shape_id for x in t.texts if x.shape_id != slot.shape_id}
    for sid, b in t.shape_boxes.items():
        if sid != slot.shape_id and sid not in text_ids and b.area < box.area and box.contains_point(b.cx, b.cy):
            out.append(sid)
    return out


def _bare_number(text: str) -> str:
    import re
    m = re.search(r"[~≈<>+\-−]?\d+(?:[.,]\d+)?", text)
    return (m.group() + ("%" if "%" in text else "")) if m else text


def _neutral(h: str) -> bool:
    r, g, b = hex_to_rgb(h)
    return max(r, g, b) - min(r, g, b) < 48       # чёрный, белый, серые — «цвет по умолчанию», а не акцент дизайнера


def _readable(ps: PlannedSlide, slot: TextSlot, size: float, d: DesignSystem) -> None:
    """Цвет текста против фона под ним (APCA). WCAG 2 считает чёрный на синем #0077FF хорошим (5.1:1),
    глаз — нет (Lc 37). Нейтральный цвет (чёрный/серый/белый) меняем на читаемый цвет текста самого шаблона.
    Акцентный цвет — решение дизайнера: крупные синие цифры на чёрном остаются, мелкий синий текст на тёмной
    карточке осветляется в том же оттенке ровно до порога читаемости."""
    if not slot.bg_color:
        return
    if slot.parts and len(slot.parts) > 1:        # «Заголовок + текст» в одной фигуре: каждую часть отдельно
        tf = next((t for t in ps.texts if t.shape_id == slot.shape_id), None)
        run = 0
        for i, part in enumerate(slot.parts):
            if tf is None or i >= len(tf.paragraphs) or not tf.paragraphs[i].strip():
                continue
            col = _readable_color(part.color, slot.bg_color, part.size_pt, part.bold, d)
            if col:
                ps.style_ops.append(StyleOp(shape_id=slot.shape_id, op="recolor", value=col, match=f"run:{run}"))
            run += 1
        return
    col = _readable_color(slot.color, slot.bg_color, size, slot.bold, d)
    if col:
        ps.style_ops.append(StyleOp(shape_id=slot.shape_id, op="recolor", value=col))


def _readable_color(color: str | None, bg: str, size: float, bold: bool, d: DesignSystem) -> str | None:
    if not color:
        return None
    floor = 45.0 if size >= 24 or (bold and size >= 18) else 55.0
    now = abs(apca_lc(color, bg))
    if now >= floor:
        return None
    if not _neutral(color):                       # акцент: мелкий текст — тот же оттенок, светлее/темнее
        return readable_tint(color, bg, floor) if size < 24 or now < 30 else None
    best = best_text_color(bg, [d.text_on_dark, d.text_on_light])
    if abs(apca_lc(best, bg)) < floor:
        best = best_text_color(bg, [d.text_on_dark, d.text_on_light, "FFFFFF", "111111"])
    return best if abs(apca_lc(best, bg)) > now + 8 else None


def _text_area(t: TemplateSlide, slot: TextSlot) -> Box | None:
    """Плашка с текстом, на которую сверху легла графика (иконка, картинка): плашку не двигаем,
    текст уводим в свободную часть внутренними полями."""
    if not (slot.container and slot.limited) or slot.shape_id not in t.shape_boxes:
        return None
    return slot.box


def _shape_box(t: TemplateSlide, slot, fb: Box | None) -> Box | None:
    """fitter возвращает рамку текста; фигуре нужна рамка с внутренними полями. Если рамку сузили
    (под текстом графика: шар на карточке, паттерн обложки) — фигуру тоже сужаем, иначе перенос строк
    и привязка по центру пойдут по старой рамке и текст снова ляжет на графику. Плашку не трогаем (_text_area)."""
    old = t.shape_boxes.get(slot.shape_id)
    tb = slot.text_box or slot.box
    if slot.container and slot.limited:
        return None
    if fb is None and (slot.box.w < tb.w - 0.005 or (slot.limited and abs(slot.box.h - tb.h) > 0.005)):
        fb = slot.box if slot.limited else slot.box.moved(h=tb.h)
    if fb is None or old is None:
        return None
    return Box(x=old.x + (fb.x - tb.x), y=old.y + (fb.y - tb.y), w=old.w + (fb.w - tb.w), h=old.h + (fb.h - tb.h))


def _avatar_near(t: TemplateSlide, slot) -> list[str]:
    """Кружок-аватар рядом с удалённым «Имя Фамилия» — пустой без фото."""
    text_ids = {x.shape_id for x in t.texts}
    pics = {p.shape_id: p for p in t.pictures}
    out = []
    for sid, b in t.shape_boxes.items():
        if sid in text_ids or (sid in pics and pics[sid].role.value in ("logo", "background")):
            continue
        if b.area < 0.012 and abs(b.cy - slot.box.cy) < max(0.04, slot.box.h) and 0 < slot.box.x - b.x2 + 0.005 < 0.08:
            out.append(sid)
    return out


def _apply_short(ps: PlannedSlide, t: TemplateSlide, res: dict[str, str], limits: dict[str, int],
                 m: TextMeasurer, sizes: list[float], s: Settings, W: float, H: float) -> None:
    slots = {x.shape_id: x for x in t.texts}
    for tf in ps.texts:
        if tf.shape_id not in res:
            continue
        slot = slots[tf.shape_id]
        new = res[tf.shape_id]
        paras = [x.strip() for x in new.split("\n") if x.strip()] if slot.role == SlotRole.bullets else [new.strip()]
        f = fit(paras, slot, m, sizes, s.composing.min_font_scale, W, H)
        lim = limits.get(tf.shape_id, slot.max_chars)
        while not f.fits and lim > 8:                    # крайний случай — обрезка по словам
            lim = int(lim * 0.85)
            paras = [truncate_words(p, max(8, lim // max(1, len(paras)))) for p in paras]
            f = fit(paras, slot, m, sizes, s.composing.min_font_scale, W, H)
        tf.paragraphs, tf.size_pt, tf.fit_box = paras, f.size_pt, _shape_box(t, slot, f.fit_box)


def _reflow(t: TemplateSlide, keep: int) -> dict[str, Box]:
    """Оставшиеся карточки ряда — по центру прежнего ряда с исходным шагом (ритм дизайнера сохраняется)."""
    kept = [it for it in t.items if it.index < keep]
    rows = {it.row for it in kept}
    moves: dict[str, Box] = {}
    if len(rows) != 1:
        return moves
    row = rows.pop()
    full = sorted((it for it in t.items if it.row == row), key=lambda it: it.col)
    mine = sorted(kept, key=lambda it: it.col)
    if len(full) <= len(mine) or len(full) < 2:
        return moves
    gap = full[1].box.x - full[0].box.x2
    span0, span1 = full[0].box.x, full[-1].box.x2
    total = sum(it.box.w for it in mine) + gap * (len(mine) - 1)
    x = (span0 + span1) / 2 - total / 2
    for it in mine:
        dx = x - it.box.x
        for rid in it.roots:
            b = t.shape_boxes.get(rid)
            if b is not None and abs(dx) > 1e-4:
                moves[rid] = b.moved(x=b.x + dx)
        x += it.box.w + gap
    return moves
