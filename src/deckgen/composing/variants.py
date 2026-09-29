"""Три варианта вёрстки одного контента на одном шаблоне (п. 5 ТЗ).

Ось различий — «плотность ↔ визуальность». Она меняет сразу три вещи, поэтому варианты визуально
различимы: (1) число слайдов и группировку, (2) выбор макетов (карточки/фактоиды/текст/таблица),
(3) способ показа данных (график или таблица, цифры в фактоидах или в тексте) и длину текстов.
При этом все три собираются только из слайдов-образцов шаблона — палитра, шрифты, макеты, логотипы
не меняются, и template-проверки аудита проходят одинаково.

  balanced — «Сбалансированный»: одна мысль на слайд, лучшие по совпадению макеты, данные графиками.
  visual   — «Визуальный»: цифры выносятся в фактоиды, данные — графики, короткие тексты (60% ёмкости),
             иллюстрации в слотах картинок, разделители разделов; предпочтение макетам с визуалом.
  compact  — «Компактный (executive)»: на ~30% меньше слайдов (соседние тезисные слайды сливаются),
             без разделителей и содержания, данные — таблицами, тексты до 95% ёмкости, плотные макеты.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..content.grounding import numbers_in
from ..models import ContentPack, Outline, OutlineSlide, SlideKind, TemplateModel
from ..planning.skeleton import available_kinds, item_counts


@dataclass(frozen=True)
class Variant:
    name: str
    label: str
    density: str               # предпочитаемая плотность макета
    text_ratio: float          # доля ёмкости слота, которую занимает текст
    bullets_max: int
    rank: int                  # какой из близких по качеству макетов брать (разнообразие между вариантами)
    visual_bonus: float        # премия макетам с картинкой/графиком
    style_note: str


VARIANTS = {
    "balanced": Variant("balanced", "Сбалансированный", "medium", 0.8, 5, 0, 0.5,
                        "сбалансированный: полные, но короткие формулировки"),
    "visual": Variant("visual", "Визуальный", "low", 0.6, 3, 1, 1.5,
                      "визуальный: минимум слов, максимум крупных цифр и коротких тезисов"),
    "compact": Variant("compact", "Компактный", "high", 0.95, 6, 2, -0.5,
                       "компактный: плотно и информативно, каждое слово несёт факт"),
}


def get(name: str) -> Variant:
    return VARIANTS.get(name, VARIANTS["balanced"])


def apply_variant(outline: Outline, variant: str, tm: TemplateModel, pack: ContentPack) -> Outline:
    v = get(variant)
    avail = available_kinds(tm)
    slides = [s.model_copy(deep=True) for s in outline.slides]
    if v.name == "compact":
        slides = [s for s in slides if s.kind not in (SlideKind.section, SlideKind.agenda, SlideKind.qa)]
        slides = _merge_neighbors(slides, tm)
        for s in slides:
            if s.viz and s.viz.kind == "chart" and SlideKind.table in avail and _small_table(pack, s.viz.data_ref):
                s.viz.kind, s.kind = "table", SlideKind.table
    elif v.name == "visual":
        for s in slides:
            nums = [f for f in s.fact_refs if any(x.id == f and numbers_in(x.text) for x in pack.facts)]
            if s.kind in (SlideKind.cards, SlideKind.text, SlideKind.two_columns) and len(nums) >= 2 and SlideKind.factoids in avail:
                counts = item_counts(tm, SlideKind.factoids) or [len(nums)]
                s.kind = SlideKind.factoids
                s.n_items = min(counts, key=lambda c: abs(c - len(nums)))
            if s.viz and s.viz.kind == "table" and SlideKind.chart in avail:
                s.viz.kind, s.kind = "chart", SlideKind.chart
            s.points = s.points[: max(v.bullets_max, s.n_items or 0)]
        slides = _add_sections(slides, avail)
    for s in slides:
        if s.kind in (SlideKind.text, SlideKind.two_columns):
            s.points = s.points[: v.bullets_max]
    for i, s in enumerate(slides):
        s.index = i
    return outline.model_copy(update={"slides": slides})


def _small_table(pack: ContentPack, ref: str) -> bool:
    t = next((t for t in pack.tables if t.id == ref), None)
    return t is not None and len(t.rows) <= 7 and len(t.columns) <= 5


def _merge_neighbors(slides: list[OutlineSlide], tm: TemplateModel) -> list[OutlineSlide]:
    """Два соседних тезисных слайда -> один с карточками/списком (если в шаблоне есть макет на столько элементов)."""
    out: list[OutlineSlide] = []
    mergeable = (SlideKind.cards, SlideKind.text, SlideKind.two_columns)
    card_counts = item_counts(tm, SlideKind.cards)
    i = 0
    while i < len(slides):
        s = slides[i]
        nxt = slides[i + 1] if i + 1 < len(slides) else None
        if nxt is not None and s.kind in mergeable and nxt.kind in mergeable and not s.viz and not nxt.viz:
            pts = (s.points or [s.message])[:3] + (nxt.points or [nxt.message])[:3]
            fits = [c for c in card_counts if c >= len(pts)]
            if fits:
                merged = s.model_copy(update={
                    "kind": SlideKind.cards, "n_items": min(fits), "points": pts,
                    "message": f"{s.message} {nxt.message}".strip(), "fact_refs": s.fact_refs + nxt.fact_refs,
                })
                out.append(merged)
                i += 2
                continue
        out.append(s)
        i += 1
    return out


def _add_sections(slides: list[OutlineSlide], avail: set[SlideKind]) -> list[OutlineSlide]:
    """Визуальный вариант: разделитель перед блоком данных, если в шаблоне есть разделитель и слайдов достаточно."""
    if SlideKind.section not in avail or any(s.kind == SlideKind.section for s in slides) or len(slides) < 8:
        return slides
    first_data = next((i for i, s in enumerate(slides) if s.viz is not None or s.kind == SlideKind.factoids), None)
    if first_data is None or first_data < 2:
        return slides
    sec = OutlineSlide(kind=SlideKind.section, title="Результаты в цифрах", message="Что показали данные")
    return slides[:first_data] + [sec] + slides[first_data:]
