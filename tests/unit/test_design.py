"""Дизайн-правила: обрезка без многоточия, читаемый оттенок акцента, скилл visual_design в промптах Qwen."""
from __future__ import annotations

import re
from pathlib import Path

from deckgen.composing.fitter import truncate_words
from deckgen.ooxml.colors import apca_lc, readable_tint

ROOT = Path(__file__).resolve().parents[2]


def test_truncate_without_ellipsis_and_dangling_words():
    t = truncate_words("Запуск VK WorkSpace Assistant для руководителей продуктовых направлений", 34)
    assert "…" not in t and not t.endswith(("для", "и", "в"))
    assert t == "Запуск VK WorkSpace Assistant"
    assert truncate_words("Цель: убедить клиентов, что ассистент экономит время", 30) == "Цель: убедить клиентов"


def test_accent_gets_lighter_not_black():
    """Мелкий синий #0077FF на тёмной карточке: тот же оттенок, светлее — а не чёрный/белый."""
    t = readable_tint("0077FF", "212121", 55)
    assert t is not None and abs(apca_lc(t, "212121")) >= 55
    r, g, b = (int(t[i:i + 2], 16) for i in (0, 2, 4))
    assert b > r and b > g                                    # всё ещё синий


def test_prompts_carry_design_skill():
    from deckgen.prompts import PromptRegistry
    from deckgen.settings import Settings
    pr = PromptRegistry(Settings.load(ROOT / "config.yaml"))
    assert pr.version("slide_writer") == "v3" and "visual_design/v1" in pr.skills_of("slide_writer")
    assert all("literacy/v1" in pr.skills_of(a) for a in ("slide_writer", "shorten", "rewrite", "proofread"))
    w = pr.render("slide_writer", deck_title="Т", purpose="sales", audience="", language="ru", tone="", position=1, total=3,
                  prev_title="—", next_title="—", kind="cards", title="Т", message="", points=[], facts=[], context="",
                  style_note="", slots=[{"key": "s1", "label": "текст", "item": None, "limit": "≤20 зн.", "hint": "h1", "demo": "a"},
                                        {"key": "s2", "label": "текст", "item": None, "limit": "≤20 зн.", "hint": "", "demo": ""}],
                  n_items=0)
    assert "как устроен хороший дизайн слайда" in w
    assert "грамотность и точность цифр" in w                  # скилл literacy подставлен
    assert "(s1)" in w and "\n- (s2)" in w                   # каждый слот — своей строкой
    a = pr.render("audit_semantic", slide_text="", kind="", language="", prev_title="", next_title="", facts=[])
    assert max(int(q) for q in re.findall(r"^(\d+)\. ", a, re.M)) == 14
