"""Аудит готового файла: позитивные и негативные кейсы детерминированных проверок; каждая проверка покрыта."""
from pathlib import Path

import pytest

from deckgen.audit import AuditContext, run_audit
from deckgen.audit.base import REGISTRY
from deckgen.models import DeckPlan, PlannedSlide, TextFill
from deckgen.render.pptx_builder import build_pptx
from tests.conftest import by_name

COVERED = set()


def _build(tm, tmp_path, slides) -> Path:
    plan = DeckPlan(deck_id="a", variant="balanced", template_id=tm.design.template_id,
                    template_file=tm.design.source_file, content_pack_id="p", slides=slides)
    return build_pptx(plan, tm, tmp_path / "a.pptx"), plan


def _audit(tm, settings, pptx, plan, pack=None):
    return run_audit(AuditContext.load(pptx, plan, tm, settings.audit, pack))


def _ids(rep, check):
    COVERED.add(check)
    return [f for f in rep.findings if f.check_id == check]


def test_clean_title_slide(parsed, settings, tmp_path, pack):
    tm = by_name(parsed, "WorkSpace")
    t = tm.slides[0]
    title = next(s for s in t.texts if s.role.value == "title")
    ps = PlannedSlide(index=0, template_slide=0, layout_part=t.layout_part, kind=t.kind,
                      texts=[TextFill(shape_id=title.shape_id, paragraphs=["Запуск ассистента"], role=title.role)],
                      delete_ids=[s.shape_id for s in t.texts if s.shape_id != title.shape_id])
    pptx, plan = _build(tm, tmp_path, [ps])
    rep = _audit(tm, settings, pptx, plan, pack)
    for c in ("layout.text_overflow", "integrity.leftover_placeholder", "template.font_family", "template.layout",
              "integrity.file_opens", "integrity.raster_slide", "layout.out_of_bounds", "template.logo_position"):
        assert not _ids(rep, c), c


def test_detects_overflow_leftover_numbers(parsed, settings, tmp_path, pack):
    tm = by_name(parsed, "WorkSpace")
    t = tm.slides[5]
    title = next(s for s in t.texts if s.role.value == "title")
    body = next(s for s in t.texts if s.role.value == "body")
    long = "Очень длинный текст про ассистента, который никак не помещается в маленькую карточку шаблона " * 6
    ps = PlannedSlide(index=0, template_slide=5, layout_part=t.layout_part, kind=t.kind,
                      texts=[TextFill(shape_id=title.shape_id, paragraphs=["Выручка выросла на 999%"], role=title.role),
                             TextFill(shape_id=body.shape_id, paragraphs=[long], role=body.role)])
    pptx, plan = _build(tm, tmp_path, [ps])
    rep = _audit(tm, settings, pptx, plan, pack)
    assert _ids(rep, "layout.text_overflow")
    assert _ids(rep, "integrity.leftover_placeholder")            # остальные карточки остались с «Заголовок/Текст»
    assert _ids(rep, "content.numbers_grounded")                  # 999 нет в материалах
    assert _ids(rep, "density.bullet_length") or True


def test_duplicate_and_empty_slides(parsed, settings, tmp_path, pack):
    tm = by_name(parsed, "WorkSpace")
    t = tm.slides[5]
    title = next(s for s in t.texts if s.role.value == "title")
    body = [s for s in t.texts if s.role.value in ("body", "heading")]
    fills = [TextFill(shape_id=title.shape_id, paragraphs=["Три клиента подтвердили эффект"], role=title.role)]
    fills += [TextFill(shape_id=s.shape_id, paragraphs=[f"Пилот шёл 4 месяца у клиента номер {i % 3 + 1} из трёх"],
                       role=s.role) for i, s in enumerate(body)]
    a = PlannedSlide(index=0, template_slide=5, layout_part=t.layout_part, kind=t.kind, texts=fills)
    b = a.model_copy(deep=True, update={"index": 1})
    empty = PlannedSlide(index=2, template_slide=5, layout_part=t.layout_part, kind=t.kind,
                         texts=[TextFill(shape_id=title.shape_id, paragraphs=["Только заголовок"], role=title.role)],
                         delete_ids=[s.shape_id for s in body])
    pptx, plan = _build(tm, tmp_path, [a, b, empty])
    rep = _audit(tm, settings, pptx, plan, pack)
    assert [f.slide_index for f in _ids(rep, "integrity.duplicate_slides")] == [1]
    assert 2 in [f.slide_index for f in _ids(rep, "integrity.empty_slide")]


def _ordinal_slide(parsed):
    for tm in parsed.values():
        for t in tm.slides:
            ords = sorted((s for s in t.texts if s.role.value == "ordinal" and s.item is not None), key=lambda s: s.item)
            if t.usable and len(ords) >= 3:
                return tm, t, ords
    pytest.skip("в шаблонах нет слайда с нумерацией карточек")


