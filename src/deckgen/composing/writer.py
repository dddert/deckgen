"""Тексты слайда под слоты выбранного слайда-образца.

Схема ответа строится под конкретный образец: ключи — id фигур, списки — для списков и составных слотов
(ровно столько частей, сколько в образце). vLLM с guided decoding не может пропустить ключ или вернуть
лишний. После модели — детерминированная чистка: маркеры списка, id фактов, выдуманные цифры.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field, create_model

from ..content.grounding import numbers_in, source_numbers, strip_ungrounded
from ..llm.client import ChatMessage, LLMClient, LLMError
from ..models import ContentPack, OutlineSlide, SlotRole, TemplateSlide, TextSlot
from ..prompts import PromptRegistry
from .variants import Variant

_ROLE_RU = {
    SlotRole.title: "заголовок слайда (вывод)", SlotRole.subtitle: "подзаголовок", SlotRole.heading: "заголовок элемента",
    SlotRole.body: "текст", SlotRole.bullets: "список (пункты)", SlotRole.number: "цифра-показатель с единицей",
    SlotRole.label: "короткая метка (дата/этап/тег)", SlotRole.name: "имя человека (только если есть в материалах)",
    SlotRole.caption: "подпись мелким шрифтом",
}
_WRITE = {SlotRole.title, SlotRole.subtitle, SlotRole.heading, SlotRole.body, SlotRole.bullets, SlotRole.number,
          SlotRole.label, SlotRole.name, SlotRole.caption}
_GLYPH = re.compile(r"^[\s•·▪◦●○\-–—*]+")
_REF = re.compile(r"\s*\[(?:f\d+|[a-z_]+\d*)\]", re.I)
_DEMO = re.compile(r"^(заголовок|подзаголовок|текст|основной текст|описание|пункт|lorem ipsum.*|имя фамилия|имя спикера.*|должность|"
                   r"название раздела|текст описания|дата|xx+%?|х+%?)$", re.I)


@dataclass
class SlotSpec:
    key: str
    slot: TextSlot
    label: str
    limit: str
    chars: int
    item: int | None
    demo: str
    n_bullets: int = 0
    hint: str = ""


def writable(t: TemplateSlide, keep_items: int) -> list[TextSlot]:
    out = []
    for s in t.texts:
        if s.role not in _WRITE:
            continue
        if s.item is not None and s.item >= keep_items:
            continue
        out.append(s)
    return out


def specs(t: TemplateSlide, keep_items: int, v: Variant) -> list[SlotSpec]:
    res = []
    for s in writable(t, keep_items):
        ratio = 1.0 if s.role in (SlotRole.title, SlotRole.number, SlotRole.label, SlotRole.name) else v.text_ratio
        chars = max(4, int(s.max_chars * ratio))
        if s.role == SlotRole.title:
            chars = min(int(chars * 1.15), 80)    # заголовок может уменьшиться на шаг шкалы
        demo = s.demo_text.replace("\n", " / ")[:40]
        hint = _hint(s)
        if s.parts and len(s.parts) > 1:
            parts = ", ".join(f"{_ROLE_RU.get(p.role, 'текст')} ≤{max(3, int(p.max_chars * (1 if p.role == SlotRole.number else v.text_ratio)))} зн."
                              for p in s.parts)
            res.append(SlotSpec(f"s{s.shape_id}", s, "составной", f"список из {len(s.parts)} частей: [{parts}]", chars, s.item, demo,
                                hint=hint))
        elif s.role == SlotRole.bullets:
            n = max(1, min(v.bullets_max, s.max_lines))
            per = max(20, min(110, chars // n))
            res.append(SlotSpec(f"s{s.shape_id}", s, _ROLE_RU[s.role], f"до {n} пунктов, каждый ≤{per} зн.", chars, s.item, demo, n,
                                hint=hint))
        else:
            res.append(SlotSpec(f"s{s.shape_id}", s, _ROLE_RU.get(s.role, "текст"), f"≤{chars} зн.", chars, s.item, demo,
                                hint=hint))
    return res


def _hint(s: TextSlot) -> str:
    """Что видит дизайнер и не видит модель: сколько строк помещается, лежит ли рядом графика шаблона."""
    out = []
    if s.role in (SlotRole.title, SlotRole.subtitle, SlotRole.heading) and s.size_pt >= 28:
        out.append(f"крупный кегль {s.size_pt:.0f} pt, строк не больше {max(1, min(2, s.max_lines))}")
    elif s.max_lines and s.role != SlotRole.bullets:
        out.append(f"строк помещается: {s.max_lines}")
    if s.limited:
        out.append("рядом графика шаблона — не длиннее образца" if s.demo_text.strip() else "рядом графика шаблона — лимит строгий")
    return ", ".join(out)


def schema_for(sp: list[SlotSpec]) -> type[BaseModel]:
    fields = {}
    for s in sp:
        if s.slot.parts and len(s.slot.parts) > 1:
            n = len(s.slot.parts)
            fields[s.key] = (list[str], Field(min_length=n, max_length=n))
        elif s.slot.role == SlotRole.bullets:
            fields[s.key] = (list[str], Field(min_length=1, max_length=max(1, s.n_bullets)))
        else:
            fields[s.key] = (str, ...)
    return create_model("SlideTexts", **fields)


def write_slide(o: OutlineSlide, t: TemplateSlide, keep_items: int, pack: ContentPack, v: Variant, deck: dict,
                llm: LLMClient | None, prompts: PromptRegistry | None) -> tuple[dict[str, list[str]], str]:
    """-> ({shape_id: [абзацы или части]}, 'llm'|'offline')"""
    sp = specs(t, keep_items, v)
    if not sp:
        return {}, "offline"
    raw: dict[str, list[str]] | None = None
    mode = "offline"
    if llm is not None and prompts is not None:
        try:
            facts = _facts_for(o, pack)
            prompt = prompts.render(
                "slide_writer", deck_title=deck["title"], purpose=pack.purpose.value, audience=pack.audience,
                language=pack.language, tone=pack.tone, position=o.index + 1, total=deck["total"],
                prev_title=deck["titles"][o.index - 1] if o.index > 0 else "—",
                next_title=deck["titles"][o.index + 1] if o.index + 1 < len(deck["titles"]) else "—",
                kind=o.kind.value, title=o.title, message=o.message, points=o.points, facts=facts,
                context=_context(o, pack), style_note=v.style_note, slots=[s.__dict__ for s in sp], n_items=keep_items,
            )
            ans = llm.complete_json([ChatMessage("user", prompt)], schema_for(sp), max_tokens=1600, temperature=0.5)
            raw = {s.slot.shape_id: _as_list(getattr(ans, s.key)) for s in sp}
            mode = "llm"
        except LLMError:
            raw = None
    if raw is None:
        raw = offline_texts(o, sp, pack)
    return clean(raw, sp, pack, o), mode


def _as_list(v) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def _facts_for(o: OutlineSlide, pack: ContentPack) -> list:
    ref = [f for f in pack.facts if f.id in o.fact_refs]
    rest = [f for f in pack.facts if f.id not in o.fact_refs]
    words = {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", (o.title + " " + o.message + " " + " ".join(o.points)).lower())}
    rest.sort(key=lambda f: -len(words & {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", f.text.lower())}))
    return (ref + rest)[:10]


def _context(o: OutlineSlide, pack: ContentPack, limit: int = 900) -> str:
    words = {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", (o.title + " " + o.message + " " + " ".join(o.points)).lower())}
    sents = re.split(r"(?<=[.!?])\s+|\n+", pack.brief)
    best = sorted(sents, key=lambda s: -len(words & {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", s.lower())}))
    out, n = [], 0
    for s in best[:8]:
        if n + len(s) > limit:
            break
        out.append(s.strip())
        n += len(s)
    return " ".join(out)


def clean(raw: dict[str, list[str]], sp: list[SlotSpec], pack: ContentPack, o: OutlineSlide) -> dict[str, list[str]]:
    sources = source_numbers(pack)
    out: dict[str, list[str]] = {}
    seen: set[str] = set()
    for s in sp:
        vals = raw.get(s.slot.shape_id, [])
        res = []
        for i, val in enumerate(vals):
            txt = _REF.sub("", _GLYPH.sub("", str(val))).strip().strip("«»\"").strip()
            txt = re.sub(r"\*\*|__|`", "", txt)
            if _DEMO.match(txt):
                txt = ""
            part_role = s.slot.parts[i].role if s.slot.parts and i < len(s.slot.parts) else s.slot.role
            if part_role == SlotRole.number:
                txt = _grounded_number(txt, sources)
            elif txt and s.slot.role != SlotRole.title:
                txt = strip_ungrounded(txt, sources)
            if s.slot.role == SlotRole.bullets:
                txt = txt.rstrip(";.")
            res.append(txt)
        if s.slot.role == SlotRole.title:
            t = res[0] if res and res[0] else o.title
            if [n for n in numbers_in(t) if n not in sources and float(n) > 12]:
                t = o.title
            res = [t.rstrip(".")]
        key = " ".join(res).lower()
        if key and key in seen and s.item is not None:
            res = [""] * len(res)                # дубль в соседней карточке
        seen.add(key)
        out[s.slot.shape_id] = [r for r in res if r] if s.slot.role == SlotRole.bullets else res
    return out


def _grounded_number(text: str, sources: set[str]) -> str:
    nums = numbers_in(text)
    if not nums or nums[0] not in sources:
        return ""
    return text.replace(" ", " ") if len(text) <= 12 else text


# ----------------------------------------------------------------------------- без модели

def offline_texts(o: OutlineSlide, sp: list[SlotSpec], pack: ContentPack) -> dict[str, list[str]]:
    facts = {f.id: f for f in pack.facts}
    ofacts = [facts[r] for r in o.fact_refs if r in facts]
    points = o.points or ([o.message] if o.message else [])
    out: dict[str, list[str]] = {}
    loose = iter(ofacts or [f for f in pack.facts if numbers_in(f.text)])   # цифры вне карточек — по порядку
    for s in sp:
        r, i = s.slot.role, s.item
        pt = points[i] if i is not None and i < len(points) else (points[0] if points and i is None else "")
        fact = ofacts[i] if i is not None and i < len(ofacts) else None
        if i is None and (r == SlotRole.number or (s.slot.parts and any(p.role == SlotRole.number for p in s.slot.parts))):
            fact = next(loose, None)
        if s.slot.parts and len(s.slot.parts) > 1:
            num = _fact_number(fact) if fact else ""
            parts = []
            for p in s.slot.parts:
                parts.append(num if p.role == SlotRole.number else _cut(_heading(pt) if p.role == SlotRole.heading else (fact.text if fact else pt), p.max_chars))
            out[s.slot.shape_id] = parts
        elif r == SlotRole.title:
            out[s.slot.shape_id] = [_cut(o.title, s.chars)]
        elif r == SlotRole.subtitle:
            out[s.slot.shape_id] = [_cut(o.message or (points[0] if points else ""), s.chars)]
        elif r == SlotRole.heading:
            out[s.slot.shape_id] = [_cut(_heading(pt), s.chars)]
        elif r == SlotRole.number:
            out[s.slot.shape_id] = [_fact_number(fact) if fact else ""]
        elif r == SlotRole.bullets:
            items = points if i is None else [pt]
            out[s.slot.shape_id] = [_cut(p, max(20, s.chars // max(1, s.n_bullets))) for p in items[: max(1, s.n_bullets)]]
        elif r in (SlotRole.body, SlotRole.caption):
            src = pt if i is not None else (o.message or " ".join(points))
            if fact and i is not None:
                src = fact.text
            out[s.slot.shape_id] = [_cut(src, s.chars)]
        elif r == SlotRole.label:
            out[s.slot.shape_id] = [f"Этап {i + 1}" if i is not None and o.kind.value == "process" else ""]
        else:
            out[s.slot.shape_id] = [""]
    return out


def _heading(text: str) -> str:
    words = re.sub(r"[^\w\s%-]", " ", text).split()
    return " ".join(words[:3]).capitalize() if words else ""


def _fact_number(fact) -> str:
    if fact is None:
        return ""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(%|ч\b|час\w*|мин\w*|сек\w*|млн|млрд|тыс\.?|₽|раз\w*)?", fact.text)
    if not m:
        return ""
    unit = m.group(2) or ""
    unit = {"часа": "ч", "часов": "ч", "час": "ч", "минут": "мин", "минуты": "мин", "секунд": "с"}.get(unit, unit)
    return f"{m.group(1)} {unit}".strip() if unit and unit != "%" else m.group(1) + unit


def _cut(text: str, limit: int) -> str:
    from .fitter import truncate_words
    return truncate_words(re.sub(r"\s+", " ", text or "").strip(), max(4, limit))


# ----------------------------------------------------------------------------- сокращение

def shorten(o: OutlineSlide, items: list[tuple[str, str, int]], llm: LLMClient | None,
            prompts: PromptRegistry | None) -> dict[str, str]:
    """items: [(shape_id, текст, лимит)] -> {shape_id: короче}"""
    if not items:
        return {}
    if llm is not None and prompts is not None:
        try:
            keys = [(f"s{sid}", sid, text, lim) for sid, text, lim in items]
            schema = create_model("Shorter", **{k: (str, ...) for k, *_ in keys})
            prompt = prompts.render("shorten", title=o.title,
                                    items=[{"key": k, "limit": lim, "length": len(text), "text": text} for k, _, text, lim in keys])
            ans = llm.complete_json([ChatMessage("user", prompt)], schema, max_tokens=800, temperature=0.2)
            return {sid: str(getattr(ans, k)).strip() for k, sid, _, _ in keys}
        except LLMError:
            pass
    return {sid: _cut(text, lim) for sid, text, lim in items}
