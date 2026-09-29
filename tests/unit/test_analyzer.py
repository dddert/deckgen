"""Разбор слайдов-образцов на реальных шаблонах датасета и на шаблоне без образцов (незнакомый)."""
from deckgen.models import PictureRole, SlideKind, SlotRole
from tests.conftest import by_name


def test_every_template_has_core_kinds(parsed):
    for name, tm in parsed.items():
        kinds = {t.kind for t in tm.usable()}
        assert SlideKind.title in kinds, name
        assert kinds & {SlideKind.cards, SlideKind.process, SlideKind.factoids}, name
        assert len(tm.usable()) >= 10, name
        assert tm.design.fonts[0] == "Play", name              # гарнитура из текста, а не из темы (там Arial)


def test_service_slides_excluded(parsed):
    tm = by_name(parsed, "VK Tech")
    bad = {t.index + 1: t.reason for t in tm.slides if not t.usable}
    assert 31 in bad and "код" in bad[31]                      # слайд с кодом
    assert any("библиотек" in r for r in bad.values())         # библиотека логотипов/иконок


def test_workspace_agenda_items_and_ordinals(parsed):
    t = by_name(parsed, "WorkSpace").slides[1]
    assert t.kind == SlideKind.agenda and t.n_items == 5
    assert sum(1 for s in t.texts if s.role == SlotRole.ordinal) == 5


def test_steps_inside_big_group_are_items(parsed):
    t = by_name(parsed, "Education").slides[20]                # 4 кружка-номера + подписи внутри одной группы
    assert t.kind == SlideKind.process and t.n_items == 4


def test_chart_examples_become_native_chart_area(parsed):
    tm = by_name(parsed, "Education")
    charts = [t for t in tm.usable() if t.kind == SlideKind.chart]
    assert charts and all(t.visual is not None for t in charts)
    assert any(p.role == PictureRole.chart_example for t in charts for p in t.pictures)


def test_composite_slot(parsed):
    t = by_name(parsed, "WorkSpace").slides[16]                # «ххх% + данные показателя» в одной фигуре
    comp = [s for s in t.texts if len(s.parts) >= 2]
    assert comp and comp[0].parts[0].role == SlotRole.number


def test_blank_template_gets_virtual_samples(blank_template, tmp_path):
    from deckgen.parsing.template import parse_template
    tm = parse_template(blank_template, tmp_path, use_cache=False)
    kinds = {t.kind for t in tm.usable()}
    assert SlideKind.title in kinds and SlideKind.two_columns in kinds and SlideKind.text in kinds
    assert tm.design.source_file.endswith("augmented.pptx")


def test_layout_names_do_not_override_structure(parsed):
    """Google Slides называет макеты «N_Титульный слайд» — это не делает контентные слайды титулами."""
    tm = by_name(parsed, "WorkSpace")
    assert sum(1 for t in tm.usable() if t.kind == SlideKind.title) <= 2
    assert tm.slides[5].kind == SlideKind.cards