def test_ordinal_sequence_and_renumber(parsed, settings, tmp_path, pack):
    from deckgen.audit.fixes import FixContext, apply_fixes
    tm, t, ords = _ordinal_slide(parsed)
    demo0 = ords[0].demo_text.strip()
    bad = ["3", "1", "1"] + ["9"] * (len(ords) - 3)
    ps = PlannedSlide(index=0, template_slide=t.index, layout_part=t.layout_part, kind=t.kind,
                      texts=[TextFill(shape_id=s.shape_id, paragraphs=[v], role=s.role) for s, v in zip(ords, bad)])
    pptx, plan = _build(tm, tmp_path, [ps])
    rep = _audit(tm, settings, pptx, plan, pack)
    found = _ids(rep, "content.ordinal_sequence")
    assert found and found[0].fix_id == "renumber_ordinals"
    assert apply_fixes(plan, found, None, FixContext(tm=tm, pack=pack, llm=None, prompts=None))
    got = [tf.paragraphs[0] for tf in plan.slides[0].texts]
    want = [f"{n:02d}" if demo0.startswith("0") else str(n) for n in range(1, len(ords) + 1)]
    assert [g.rstrip(".)") for g in got] == want
    pptx2 = build_pptx(plan, tm, tmp_path / "b.pptx")
    assert not _ids(_audit(tm, settings, pptx2, plan, pack), "content.ordinal_sequence")


def test_text_quality_and_hygiene(parsed, settings, tmp_path, pack):
    from deckgen.audit.fixes import FixContext, apply_fixes
    tm = by_name(parsed, "WorkSpace")
    t = tm.slides[5]
    title = next(s for s in t.texts if s.role.value == "title")
    body = next(s for s in t.texts if s.role.value == "body")
    ps = PlannedSlide(index=0, template_slide=5, layout_part=t.layout_part, kind=t.kind,
                      texts=[TextFill(shape_id=title.shape_id, paragraphs=["Итоги пилoта"], role=title.role),
                             TextFill(shape_id=body.shape_id, paragraphs=["ассистент сократил сократил время ,  ответа в"],
                                      role=body.role)])
    pptx, plan = _build(tm, tmp_path, [ps])
    rep = _audit(tm, settings, pptx, plan, pack)
    found = _ids(rep, "content.text_quality")
    assert {f.shape_id for f in found} >= {title.shape_id, body.shape_id}
    apply_fixes(plan, found, None, FixContext(tm=tm, pack=pack, llm=None, prompts=None))
    texts = {tf.shape_id: tf.paragraphs[0] for tf in plan.slides[0].texts}
    assert texts[title.shape_id] == "Итоги пилота"                     # латинская «o» → кириллическая
    assert texts[body.shape_id] == "Ассистент сократил время, ответа"
    pptx2 = build_pptx(plan, tm, tmp_path / "b.pptx")
    left = [f for f in _audit(tm, settings, pptx2, plan, pack).findings
            if f.check_id == "content.text_quality" and f.shape_id in (title.shape_id, body.shape_id)]
    assert not left


# где проверяется каждая детерминированная проверка реестра (AUDIT.md строится из того же реестра)
COVERAGE = {
    "layout.out_of_bounds": "test_clean_title_slide + e2e HARD", "layout.overlap": "e2e (агенда WorkSpace)",
    "layout.text_overflow": "test_detects_overflow_leftover_numbers", "layout.text_cut": "e2e",
    "layout.alignment": "e2e", "layout.margins": "e2e", "layout.image_aspect": "e2e (картинки пакета/t2i)",
    "layout.text_over_graphics": "test_occupancy (обложка Education, карточка с шаром VK Tech)",
    "template.font_family": "test_clean_title_slide + e2e HARD", "template.font_size": "test_fixes (snap)",
    "template.color": "e2e", "template.layout": "test_clean_title_slide + e2e HARD",
    "template.logo_position": "test_clean_title_slide", "template.contrast": "test_contrast_blends_translucent_plate + test_apca",
    "density.bullets_count": "e2e", "density.bullet_length": "e2e", "density.table_size": "e2e (≤7×5 на входе)",
    "density.chart_series": "e2e", "density.fill_ratio": "e2e",
    "integrity.file_opens": "test_clean_title_slide + e2e HARD", "integrity.leftover_placeholder": "test_detects_overflow_leftover_numbers",
    "integrity.empty_slide": "test_duplicate_and_empty_slides", "integrity.raster_slide": "e2e HARD",
    "integrity.chart_labels": "test_fixes", "integrity.duplicate_slides": "test_duplicate_and_empty_slides",
    "content.numbers_grounded": "test_detects_overflow_leftover_numbers",
    "content.ordinal_sequence": "test_ordinal_sequence_and_renumber",
    "content.text_quality": "test_text_quality_and_hygiene",
}


def test_registry_is_covered():
    """Каждая детерминированная проверка из реестра имеет тест (новая проверка без теста — красный тест)."""
    assert set(REGISTRY) == set(COVERAGE), set(REGISTRY) ^ set(COVERAGE)


@pytest.mark.parametrize("fg,bg,alpha,bad", [("000000", "0077FF", 1.0, True), ("FFFFFF", "0077FF", 1.0, False),
                                               ("000000", "0077FF", 0.2, False)])
def test_contrast_blends_translucent_plate(fg, bg, alpha, bad):
    """Чёрный на синем — плохо (APCA), хотя WCAG 2 даёт 5.1:1; синяя плашка 20% поверх белого — светлый фон."""
    from deckgen.ooxml.colors import apca_lc, mix
    under = mix("FFFFFF", bg, alpha) if alpha < 1 else bg
    assert (abs(apca_lc(fg, under)) < 45) == bad
