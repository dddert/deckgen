"""Сквозной пайплайн: parse -> skeleton -> [variant ×3 параллельно: compose -> build -> render -> audit ->
auto-fix -> rebuild -> VLM-audit -> export]. Аудит — часть пайплайна, а не внешняя проверка.

Каждый этап пишет артефакт в папку прогона (воспроизводимость) и тайминг (бюджет ТЗ — 5 минут на колоду).
"""
from __future__ import annotations

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..audit import AuditContext, FixContext, apply_fixes, run_audit
from ..composing.composer import compose_variant
from ..composing.variants import VARIANTS, apply_variant
from ..images.provider import ImageProvider
from ..llm.client import LLMClient, make_client
from ..models import AuditReport, ContentPack, DeckPlan, Outline, TemplateModel
from ..parsing.template import parse_template, template_id_of
from ..planning.skeleton import build_outline
from ..prompts import PromptRegistry
from ..render import office
from ..render.html import write_html
from ..render.pptx_builder import build_pptx
from ..settings import Settings
from .classify import make_classifier

Log = Callable[[str], None]


@dataclass
class VariantResult:
    variant: str
    label: str
    plan: DeckPlan
    audit: AuditReport
    files: dict[str, str] = field(default_factory=dict)
    pngs: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)


@dataclass
class RunResult:
    run_dir: Path
    template: TemplateModel
    outline: Outline
    variants: dict[str, VariantResult] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    llm_stats: dict[str, dict] = field(default_factory=dict)
    mode: str = "llm"
    notes: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, settings: Settings, log: Log | None = None):
        self.s = settings
        self.log = log or (lambda m: None)
        self.prompts = PromptRegistry(settings)
        self.workdir = Path(settings.run.workdir)
        self.llm = self._client(settings.llm, "llm")
        self.vlm = self._client(settings.vlm, "vlm")         # та же модель на том же vLLM, но свои параметры (t=0)
        self.render_backend = office.backend(settings.export.pdf_backend, settings.export.soffice_bin)

    def _client(self, ep, name) -> LLMClient | None:
        c = make_client(ep, name)
        if c is not None and not c.alive():
            if ep.fallback_to_offline:
                self.log(f"{name}: {ep.base_url} не отвечает — работаем эвристиками (offline)")
                return None
            raise RuntimeError(f"{name}: {ep.base_url} недоступен")
        return c

    # ------------------------------------------------------------------ шаги
    def parse(self, template: Path) -> TemplateModel:
        renderer = None
        if self.s.parsing.render_previews and self.render_backend != "none":
            def renderer(p: Path, out: Path):
                return office.render_pngs(p, out, self.render_backend, self.s.export.soffice_bin, 60)
        classifier, key = None, "heur"
        if self.s.parsing.vlm_classify and self.vlm is not None:
            classifier = make_classifier(self.vlm, self.prompts)
            key = f"vlm-{self.prompts.version('slide_classifier')}"
        return parse_template(template, self.workdir / "cache", use_cache=self.s.parsing.cache,
                              max_shapes=self.s.parsing.max_shapes_per_layout, renderer=renderer,
                              classifier=classifier, classifier_key=key, log=self.log)

    def outline(self, pack: ContentPack, tm: TemplateModel, target: int) -> Outline:
        return build_outline(pack, tm, target, self.llm, self.prompts, self.log)

    # ------------------------------------------------------------------ прогон
    def run(self, template: Path, pack: ContentPack, variants: list[str] | None = None, target: int | None = None,
            on_stage: Callable[[str, float], None] | None = None) -> RunResult:
        stage = on_stage or (lambda name, p: None)
        t0 = time.perf_counter()
        run_dir = self.workdir / "runs" / f"{time.strftime('%Y%m%d_%H%M%S')}_{template_id_of(template)}_{pack.id}"[:120]
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "pack.json").write_text(pack.model_dump_json(indent=2), encoding="utf-8")
        stage("parse", 0.05)
        tm = self.parse(template)
        t_parse = time.perf_counter()
        (run_dir / "template.json").write_text(json.dumps({"file": str(template), "template_id": tm.design.template_id},
                                                          ensure_ascii=False), encoding="utf-8")
        stage("outline", 0.15)
        target = target or pack.target_slides or self.s.run.target_slides
        outline = self.outline(pack, tm, target)
        (run_dir / "outline.json").write_text(outline.model_dump_json(indent=2), encoding="utf-8")
        (run_dir / "pack.json").write_text(pack.model_dump_json(indent=2), encoding="utf-8")   # + таблицы из текста
        t_outline = time.perf_counter()
        res = RunResult(run_dir=run_dir, template=tm, outline=outline, mode=outline.source,
                        timings={"parse": t_parse - t0, "outline": t_outline - t_parse})
        names = [v for v in (variants or self.s.composing.variants) if v in VARIANTS]
        stage("variants", 0.25)
        with ThreadPoolExecutor(max_workers=len(names)) as ex:
            futs = {v: ex.submit(self._variant, v, outline, tm, pack, template, run_dir) for v in names}
            for v, fut in futs.items():
                res.variants[v] = fut.result()
                res.timings[v] = res.variants[v].timings.get("total", 0.0)
        res.timings["total"] = time.perf_counter() - t0
        res.llm_stats = {n: dict(c.stats) for n, c in (("llm", self.llm), ("vlm", self.vlm)) if c is not None}
        if res.timings["total"] > self.s.run.max_generation_seconds:
            res.notes.append(f"Превышен бюджет {self.s.run.max_generation_seconds} с: {res.timings['total']:.0f} с")
        if self.render_backend == "none":
            res.notes.append("LibreOffice не найден: PDF, PNG и VLM-аудит пропущены (установите soffice)")
        (run_dir / "run.json").write_text(json.dumps({
            "timings": res.timings, "llm_stats": res.llm_stats, "mode": res.mode, "notes": res.notes,
            "variants": {v: {"files": r.files, "pngs": r.pngs, "timings": r.timings, "label": r.label} for v, r in res.variants.items()},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        stage("done", 1.0)
        return res

    def _variant(self, v: str, outline: Outline, tm: TemplateModel, pack: ContentPack, template: Path, run_dir: Path) -> VariantResult:
        t0 = time.perf_counter()
        vdir = run_dir / v
        vdir.mkdir(parents=True, exist_ok=True)
        ov = apply_variant(outline, v, tm, pack)
        (vdir / "outline.json").write_text(ov.model_dump_json(indent=2), encoding="utf-8")
        images = ImageProvider(pack, self.s.text_to_image, self.workdir / "cache" / "images", tm.design, self.llm,
                               self.prompts, self.s.run.seed, self.log)
        plan = compose_variant(ov, tm, pack, v, self.s, self.llm, self.prompts, images, self.log)
        t_compose = time.perf_counter()
        report, files, pngs = self._build_and_audit(plan, tm, pack, vdir, auto_fix=True)
        vr = VariantResult(variant=v, label=VARIANTS[v].label, plan=plan, audit=report, files=files, pngs=pngs)
        vr.timings = {"compose": t_compose - t0, "build_audit": time.perf_counter() - t_compose,
                      "total": time.perf_counter() - t0, **{f"compose_{k}": x for k, x in plan.timings.items()}}
        return vr

    def _render(self, pptx: Path, vdir: Path) -> tuple[Path | None, list[Path]]:
        if self.render_backend == "none":
            return None, []
        try:
            png_dir = vdir / "png"
            shutil.rmtree(png_dir, ignore_errors=True)
            pdf = office.to_pdf(pptx, png_dir, self.render_backend, self.s.export.soffice_bin)
            return pdf, office.pdf_to_pngs(pdf, png_dir, self.s.parsing.render_dpi, prefix="slide")
        except Exception as e:  # noqa: BLE001
            self.log(f"render: {e}")
            return None, []

    def _build_and_audit(self, plan: DeckPlan, tm: TemplateModel, pack: ContentPack, vdir: Path, auto_fix: bool,
                         selected: set[str] | None = None) -> tuple[AuditReport, dict[str, str], list[str]]:
        pptx = build_pptx(plan, tm, vdir / f"deck_{plan.variant}.pptx", self.s.export.prune_unused_layouts)
        pdf, pngs = self._render(pptx, vdir)
        ctx = AuditContext.load(pptx, plan, tm, self.s.audit, pack, pngs)
        report = run_audit(ctx)
        fctx = FixContext(tm, pack, self.llm, self.prompts, self.s.audit.max_bullets, self.s.audit.max_words_per_bullet)
        fixed: list[str] = []
        todo = selected if selected is not None else (set(self.s.audit.auto_fix) if auto_fix else set())
        for _ in range(max(1, self.s.audit.auto_fix_rounds) if todo else 0):
            done = apply_fixes(plan, report.findings, todo, fctx)
            if not done:
                break
            fixed += done
            pptx = build_pptx(plan, tm, pptx, self.s.export.prune_unused_layouts)
            ctx = AuditContext.load(pptx, plan, tm, self.s.audit, pack, [])
            report = run_audit(ctx)                         # промежуточные раунды — без рендера (быстро)
        if fixed:
            pdf, pngs = self._render(pptx, vdir)
            ctx = AuditContext.load(pptx, plan, tm, self.s.audit, pack, pngs)
            report = run_audit(ctx)
        if self.s.audit.semantic_checks:
            sem = run_audit(ctx, vlm=self.vlm, prompts=self.prompts, semantic=True, only={"__none__"})
            report.findings += sem.findings
            report.checks_run += [c for c in sem.checks_run if c not in report.checks_run]
            report.semantic_status = sem.semantic_status
        report.fixed = fixed
        files = {"pptx": str(pptx)}
        if "pdf" in self.s.export.formats and pdf is not None:
            files["pdf"] = str(shutil.copy(pdf, vdir / f"deck_{plan.variant}.pdf"))
        if "html" in self.s.export.formats:
            files["html"] = str(write_html(pptx, plan, tm.design, vdir / f"deck_{plan.variant}.html", self.s.export.html_image_max_px))
        (vdir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        (vdir / "audit.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return report, files, [str(p) for p in pngs]

    # ------------------------------------------------------------------ исправления из UI
    def fix(self, run_dir: Path, variant: str, selected: list[str]) -> VariantResult:
        meta = json.loads((run_dir / "template.json").read_text(encoding="utf-8"))
        tm = self.parse(Path(meta["file"]))
        pack = ContentPack.model_validate_json((run_dir / "pack.json").read_text(encoding="utf-8"))
        vdir = run_dir / variant
        plan = DeckPlan.model_validate_json((vdir / "plan.json").read_text(encoding="utf-8"))
        old = AuditReport.model_validate_json((vdir / "audit.json").read_text(encoding="utf-8"))
        fctx = FixContext(tm, pack, self.llm, self.prompts, self.s.audit.max_bullets, self.s.audit.max_words_per_bullet)
        done = apply_fixes(plan, old.findings, set(selected), fctx)
        report, files, pngs = self._build_and_audit(plan, tm, pack, vdir, auto_fix=False, selected=set())
        report.fixed = done
        (vdir / "audit.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return VariantResult(variant=variant, label=VARIANTS[variant].label, plan=plan, audit=report, files=files, pngs=pngs)
