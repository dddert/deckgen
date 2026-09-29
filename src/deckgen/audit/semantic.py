"""Недетерминированные проверки: 11 вопросов Приложения 1 (+3 о дизайне в audit_semantic v2) к VLM по картинке слайда.

Рядом с картинкой модель получает текст слайда (для сверки цифр и опечаток), факты-источники и заголовки
соседних слайдов. Ответ может меняться между запусками — findings помечены deterministic=False, ответ
модели целиком лежит в evidence.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field, create_model

from ..llm.client import ChatMessage, LLMClient, LLMError
from ..models import CheckCategory, Finding, Severity
from ..prompts import PromptRegistry
from .base import SERVICE_KINDS, AuditContext

QUESTIONS = {
    1: ("content.title_is_conclusion", "Заголовок содержит вывод, а не просто называет тему"),
    2: ("content.body_matches_title", "Содержимое слайда соответствует заголовку"),
    3: ("content.one_sentence", "Слайд пересказывается одним предложением"),
    4: ("content.facts_in_sources", "Все цифры и факты со слайда есть в исходных материалах"),
    5: ("content.has_body", "На слайде есть содержание, а не только заголовок"),
    6: ("content.images_relevant", "Картинки и иконки относятся к теме слайда"),
    7: ("content.no_service_junk", "Нет служебного мусора: реплик спикера, кусков промпта"),
    8: ("content.no_typos", "Текст без опечаток"),
    9: ("content.single_language", "Вся колода на одном языке"),
    10: ("content.table_legend_relevant", "Все строки таблицы и элементы легенды работают на мысль слайда"),
    11: ("content.neighbors_linked", "Соседние слайды связаны между собой по логике"),
    12: ("design.text_over_graphics", "Текст не заходит на картинки, объекты и фигуры шаблона"),
    13: ("design.readable_on_background", "Весь текст хорошо читается на своём фоне"),
    14: ("design.balanced", "Слайд сбалансирован: нет обрезанного текста, гигантских заголовков, перегруженных зон"),
}
_SKIP_SERVICE = {1, 3, 4, 5, 10}
_FIX = {1: "rewrite", 2: "rewrite", 3: "rewrite", 4: "strip_numbers", 7: "rewrite", 8: "rewrite", 9: "rewrite", 11: "rewrite",
        12: "rewrite", 14: "rewrite"}      # 13 (читаемость) исправляет детерминированный template.contrast по APCA
_CATEGORY = {12: CheckCategory.layout, 13: CheckCategory.template, 14: CheckCategory.layout}


class _A(BaseModel):
    q: int
    ok: bool
    note: str = ""


def _answers(n: int) -> type[BaseModel]:
    """Схема ответа под версию промпта: v1 — 11 вопросов Приложения 1, v2 — ещё 3 вопроса о дизайне."""
    return create_model("Answers", answers=(list[_A], Field(min_length=n, max_length=n)))


def run_semantic(ctx: AuditContext, vlm: LLMClient, prompts: PromptRegistry) -> tuple[list[Finding], str]:
    """-> (findings, статус). Без картинок/модели — пусто и причина."""
    if not ctx.pngs:
        return [], "нет рендера слайдов (PNG) — семантические проверки пропущены"
    facts = []
    if ctx.pack is not None:
        facts = [f.text for f in ctx.pack.facts][:30]
        facts += [f"{t.title}: " + "; ".join(", ".join(f"{c}={v}" for c, v in zip(t.columns, r)) for r in t.rows[:10])
                  for t in ctx.pack.tables[:6]]
    titles = [ps.title for ps in ctx.plan.slides]
    lang = ctx.pack.language if ctx.pack else "ru"

    n_q = max(int(q) for q in re.findall(r"^(\d+)\. ", prompts.render("audit_semantic", slide_text="", kind="", language="",
                                                                    prev_title="", next_title="", facts=[]), re.M))
    schema = _answers(n_q)

    def one(sv) -> list[Finding]:
        if sv.png is None:
            return []
        text = " | ".join(s.text.replace("\n", " ") for s in sv.text_shapes())[:1500]
        prompt = prompts.render("audit_semantic", slide_text=text or "(текста нет)", kind=sv.kind, language=lang,
                                prev_title=titles[sv.index - 1] if sv.index > 0 else "—",
                                next_title=titles[sv.index + 1] if sv.index + 1 < len(titles) else "—", facts=facts)
        try:
            ans = vlm.complete_json([ChatMessage("user", prompt, images=[sv.png.read_bytes()])], schema,
                                    temperature=0.0, max_tokens=900)
        except LLMError:
            return []
        out = []
        for a in ans.answers:
            if a.ok or a.q not in QUESTIONS or (sv.kind in SERVICE_KINDS and a.q in _SKIP_SERVICE):
                continue
            cid, question = QUESTIONS[a.q]
            out.append(Finding(id=f"{sv.index}:{cid}:", check_id=cid, category=_CATEGORY.get(a.q, CheckCategory.content),
                               deterministic=False,
                               severity=Severity.error if a.q in (4, 7) else Severity.warning, slide_index=sv.index,
                               message=f"{question}? — нет. {a.note}".strip(), fix_id=_FIX.get(a.q),
                               evidence={"question": a.q, "answer": a.model_dump()}))
        return out

    workers = max(1, vlm.ep.max_concurrency)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = [f for chunk in ex.map(one, ctx.slides) for f in chunk]
    return res, "ok"


def catalog() -> list[dict]:
    return [{"id": cid, "category": _CATEGORY.get(q, CheckCategory.content).value, "deterministic": False,
             "description": f"Вопрос {q}: {text}",
             "fix": _FIX.get(q)} for q, (cid, text) in QUESTIONS.items()]
