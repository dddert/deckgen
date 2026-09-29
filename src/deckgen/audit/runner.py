"""Прогон аудита готового файла: все детерминированные проверки из реестра + (опционально) VLM."""
from __future__ import annotations

import time

from ..llm.client import LLMClient
from ..models import AuditReport
from ..prompts import PromptRegistry
from . import checks_density, checks_integrity, checks_layout, checks_template  # noqa: F401 — регистрация @check
from .base import REGISTRY, AuditContext
from .semantic import catalog as semantic_catalog
from .semantic import run_semantic


def run_audit(ctx: AuditContext, *, vlm: LLMClient | None = None, prompts: PromptRegistry | None = None,
              semantic: bool = False, only: set[str] | None = None) -> AuditReport:
    t0 = time.perf_counter()
    findings, run = [], []
    for cid, meta in REGISTRY.items():
        if only and cid not in only:
            continue
        if ctx.open_error and cid != "integrity.file_opens":
            continue
        try:
            findings += meta.fn(ctx)
        except Exception as e:  # noqa: BLE001 — сломанная проверка не должна ронять аудит
            from ..models import CheckCategory, Finding, Severity
            findings.append(Finding(id=f"0:{cid}:error", check_id=cid, category=CheckCategory.integrity, deterministic=True,
                                    severity=Severity.info, slide_index=0, message=f"Проверка упала: {e}"))
        run.append(cid)
    status = "выключены"
    if semantic and vlm is not None and prompts is not None:
        sem, status = run_semantic(ctx, vlm, prompts)
        findings += sem
        run.append("content.* (VLM)")
    elif semantic:
        status = "модель недоступна — семантические проверки пропущены"
    seen, uniq = set(), []
    for f in findings:                               # один и тот же дефект одной фигуры — одно замечание
        if f.id in seen:
            continue
        seen.add(f.id)
        uniq.append(f)
    return AuditReport(deck_id=ctx.plan.deck_id, variant=ctx.plan.variant, findings=uniq, checks_run=run,
                       prompt_versions=prompts.versions() if prompts else {}, duration_seconds=time.perf_counter() - t0,
                       semantic_status=status)


def catalog() -> list[dict]:
    rows = [{"id": m.id, "category": m.category.value, "deterministic": m.deterministic, "description": m.description,
             "fix": m.fix} for m in REGISTRY.values()]
    return rows + semantic_catalog()
