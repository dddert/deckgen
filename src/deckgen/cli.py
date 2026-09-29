"""CLI: deckgen parse | run | fix | audit-catalog | prompts | serve."""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

from .settings import Settings

app = typer.Typer(help="Цифровой дизайнер презентаций v2", no_args_is_help=True)


def _settings(config: Path | None) -> Settings:
    return Settings.load(config)


@app.command()
def parse(template: Path, config: Path = typer.Option(None, help="config.yaml"), debug: bool = False):
    """Разобрать шаблон: дизайн-система и типы слайдов-образцов (без генерации)."""
    from .parsing.template import dump_debug
    from .pipeline.orchestrator import Pipeline
    p = Pipeline(_settings(config), log=lambda m: rprint(f"[dim]{m}[/]"))
    tm = p.parse(template)
    d = tm.design
    rprint(f"[bold]{template.name}[/] {d.slide_w_in:.2f}×{d.slide_h_in:.2f} in · образцов {len(tm.slides)}, пригодных {len(tm.usable())}")
    rprint("Шрифты:", d.fonts[:3], "| текст на светлом/тёмном:", d.text_on_light, d.text_on_dark)
    t = Table("hex", "роль", "исп.")
    for c in d.palette[:10]:
        t.add_row(f"#{c.hex}", c.role.value, str(c.usage))
    rprint(t)
    t = Table("#", "тип", "описание", "элем.", "график", "ок", "примечание")
    for s in tm.slides:
        t.add_row(str(s.index + 1), s.kind.value, s.name, str(s.n_items or ""), s.visual.source if s.visual else "",
                  "✓" if s.usable else "✗", s.reason[:40])
    rprint(t)
    if debug:
        print(dump_debug(tm))


@app.command()
def run(template: Path, content: Path = typer.Argument(..., help="папка контент-пакета или .txt/.md с текстом"),
        config: Path = typer.Option(None), variant: list[str] = typer.Option(None), slides: int = typer.Option(None)):
    """Сквозной прогон: шаблон + контент -> 3 варианта (.pptx/.pdf/.html) + аудит."""
    from .content.pack import load_pack, pack_from_text
    from .pipeline.orchestrator import Pipeline
    s = _settings(config)
    pack = load_pack(content) if content.is_dir() else pack_from_text(content.read_text(encoding="utf-8"), target_slides=slides)
    p = Pipeline(s, log=lambda m: rprint(f"[dim]{m}[/]"))
    res = p.run(template, pack, variant or None, slides)
    tm = res.timings
    rprint(f"[bold]run:[/] {res.run_dir}\n режим {res.mode} · parse {tm['parse']:.1f}s · outline {tm['outline']:.1f}s · всего {tm['total']:.1f}s")
    t = Table("вариант", "слайдов", "ошибок", "предупр.", "исправлено", "время", "pptx")
    for v, r in res.variants.items():
        t.add_row(r.label, str(len(r.plan.slides)), str(r.audit.count(_sev("error"))), str(r.audit.count(_sev("warning"))),
                  str(len(r.audit.fixed)), f"{r.timings['total']:.1f}s", r.files.get("pptx", ""))
    rprint(t)
    for n, st in res.llm_stats.items():
        rprint(f"  {n}: вызовов {st['calls']}, ошибок {st['errors']}, токенов {st['prompt_tokens']}/{st['completion_tokens']}")
    for note in res.notes:
        rprint(f"[yellow]• {note}[/]")


def _sev(name: str):
    from .models import Severity
    return Severity(name)


@app.command()
def fix(run_dir: Path, variant: str, finding: list[str] = typer.Option(..., help="id замечания, check_id или fix_id"),
        config: Path = typer.Option(None)):
    """Применить выбранные исправления к варианту готового прогона и пересобрать файлы."""
    from .pipeline.orchestrator import Pipeline
    r = Pipeline(_settings(config)).fix(run_dir, variant, finding)
    rprint(f"исправлено: {len(r.audit.fixed)}; осталось замечаний: {len(r.audit.findings)} -> {r.files.get('pptx')}")


@app.command("audit-catalog")
def audit_catalog(markdown: bool = False):
    """Список проверок аудита (источник для AUDIT.md)."""
    from .audit import catalog
    rows = catalog()
    if markdown:
        print("| id | категория | детерм. | исправление | описание |\n|---|---|---|---|---|")
        for r in rows:
            print(f"| `{r['id']}` | {r['category']} | {'да' if r['deterministic'] else 'нет (VLM)'} | {r.get('fix') or '—'} | {r['description']} |")
    else:
        print(json.dumps(rows, ensure_ascii=False, indent=2))


@app.command()
def prompts(config: Path = typer.Option(None)):
    """Реестр агентов и скиллов с закреплёнными версиями."""
    from .prompts import PromptRegistry
    print(json.dumps(PromptRegistry(_settings(config)).catalog(), ensure_ascii=False, indent=2))


@app.command()
def serve(config: Path = typer.Option(None), host: str = None, port: int = None):
    """API + веб-интерфейс."""
    import os

    import uvicorn
    if config:
        os.environ["DECKGEN_CONFIG"] = str(config)
    s = _settings(config)
    uvicorn.run("deckgen.api.app:app", host=host or s.api.host, port=port or s.api.port, workers=1)


if __name__ == "__main__":
    app()
