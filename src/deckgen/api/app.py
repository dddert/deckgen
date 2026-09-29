"""FastAPI: шаблоны (список/загрузка/разбор/превью), генерация по тексту пользователя + файлам, прогоны,
аудит с выбором исправлений, раздача файлов. Собранный web/dist раздаётся с того же порта.

Все пути к файлам — только внутри рабочих папок сервиса (в v1 API принимал произвольные пути сервера).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..settings import Settings
from .jobs import STORE_DEFAULT_PARALLEL, JobStore

settings = Settings.load()
app = FastAPI(title="deckgen v2", version=__version__)
app.add_middleware(CORSMiddleware, allow_origins=settings.api.cors_origins or ["*"], allow_methods=["*"], allow_headers=["*"])
JOBS = JobStore(STORE_DEFAULT_PARALLEL)
WORK = Path(settings.run.workdir)
UPLOADS = WORK / "uploads"
RUNS = WORK / "runs"
TEMPLATES = Path(settings.run.templates_dir)
EXAMPLES = Path("data/content_packs")
_ALLOWED = {".csv", ".xlsx", ".xlsm", ".md", ".txt", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def _pipeline(job=None):
    from ..pipeline.orchestrator import Pipeline
    return Pipeline(Settings.load(), log=(job.say if job else None))


def _safe(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if not p.is_relative_to(root.resolve()) or not p.exists():
        raise HTTPException(404, "нет такого файла")
    return p


def _templates() -> list[dict]:
    out = []
    for folder, origin in ((TEMPLATES, "dataset"), (UPLOADS, "upload")):
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.pptx")):
            out.append({"id": hashlib.md5(p.read_bytes()).hexdigest()[:10], "name": p.stem, "origin": origin,
                        "path": str(p), "size_mb": round(p.stat().st_size / 1e6, 1)})
    return out


def _template_path(tid: str) -> Path:
    t = next((t for t in _templates() if t["id"] == tid), None)
    if t is None:
        raise HTTPException(404, "шаблон не найден")
    return Path(t["path"])


# ---------------------------------------------------------------------------- служебное

@app.get("/api/health")
def health():
    p = _pipeline()
    return {"ok": True, "version": __version__, "llm": p.llm is not None, "vlm": p.vlm is not None,
            "llm_model": settings.llm.model, "render": p.render_backend, "t2i": settings.text_to_image.enabled}


@app.get("/api/audit/catalog")
def audit_catalog():
    from ..audit import catalog
    return catalog()


@app.get("/api/prompts")
def prompts():
    from ..prompts import PromptRegistry
    return PromptRegistry(settings).catalog()


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = JOBS.get(job_id)
    if j is None:
        raise HTTPException(404, "задача не найдена")
    return j.view()


# ---------------------------------------------------------------------------- шаблоны

@app.get("/api/templates")
def templates():
    return _templates()


@app.post("/api/templates")
async def upload_template(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pptx"):
        raise HTTPException(400, "нужен .pptx")
    data = await file.read()
    if len(data) > settings.api.max_upload_mb * 1e6:
        raise HTTPException(413, "файл слишком большой")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w\-. ]+", "_", Path(file.filename).stem)[:60] or "template"
    dst = UPLOADS / f"{name}.pptx"
    dst.write_bytes(data)
    tid = hashlib.md5(data).hexdigest()[:10]
    return {"id": tid, "name": name}


@app.post("/api/templates/{tid}/parse")
def parse_template(tid: str):
    path = _template_path(tid)

    def work(job):
        job.stage = "parse"
        from ..parsing.template import summary
        tm = _pipeline(job).parse(path)
        s = summary(tm)
        s["slides"] = [{"index": t.index, "kind": t.kind.value, "name": t.name, "usable": t.usable, "items": t.n_items,
                        "visual": t.visual.source if t.visual else None, "reason": t.reason,
                        "preview": bool(t.preview_png)} for t in tm.slides]
        return s

    return {"job_id": JOBS.submit("parse", work).id}


@app.get("/api/templates/{tid}/preview/{index}")
def template_preview(tid: str, index: int):
    folder = WORK / "cache" / tid / "preview"
    return FileResponse(_safe(folder, f"slide-{index + 1:02d}.png"))


# ---------------------------------------------------------------------------- генерация

@app.get("/api/examples")
def examples():
    out = []
    for d in sorted(EXAMPLES.iterdir()) if EXAMPLES.exists() else []:
        if (d / "brief.md").exists():
            from ..content.pack import load_pack
            p = load_pack(d)
            text = p.brief + "\n\n" + "\n".join(f"- {f.text}" for f in p.facts)
            out.append({"id": d.name, "title": p.title, "audience": p.audience, "purpose": p.purpose.value,
                        "text": text, "tables": [t.id for t in p.tables]})
    return out


@app.post("/api/runs")
async def create_run(template_id: str = Form(...), text: str = Form(""), title: str = Form(""), audience: str = Form(""),
                     purpose: str = Form(""), slides: int = Form(0), variants: str = Form(""), example: str = Form(""),
                     files: list[UploadFile] = File(default=[])):
    template = _template_path(template_id)
    if not text.strip() and not example:
        raise HTTPException(400, "нужен текст или пример контент-пакета")
    staged: list[Path] = []
    if files:
        stage_dir = WORK / "incoming" / hashlib.md5(text.encode()).hexdigest()[:8]
        stage_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            suf = Path(f.filename or "").suffix.lower()
            if suf not in _ALLOWED:
                continue
            dst = stage_dir / (re.sub(r"[^\w\-.]+", "_", Path(f.filename).stem)[:50] + suf)
            dst.write_bytes(await f.read())
            staged.append(dst)
    names = [v for v in variants.split(",") if v] or None

    def work(job):
        from ..content.pack import load_pack, pack_from_text
        if example:
            pack = load_pack(_safe(EXAMPLES, example))
            if text.strip():
                pack.brief, pack.source_text = text, text + "\n" + pack.source_text
        else:
            pack = pack_from_text(text, title=title or None, purpose=purpose or None, audience=audience,
                                  target_slides=slides or None, files=staged)
        pipe = _pipeline(job)
        stages = {"parse": "Разбор шаблона", "outline": "Каркас колоды", "variants": "Вёрстка, аудит и экспорт трёх вариантов",
                  "done": "Готово"}

        def on_stage(name, p):
            job.stage, job.progress = stages.get(name, name), p
            job.say(job.stage)
        res = pipe.run(template, pack, names, slides or None, on_stage=on_stage)
        return {"run_id": res.run_dir.name}

    return {"job_id": JOBS.submit("run", work).id}


@app.get("/api/runs")
def runs():
    if not RUNS.exists():
        return []
    items = []
    for d in sorted(RUNS.iterdir(), reverse=True)[:30]:
        meta = d / "run.json"
        if meta.exists():
            m = json.loads(meta.read_text(encoding="utf-8"))
            items.append({"run_id": d.name, "total": m["timings"].get("total"), "variants": list(m["variants"])})
    return items


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    d = _safe(RUNS, run_id)
    meta = json.loads((d / "run.json").read_text(encoding="utf-8"))
    outline = json.loads((d / "outline.json").read_text(encoding="utf-8"))
    out = {"run_id": run_id, "timings": meta["timings"], "mode": meta["mode"], "notes": meta.get("notes", []),
           "llm_stats": meta.get("llm_stats", {}), "title": outline.get("title"), "variants": {}}
    for v, info in meta["variants"].items():
        out["variants"][v] = _variant_view(d, v, info.get("label", v), info.get("timings", {}))
    return out


def _variant_view(d: Path, v: str, label: str, timings: dict) -> dict:
    vdir = d / v
    audit = json.loads((vdir / "audit.json").read_text(encoding="utf-8"))
    plan = json.loads((vdir / "plan.json").read_text(encoding="utf-8"))
    files = {k: f"{v}/{Path(p).name}" for k, p in (("pptx", vdir / f"deck_{v}.pptx"), ("pdf", vdir / f"deck_{v}.pdf"),
                                                    ("html", vdir / f"deck_{v}.html")) if Path(p).exists()}
    pngs = sorted((vdir / "png").glob("slide-*.png")) if (vdir / "png").exists() else []
    return {"label": label, "timings": timings, "files": files,
            "slides": [{"index": s["index"], "title": s["title"], "kind": s["kind"], "template_slide": s["template_slide"] + 1,
                        "png": f"{v}/png/{pngs[i].name}" if i < len(pngs) else None} for i, s in enumerate(plan["slides"])],
            "audit": audit, "llm_mode": plan.get("llm_mode")}


class FixRequest(BaseModel):
    ids: list[str]


@app.post("/api/runs/{run_id}/{variant}/fix")
def fix(run_id: str, variant: str, req: FixRequest):
    d = _safe(RUNS, run_id)
    _safe(d, variant)

    def work(job):
        job.stage = "Исправление и пересборка"
        r = _pipeline(job).fix(d, variant, req.ids)
        return {"fixed": len(r.audit.fixed), "left": len(r.audit.findings)}

    return {"job_id": JOBS.submit("fix", work).id}


@app.get("/api/files/{run_id}/{path:path}")
def file(run_id: str, path: str):
    p = _safe(_safe(RUNS, run_id), path)
    return FileResponse(p, filename=p.name if p.suffix in (".pptx", ".pdf") else None)


_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="web")
