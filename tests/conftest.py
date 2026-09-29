"""Общие фикстуры. Тесты не требуют GPU, модели и LibreOffice: offline-режим + рендер выключен."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("DECKGEN_CONFIG", str(ROOT / "config.yaml"))
TEMPLATES = sorted((ROOT / "data" / "templates").glob("*.pptx"))
PACK = ROOT / "data" / "content_packs" / "example_product_launch"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    for k, v in {"DECKGEN__LLM__PROVIDER": "offline", "DECKGEN__VLM__PROVIDER": "offline",
                 "DECKGEN__EXPORT__PDF_BACKEND": "none", "DECKGEN__PARSING__RENDER_PREVIEWS": "false",
                 "DECKGEN_OUTPUTS": str(tmp_path / "out")}.items():
        monkeypatch.setenv(k, v)
    from deckgen.settings import Settings
    return Settings.load(ROOT / "config.yaml")


@pytest.fixture(scope="session")
def templates():
    if not TEMPLATES:
        pytest.skip("нет шаблонов датасета в data/templates")
    return TEMPLATES


@pytest.fixture(scope="session")
def parsed(tmp_path_factory, templates):
    from deckgen.parsing.template import parse_template
    cache = tmp_path_factory.mktemp("cache")
    return {p.name: parse_template(p, cache, use_cache=False) for p in templates}


@pytest.fixture
def pack():
    from deckgen.content.pack import load_pack
    return load_pack(PACK)


@pytest.fixture(scope="session")
def blank_template(tmp_path_factory) -> Path:
    """«Незнакомый» шаблон: стандартный шаблон PowerPoint без единого слайда — только макеты."""
    from pptx import Presentation
    p = tmp_path_factory.mktemp("blank") / "blank.pptx"
    Presentation().save(str(p))
    return p


def by_name(parsed: dict, part: str):
    import unicodedata
    for k, v in parsed.items():
        if unicodedata.normalize("NFC", part) in unicodedata.normalize("NFC", k):
            return v
    pytest.skip(f"нет шаблона {part}")
