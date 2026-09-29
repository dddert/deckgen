"""Карта занятости и APCA: текст не ложится на графику шаблона и читается на своём фоне (скриншоты пользователя)."""
from __future__ import annotations

from pathlib import Path

import pytest

from deckgen.ooxml.colors import apca_lc, apca_min, best_text_color

TEMPLATES = Path(__file__).resolve().parents[2] / "data" / "templates"


def _tpl(prefix: str) -> Path:
    found = [p for p in TEMPLATES.glob("*.pptx") if p.name.startswith(prefix)]
    if not found:
        pytest.skip(f"нет шаблона {prefix}")
    return found[0]


def test_apca():
    # значения эталонной реализации APCA-W3 0.0.98G-4g
    assert round(apca_lc("000000", "FFFFFF"), 1) == 106.0
    assert round(apca_lc("FFFFFF", "000000"), 1) == -107.9
    assert round(apca_lc("888888", "FFFFFF"), 1) == 63.1
    # то, на что жаловался пользователь: чёрный на холодном синем нечитаем, белый — нормально
    assert abs(apca_lc("000000", "0077FF")) < 45 < abs(apca_lc("FFFFFF", "0077FF"))
    assert best_text_color("0077FF", ["000000", "FFFFFF"]) == "FFFFFF"
    assert best_text_color("0092F3", ["212121", "FAFCFF"]) == "FAFCFF"
    assert apca_min(60) == 45 and apca_min(16) == 60 and apca_min(10) == 70


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    from deckgen.parsing.template import parse_template
    out = tmp_path_factory.mktemp("tpl")
    return {k: parse_template(_tpl(k), out, use_cache=False) for k in ("Шаблон презентации VK Education", "VK Tech")}


def test_cover_title_white_and_clear_of_pattern(parsed):
    """Обложка Education: пустой плейсхолдер наследует белый 60 pt; рамка не заходит на паттерн (x ≥ 0.66)."""
    t = next(x for x in parsed["Шаблон презентации VK Education"].slides[0].texts if x.role.value == "title")
    assert t.color == "FFFFFF" and t.size_pt == 60 and t.bg_color == "0077FF"
    assert t.limited and t.box.x2 <= 0.67


def test_card_text_stops_above_ball(parsed):
    """VK Tech, карточка с 3D-шаром: текст — только в верхней свободной части картинки-карточки."""
    ts = parsed["VK Tech"].slides[27]
    slot = ts.slot("1123")
    assert slot is not None and slot.limited
    assert slot.box.y2 < 0.47                     # верх шара — на 0.47 высоты слайда


def test_donut_number_stays_in_ring(parsed):
    """VK Tech, кольцевые диаграммы: цифра без переноса может расти только внутри кольца."""
    ts = parsed["VK Tech"].slides[44]
    for sid in ("2348", "2349", "2350"):
        slot = ts.slot(sid)
        assert slot is not None and slot.wide_box is not None
        assert slot.wide_box.w < 0.2              # отверстие кольца, а не 1.6 × рамки
