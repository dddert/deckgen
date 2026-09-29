"""VLM-уточнение типа слайда-образца: эвристика подаётся как гипотеза, модель смотрит на рендер и
подтверждает или исправляет тип, описание и пригодность. Один вызов на образец, параллельно, результат
кэшируется вместе с разбором шаблона."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from ..llm.client import ChatMessage, LLMClient, LLMError
from ..models import SlideKind, TemplateSlide
from ..prompts import PromptRegistry


class _Cls(BaseModel):
    kind: SlideKind
    name: str
    usable: bool = True
    confidence: float = 0.7


def make_classifier(vlm: LLMClient, prompts: PromptRegistry):
    def classify(slides: list[TemplateSlide], pngs: list[Path]) -> list[TemplateSlide]:
        def one(pair):
            t, png = pair
            if not t.usable and "дубликат" in t.reason:
                return t
            slots = ", ".join(f"{s.role.value}{'' if s.item is None else f'#{s.item + 1}'}" for s in t.texts[:24])
            prompt = prompts.render("slide_classifier", guess_kind=t.kind.value, n_items=t.n_items, slots=slots)
            try:
                ans = vlm.complete_json([ChatMessage("user", prompt, images=[png.read_bytes()])], _Cls,
                                        temperature=0.0, max_tokens=200)
            except LLMError:
                return t
            if ans.confidence >= 0.6:
                if ans.kind != t.kind:
                    t.reason = f"VLM: {t.kind.value} → {ans.kind.value}"
                t.kind, t.name, t.confidence = ans.kind, ans.name or t.name, max(t.confidence, ans.confidence)
                if not ans.usable and t.usable:
                    t.usable, t.reason = False, "VLM: служебный слайд"
            return t
        with ThreadPoolExecutor(max_workers=max(1, vlm.ep.max_concurrency)) as ex:
            return list(ex.map(one, zip(slides, pngs)))
    return classify
