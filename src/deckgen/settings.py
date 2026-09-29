"""config.yaml -> типизированные настройки.

${VAR:-default} подставляется из окружения; DECKGEN__SECTION__KEY=value переопределяет любой ключ
(значение парсится как YAML: true/false/числа/списки).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _apply_overrides(raw: dict) -> dict:
    for key, val in os.environ.items():
        if not key.startswith("DECKGEN__"):
            continue
        path = [p.lower() for p in key[len("DECKGEN__"):].split("__")]
        node = raw
        for p in path[:-1]:
            node = node.setdefault(p, {})
        node[path[-1]] = yaml.safe_load(val)
    return raw


class ModelEndpoint(BaseModel):
    provider: str = "openai_compat"
    base_url: str = "http://localhost:18000/v1"
    api_key: str = "none"
    model: str = "qwen"
    temperature: float = 0.4
    top_p: float = 0.8
    max_tokens: int = 3000
    timeout_seconds: int = 120
    structured_output: bool = True
    max_concurrency: int = 16
    json_retries: int = 2
    fallback_to_offline: bool = True
    extra_body: dict = {}


class T2ISettings(BaseModel):
    enabled: bool = False
    model: str = "black-forest-labs/FLUX.2-klein-4B"
    steps: int = 4
    guidance: float = 1.0
    width: int = 1024
    height: int = 576
    cpu_offload: bool = True
    min_free_vram_gb: float = 10
    max_images_per_deck: int = 4


class RunSettings(BaseModel):
    seed: int = 42
    target_slides: int = 12
    max_generation_seconds: int = 300
    workdir: str = "./data/outputs"
    templates_dir: str = "./data/templates"


class PromptSettings(BaseModel):
    registry: str = "./prompts/registry.yaml"
    skills_dir: str = "./skills"
    skeleton: str = "v1"
    slide_writer: str = "v1"
    shorten: str = "v1"
    slide_classifier: str = "v1"
    audit_semantic: str = "v1"
    image_prompt: str = "v1"
    rewrite: str = "v1"


class ParsingSettings(BaseModel):
    cache: bool = True
    render_previews: bool = True
    vlm_classify: bool = True
    render_dpi: int = 110
    max_shapes_per_layout: int = 70


class ComposingSettings(BaseModel):
    variants: list[str] = ["balanced", "visual", "compact"]
    min_font_scale: float = 0.8
    shorten_rounds: int = 1
    reflow_items: bool = True


class AuditSettings(BaseModel):
    contrast_min: float = 4.5
    max_bullets: int = 6
    max_words_per_bullet: int = 15
    table_max_rows: int = 7
    table_max_cols: int = 5
    chart_max_series: int = 5
    fill_ratio_min: float = 0.25
    fill_ratio_max: float = 0.75
    max_font_families: int = 2
    semantic_checks: bool = True
    auto_fix: list[str] = []
    auto_fix_rounds: int = 2


class ExportSettings(BaseModel):
    formats: list[str] = ["pptx", "pdf", "html"]
    pdf_backend: str = "auto"
    soffice_bin: str = "soffice"
    prune_unused_layouts: bool = True
    html_image_max_px: int = 1600


class ApiSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 18080
    cors_origins: list[str] = []
    max_upload_mb: int = 80


class Settings(BaseModel):
    run: RunSettings = RunSettings()
    llm: ModelEndpoint = ModelEndpoint()
    vlm: ModelEndpoint = ModelEndpoint(temperature=0.0, max_tokens=900)
    text_to_image: T2ISettings = T2ISettings()
    prompts: PromptSettings = PromptSettings()
    parsing: ParsingSettings = ParsingSettings()
    composing: ComposingSettings = ComposingSettings()
    audit: AuditSettings = AuditSettings()
    export: ExportSettings = ExportSettings()
    api: ApiSettings = ApiSettings()

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        path = Path(path or os.environ.get("DECKGEN_CONFIG", "config.yaml"))
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls.model_validate(_apply_overrides(_expand(raw or {})))
