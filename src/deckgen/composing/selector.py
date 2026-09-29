"""Выбор слайда-образца под каждый слайд каркаса.

Скоринг прозрачный (для защиты): тип -> число элементов -> место под график -> картинки ->
ёмкость текста -> плотность под вариант -> уверенность разбора -> разнообразие.
Среди образцов, близких к лучшему, вариант берёт «свой» по счёту — так три варианта различаются
ещё и макетами, оставаясь в пределах шаблона.
"""
from __future__ import annotations

from collections import Counter

from ..models import OutlineSlide, PictureRole, SlideKind, SlotRole, TemplateModel, TemplateSlide
from ..planning.skeleton import FALLBACK
from .variants import Variant

_NEAR = 1.2


def select_slides(slides: list[OutlineSlide], tm: TemplateModel, v: Variant, images_available: bool) -> list[TemplateSlide]:
    usable = tm.usable()
    content_dark = _content_dark(usable)
    used: Counter = Counter()
    out: list[TemplateSlide] = []
    prev: int | None = None
    for s in slides:
        scored = [(score(t, s, v, used, prev, images_available, content_dark), t) for t in usable]
        scored = [st for st in scored if st[0] > -50]
        if not scored:
            scored = [(0.0, max(usable, key=lambda t: len(t.texts)))]
        scored.sort(key=lambda st: -st[0])
        best = scored[0][0]
        near = [t for sc, t in scored if sc >= best - _NEAR]
        pick = near[min(v.rank, len(near) - 1)]
        used[pick.index] += 1
        prev = pick.index
        out.append(pick)
    return out


def score(t: TemplateSlide, s: OutlineSlide, v: Variant, used: Counter, prev: int | None,
          images_available: bool, content_dark: bool) -> float:
    chain = [s.kind, *FALLBACK.get(s.kind, [])]
    if t.kind in chain:
        sc = 6.0 - 1.5 * chain.index(t.kind)
    elif s.viz is not None and t.visual is not None and t.kind in (SlideKind.text, SlideKind.section, SlideKind.two_columns,
                                                                   SlideKind.image, SlideKind.other):
        sc = 1.5                               # любой образец с местом под график годится для данных
    else:
        return -100.0
    if t.layout_photos:                        # демо-фото в макете не заменить: контентный образец почти не берём;
        sc -= 2.0 if t.kind in (SlideKind.title, SlideKind.section, SlideKind.thanks) else 8.0   # на титуле это обычно фон бренда
    need = s.n_items
    if t.n_items and need:
        if t.n_items == need:
            sc += 3.0
        elif t.n_items > need:
            sc += 1.0 - 0.6 * (t.n_items - need)
        else:
            sc -= 2.5 * (need - t.n_items)
    elif need and not t.n_items:
        bullets = any(x.role == SlotRole.bullets for x in t.texts)
        sc -= 0.5 if bullets else 2.0
    elif t.n_items and not need:
        sc -= 0.8 * t.n_items
    if s.viz is not None:
        if t.visual is None:
            sc -= 6.0
        else:
            sc += 3.0 + (2.0 if (s.viz.kind == "table") == (t.visual.source == "table") else 0.0)
            sc += 1.0 if t.visual.source in ("chart", "picture", "table") else 0.0
    elif t.visual is not None and t.visual.source in ("chart", "table"):
        sc -= 3.0
    photos = [p for p in t.pictures if p.role == PictureRole.photo]
    if photos:
        big = sum(1 for p in photos if p.box.area > 0.05)
        if images_available and (s.image or s.kind in (SlideKind.image, SlideKind.title)):
            sc += 1.0 + v.visual_bonus
        else:
            sc -= 1.5 * big + 0.3 * (len(photos) - big)
    if any(p.role == PictureRole.chart_example for p in t.pictures) and s.viz is None:
        sc -= 4.0
    need_chars = sum(len(p) for p in s.points) + len(s.message) // 2
    if need_chars and t.text_capacity:
        ratio = t.text_capacity * v.text_ratio / max(40, need_chars)
        sc += -2.0 if ratio < 0.5 else -1.0 if ratio > 6 else 0.5
    title_slot = next((x for x in t.texts if x.role == SlotRole.title), None)
    if title_slot is not None and s.title:
        cap = title_slot.max_chars
        if len(s.title) > cap * 1.1:
            sc -= min(4.0, 1.0 + (len(s.title) / max(8, cap) - 1.1) * 2.5)
    elif title_slot is None and s.kind not in (SlideKind.title, SlideKind.thanks, SlideKind.qa):
        sc -= 2.0
    if t.density == v.density:
        sc += 1.0
    if v.visual_bonus > 0 and (t.visual is not None or any(p.role in (PictureRole.decor, PictureRole.icon) for p in t.pictures)):
        sc += 0.4 * v.visual_bonus
    if s.kind not in (SlideKind.title, SlideKind.section, SlideKind.thanks, SlideKind.cta, SlideKind.qa):
        sc += 0.6 if t.dark == content_dark else -0.6
    names = [x for x in t.texts if x.role == SlotRole.name]
    if names and s.kind not in (SlideKind.team,):
        sc -= 0.7 * len(names)                   # «Имя Фамилия» без данных о спикере — пустые места
    sc += t.confidence
    sc -= 1.6 * used[t.index]
    if prev == t.index:
        sc -= 2.5
    return sc


def _content_dark(slides: list[TemplateSlide]) -> bool:
    content = [t for t in slides if t.kind in (SlideKind.cards, SlideKind.text, SlideKind.factoids, SlideKind.process,
                                               SlideKind.two_columns, SlideKind.chart, SlideKind.table)]
    return sum(t.dark for t in content) > len(content) / 2 if content else False
