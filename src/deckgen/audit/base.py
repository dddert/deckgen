"""Инфраструктура аудита: реестр проверок (декоратор @check), контекст проверки готового файла.

Аудит читает ГОТОВЫЙ .pptx тем же ридером, что и парсер шаблона, и сравнивает каждый слайд
с его слайдом-образцом: так детерминированные проверки ловят то, что внёс генератор, а замысел
дизайнера шаблона (декор «в обрез», наложение подписи на карточку) не считается ошибкой.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..models import Box, CheckCategory, ContentPack, DeckPlan, Finding, Severity, TemplateModel, TemplateSlide
from ..ooxml.reader import PresentationReader, RShape, RSlide
from ..parsing.fonts import TextMeasurer
from ..settings import AuditSettings


@dataclass
class SlideView:
    index: int
    rs: RSlide
    ts: TemplateSlide                 # слайд-образец
    decor: list[RShape]               # графика макета/мастера под слайдом
    kind: str
    png: Path | None = None

    @property
    def shapes(self) -> list[RShape]:
        return [s for s in self.rs.shapes if s.box.visible() or s.kind == "grp"]

    def is_new(self, s: RShape) -> bool:
        return s.id not in self.ts.shape_boxes

    def is_changed(self, s: RShape, eps: float = 0.003) -> bool:
        t = self.ts.shape_boxes.get(s.id)
        if t is None:
            return True
        return any(abs(getattr(s.box, k) - getattr(t, k)) > eps for k in ("x", "y", "w", "h"))

    def text_shapes(self) -> list[RShape]:
        return [s for s in self.shapes if s.kind == "sp" and s.text.strip()]


@dataclass
class AuditContext:
    pptx: Path
    plan: DeckPlan
    tm: TemplateModel
    cfg: AuditSettings
    pack: ContentPack | None = None
    pngs: list[Path] = field(default_factory=list)
    reader: PresentationReader | None = None
    slides: list[SlideView] = field(default_factory=list)
    measurer: TextMeasurer | None = None
    open_error: str | None = None

    @classmethod
    def load(cls, pptx: Path, plan: DeckPlan, tm: TemplateModel, cfg: AuditSettings, pack: ContentPack | None = None,
             pngs: list[Path] | None = None) -> "AuditContext":
        ctx = cls(pptx=pptx, plan=plan, tm=tm, cfg=cfg, pack=pack, pngs=list(pngs or []))
        try:
            ctx.reader = PresentationReader(pptx)
        except Exception as e:  # noqa: BLE001
            ctx.open_error = str(e)
            return ctx
        ctx.measurer = TextMeasurer(tm.design.font_files)
        for i, rs in enumerate(ctx.reader.slides()):
            ps = plan.slides[i] if i < len(plan.slides) else None
            ts = tm.slides[ps.template_slide] if ps is not None else tm.slides[0]
            png = ctx.pngs[i] if i < len(ctx.pngs) else None
            ctx.slides.append(SlideView(i, rs, ts, ctx.reader.read_decor(i), ps.kind.value if ps else "other", png))
        return ctx


Check = Callable[[AuditContext], list[Finding]]


@dataclass(frozen=True)
class CheckMeta:
    id: str
    category: CheckCategory
    description: str
    deterministic: bool
    fn: Check
    fix: str | None = None


REGISTRY: dict[str, CheckMeta] = {}


def check(id: str, category: CheckCategory, description: str, deterministic: bool = True, fix: str | None = None):
    def deco(fn: Check) -> Check:
        REGISTRY[id] = CheckMeta(id, category, description, deterministic, fn, fix)
        return fn
    return deco


def finding(check_id: str, slide: int, message: str, *, shape: RShape | None = None, box: Box | None = None,
            severity: Severity = Severity.warning, fix: str | None = None, deterministic: bool = True, **evidence) -> Finding:
    meta = REGISTRY.get(check_id)
    cat = meta.category if meta else CheckCategory(check_id.split(".")[0])
    sid = shape.id if shape is not None else None
    return Finding(id=f"{slide}:{check_id}:{sid or ''}", check_id=check_id, category=cat, deterministic=deterministic,
                   severity=severity, slide_index=slide, shape_id=sid, message=message,
                   box=box if box is not None else (shape.box if shape is not None else None),
                   fix_id=(fix or None) if fix is not None else (meta.fix if meta else None), evidence=evidence)


SERVICE_KINDS = {"title", "section", "thanks", "qa", "cta", "agenda"}
