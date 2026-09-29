"""Каркас колоды (planning): состав, порядок, заголовки-выводы, число элементов, данные для графиков.

Один короткий вызов модели на колоду; тексты пишет slide_writer параллельно по слайдам уже под
выбранный макет. Модель видит только те типы слайдов, которые реально есть в шаблоне, и числа
элементов, под которые есть макеты. Всё, что вернула модель, проверяется детерминированно.
Без модели (offline / сбой) каркас строит эвристика — колода собирается всегда.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..content.grounding import numbers_in, source_numbers
from ..content.textfix import sanitize, unwrap
from ..llm.client import ChatMessage, LLMClient, LLMError
from ..models import ContentPack, DataTable, Outline, OutlineSlide, SlideKind, TemplateModel, VizSpec
from ..prompts import PromptRegistry

_HINTS = {
    SlideKind.title: "титул", SlideKind.section: "разделитель раздела", SlideKind.agenda: "содержание",
    SlideKind.text: "заголовок + текст или список", SlideKind.two_columns: "две колонки текста",
    SlideKind.cards: "карточки-преимущества/аргументы", SlideKind.factoids: "крупные цифры с подписями",
    SlideKind.process: "шаги / этапы / таймлайн", SlideKind.table: "таблица данных", SlideKind.chart: "график по таблице",
    SlideKind.quote: "цитата", SlideKind.image: "иллюстрация + текст", SlideKind.team: "спикер / команда",
    SlideKind.cta: "призыв к действию / контакты", SlideKind.qa: "вопросы", SlideKind.thanks: "спасибо / финал",
}
_ITEM_KINDS = {SlideKind.cards, SlideKind.factoids, SlideKind.process, SlideKind.agenda, SlideKind.team}
# если в шаблоне нет нужного типа — чем заменить (порядок важен)
FALLBACK: dict[SlideKind, list[SlideKind]] = {
    SlideKind.factoids: [SlideKind.cards, SlideKind.text],
    SlideKind.cards: [SlideKind.two_columns, SlideKind.text, SlideKind.process],
    SlideKind.process: [SlideKind.cards, SlideKind.text],
    SlideKind.two_columns: [SlideKind.cards, SlideKind.text],
    SlideKind.chart: [SlideKind.image, SlideKind.table, SlideKind.text],
    SlideKind.table: [SlideKind.chart, SlideKind.text],
    SlideKind.agenda: [SlideKind.process, SlideKind.cards, SlideKind.text],
    SlideKind.section: [SlideKind.title, SlideKind.text],
    SlideKind.quote: [SlideKind.text, SlideKind.section],
    SlideKind.image: [SlideKind.text, SlideKind.cards],
    SlideKind.team: [SlideKind.cards, SlideKind.text],
    SlideKind.cta: [SlideKind.thanks, SlideKind.section, SlideKind.text],
    SlideKind.qa: [SlideKind.thanks, SlideKind.section],
    SlideKind.thanks: [SlideKind.cta, SlideKind.section, SlideKind.title],
    SlideKind.text: [SlideKind.two_columns, SlideKind.cards],
}


class _SkTable(BaseModel):
    id: str
    title: str
    columns: list[str]
    rows: list[list[str]]


class _SkSlide(BaseModel):
    kind: str
    title: str
    message: str = ""
    n_items: int = 0
    points: list[str] = Field(default_factory=list)
    fact_refs: list[str] = Field(default_factory=list)
    data_ref: str | None = None
    chart_type: Literal["column", "bar", "line", "area", "pie", "doughnut"] | None = None
    image: str | None = None


class _Skeleton(BaseModel):
    title: str
    tables: list[_SkTable] = Field(default_factory=list)
    slides: list[_SkSlide]


# ----------------------------------------------------------------------------- каталог шаблона

def catalog(tm: TemplateModel) -> list[dict]:
    kinds: dict[SlideKind, set[int]] = {}
    for t in tm.usable():
        kinds.setdefault(t.kind, set())
        if t.n_items:
            kinds[t.kind].add(t.n_items)
    out = []
    for k, items in kinds.items():
        if k == SlideKind.other:
            continue
        out.append({"kind": k.value, "hint": _HINTS.get(k, k.value),
                    "items": ", ".join(str(n) for n in sorted(items)) if items and k in _ITEM_KINDS else ""})
    return out


def available_kinds(tm: TemplateModel) -> set[SlideKind]:
    return {t.kind for t in tm.usable()}


def resolve_kind(kind: SlideKind, avail: set[SlideKind]) -> SlideKind:
    if kind in avail:
        return kind
    for k in FALLBACK.get(kind, []):
        if k in avail:
            return k
    for k in (SlideKind.text, SlideKind.cards, SlideKind.two_columns, SlideKind.section, SlideKind.title):
        if k in avail:
            return k
    return next(iter(avail))


def item_counts(tm: TemplateModel, kind: SlideKind) -> list[int]:
    return sorted({t.n_items for t in tm.usable() if t.kind == kind and t.n_items})


# ----------------------------------------------------------------------------- LLM

def build_outline(pack: ContentPack, tm: TemplateModel, target: int, llm: LLMClient | None,
                  prompts: PromptRegistry | None, log=lambda m: None) -> Outline:
    if llm is not None and prompts is not None:
        try:
            return _llm_outline(pack, tm, target, llm, prompts)
        except LLMError as e:
            log(f"skeleton: модель недоступна или сломала JSON ({e}) — эвристический каркас")
    return heuristic_outline(pack, tm, target)


def _llm_outline(pack: ContentPack, tm: TemplateModel, target: int, llm: LLMClient, prompts: PromptRegistry) -> Outline:
    prompt = prompts.render(
        "skeleton", purpose=pack.purpose.value, audience=pack.audience, language=pack.language,
        target_slides=target, must_include=pack.must_include, brief=pack.brief[:7000],
        facts=pack.facts[:40], tables=pack.tables[:10], catalog=catalog(tm),
    )
    sk = llm.complete_json([ChatMessage("user", prompt)], _Skeleton, max_tokens=3500, temperature=0.4)
    sources = source_numbers(pack)
    for t in sk.tables:                     # таблица из текста — только если все её числа есть в тексте
        nums = [n for r in t.rows for c in r for n in numbers_in(str(c))]
        if t.rows and t.columns and nums and all(n in sources for n in nums) and t.id not in {x.id for x in pack.tables}:
            pack.tables.append(DataTable(id=t.id, title=t.title, columns=t.columns, rows=t.rows, source="text"))
    slides = []
    for s in sk.slides:
        try:
            kind = SlideKind(s.kind)
        except ValueError:
            kind = SlideKind.text
        viz = None
        if s.data_ref and any(t.id == s.data_ref for t in pack.tables):
            viz = VizSpec(kind="table" if kind == SlideKind.table else "chart", data_ref=s.data_ref, chart_type=s.chart_type)
        slides.append(OutlineSlide(kind=kind, title=s.title.strip().rstrip("."), message=s.message, n_items=s.n_items,
                                   points=[p for p in s.points if p.strip()][:8], viz=viz,
                                   fact_refs=[f for f in s.fact_refs if any(x.id == f for x in pack.facts)],
                                   image=s.image))
    out = Outline(title=sk.title or pack.title, slides=slides, language=pack.language,
                  prompt_versions=prompts.versions(), source="llm")
    return normalize(out, pack, tm, target)


# ----------------------------------------------------------------------------- проверка каркаса

def normalize(o: Outline, pack: ContentPack, tm: TemplateModel, target: int) -> Outline:
    avail = available_kinds(tm)
    used_tables = set()
    for s in o.slides:
        if s.kind in (SlideKind.chart, SlideKind.table) and s.viz is None:
            free = [t for t in pack.tables if t.id not in used_tables]
            if free:
                s.viz = VizSpec(kind="table" if s.kind == SlideKind.table else "chart", data_ref=free[0].id)
            else:
                s.kind = SlideKind.cards if s.points else SlideKind.text
        if s.viz is not None:
            used_tables.add(s.viz.data_ref)
            s.kind = s.kind if s.kind in (SlideKind.chart, SlideKind.table) else SlideKind.chart
        s.kind = resolve_kind(s.kind, avail) if s.viz is None else _viz_kind(s.kind, avail)
        counts = item_counts(tm, s.kind)
        if counts:
            want = s.n_items or len(s.points) or counts[0]
            s.n_items = min(counts, key=lambda c: (c < want, abs(c - want)))
            if len(s.points) > s.n_items:
                s.points = s.points[: s.n_items]
        else:
            s.n_items = 0
    if o.slides and o.slides[0].kind != SlideKind.title and SlideKind.title in avail:
        o.slides.insert(0, OutlineSlide(kind=SlideKind.title, title=o.title, points=[pack.audience or ""]))
    if SlideKind.thanks in avail and (not o.slides or o.slides[-1].kind != SlideKind.thanks):
        o.slides.append(OutlineSlide(kind=SlideKind.thanks, title="Спасибо за внимание" if pack.language == "ru" else "Thank you"))
    # лимит объёма: убираем лишние слайды из середины (разделители — первыми)
    while len(o.slides) > target + 2:
        mid = [i for i, s in enumerate(o.slides[1:-1], start=1)]
        sec = [i for i in mid if o.slides[i].kind == SlideKind.section]
        o.slides.pop(sec[-1] if sec else mid[-1])
    lang = pack.language or "ru"
    o.title = sanitize(o.title, lang)
    for i, s in enumerate(o.slides):
        s.index = i
        s.title = sanitize(s.title, lang).rstrip(".")
        s.message = sanitize(s.message, lang) if s.message else s.message
        s.points = [p for p in (sanitize(x, lang) for x in s.points) if p]
    return o


def _viz_kind(kind: SlideKind, avail: set[SlideKind]) -> SlideKind:
    for k in ([kind] if kind in (SlideKind.chart, SlideKind.table) else []) + [SlideKind.chart, SlideKind.table, SlideKind.image, SlideKind.text]:
        if k in avail:
            return k
    return resolve_kind(SlideKind.text, avail)


# ----------------------------------------------------------------------------- эвристика (без модели)

def _sentences(text: str) -> list[str]:
    s = re.split(r"(?<=[.!?…])\s+|\n+", unwrap(text))     # жёсткие переносы брифа — не границы предложений
    return [x.strip(" -•*#\t") for x in s if len(x.strip()) > 20]


def _short(text: str, words: int = 9) -> str:
    """Короткая фраза без многоточия: первая смысловая часть предложения или первые слова без хвоста-предлога."""
    from ..composing.fitter import clean_tail
    t = re.sub(r"\s+", " ", text).strip().rstrip(".")
    t = t[:1].upper() + t[1:]                        # «дорожная карта» из списка тем -> «Дорожная карта»
    w = t.split(" ")
    if len(w) <= words:
        return t
    for sep in (" — ", ": ", "; ", ", ", " – "):
        head = t.split(sep)[0]
        if 2 <= len(head.split()) <= words:
            return clean_tail(head)
    return clean_tail(" ".join(w[:words]))


def heuristic_outline(pack: ContentPack, tm: TemplateModel, target: int) -> Outline:
    avail = available_kinds(tm)
    sents = _sentences(pack.brief)
    facts = [f for f in pack.facts if numbers_in(f.text)]
    slides: list[OutlineSlide] = [OutlineSlide(kind=SlideKind.title, title=pack.title,
                                               points=[pack.audience or (sents[0] if sents else "")])]
    topics = list(pack.must_include) or _topics(pack.brief) or [_short(s, 6) for s in sents[1:5]]
    if len(topics) >= 3 and SlideKind.agenda in avail:
        slides.append(OutlineSlide(kind=SlideKind.agenda, title="Содержание", points=topics[:6], n_items=min(6, len(topics))))
    if sents:
        slides.append(OutlineSlide(kind=SlideKind.cards, title=_short(sents[0]), message=sents[0],
                                   points=[_short(s, 12) for s in sents[1:4]], n_items=3))
    for chunk in [facts[i:i + 3] for i in range(0, min(len(facts), 6), 3)]:
        if len(chunk) >= 2:
            slides.append(OutlineSlide(kind=SlideKind.factoids, title=_short(chunk[0].text), message=chunk[0].text,
                                       points=[f.text for f in chunk], fact_refs=[f.id for f in chunk], n_items=len(chunk)))
    for t in pack.tables[:3]:
        slides.append(OutlineSlide(kind=SlideKind.chart, title=_table_title(t), message=t.title,
                                   viz=VizSpec(kind="chart", data_ref=t.id)))
    for topic in topics[:4]:
        rel = sorted(sents, key=lambda s: -_overlap(topic, s))[:4]
        slides.append(OutlineSlide(kind=SlideKind.cards, title=_short(topic, 8), message=rel[0] if rel else topic,
                                   points=[_short(s, 12) for s in rel[:3]], n_items=3))
    cta = [s for s in sents if re.search(r"предлага|решени|просим|следующ|призыв|подключ|цель", s, re.I)]
    slides.append(OutlineSlide(kind=SlideKind.cta if SlideKind.cta in avail else SlideKind.text,
                               title=_short(cta[-1] if cta else "Следующие шаги"), message=cta[-1] if cta else "",
                               points=[_short(s, 12) for s in cta[:3]]))
    o = Outline(title=pack.title, slides=slides, language=pack.language, source="offline")
    return normalize(o, pack, tm, target)


def _topics(text: str) -> list[str]:
    m = re.search(r"(?:ключевые темы|темы|разделы)\s*:\s*([^\n]+)", text, re.I)
    if m:
        return [t.strip(" .") for t in re.split(r",|;", m.group(1)) if t.strip()][:6]
    heads = re.findall(r"^#+\s*(.+)$", text, re.M)
    return heads[:6]


def _overlap(a: str, b: str) -> int:
    sa = {w[:5] for w in re.findall(r"[а-яёa-z0-9]{4,}", a.lower())}
    sb = {w[:5] for w in re.findall(r"[а-яёa-z0-9]{4,}", b.lower())}
    return len(sa & sb)


def _table_title(t: DataTable) -> str:
    try:
        col = t.columns[1]
        vals = [float(str(r[1]).replace(",", ".").replace(" ", "")) for r in t.rows]
        if len(vals) >= 2 and vals[-1] > vals[0] * 1.2:
            return f"{col}: рост с {_fmt(vals[0])} до {_fmt(vals[-1])}"
        top = max(range(len(vals)), key=lambda i: vals[i])
        return f"{col}: лидер — {t.rows[top][0]}"
    except (ValueError, IndexError):
        return t.title.capitalize()


def _fmt(v: float) -> str:
    return str(int(v)) if v.is_integer() else f"{v:.1f}".replace(".", ",")
