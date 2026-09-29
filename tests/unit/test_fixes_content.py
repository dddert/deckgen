"""Исправления и контент: подгонка кегля, удаление заглушек, подписи графиков, факты из текста, сверка цифр."""
from deckgen.audit.fixes import FixContext, apply_fixes
from deckgen.content.grounding import numbers_in, source_numbers, strip_ungrounded, ungrounded
from deckgen.content.pack import extract_facts, pack_from_text
from deckgen.models import (ChartSeries, ChartSpec, CheckCategory, DeckPlan, Finding, PlannedSlide, Severity, TextFill,
                            VisualFill)
from tests.conftest import by_name


def _f(check, fix, slide=0, shape=None, **ev):
    return Finding(id=f"{slide}:{check}:{shape}", check_id=check, category=CheckCategory.layout, deterministic=True,
                   severity=Severity.error, slide_index=slide, shape_id=shape, message="", fix_id=fix, evidence=ev)


def test_fixes(parsed, pack):
    tm = by_name(parsed, "WorkSpace")
    t = tm.slides[5]
    body = next(s for s in t.texts if s.role.value == "body")
    ch = ChartSpec(chart_type="column", categories=["a"], series=[ChartSeries(name="s", values=[1])], legend=False)
    ps = PlannedSlide(index=0, template_slide=5, layout_part=t.layout_part, kind=t.kind,
                      texts=[TextFill(shape_id=body.shape_id, paragraphs=["текст"], role=body.role)],
                      visual=VisualFill(kind="chart", box=t.items[0].box, chart=ch))
    plan = DeckPlan(deck_id="x", variant="balanced", template_id="t", template_file="t", content_pack_id="p", slides=[ps])
    c = FixContext(tm, pack, None, None)
    done = apply_fixes(plan, [_f("layout.text_overflow", "text_overflow", shape=body.shape_id),
                              _f("integrity.chart_labels", "chart_labels"),
                              _f("integrity.leftover_placeholder", "leftover_placeholder", shape="460")], None, c)
    assert len(done) == 3
    assert ps.texts[0].size_pt is not None and ps.texts[0].size_pt < body.size_pt
    assert ch.x_title and ch.y_title
    assert "460" in ps.delete_ids or any(tf.shape_id == "460" and tf.paragraphs == [""] for tf in ps.texts)


def test_facts_and_grounding():
    text = "Пилот длился 4 месяца. 68% участников используют ассистента ежедневно. NPS ассистента — 47 пунктов."
    facts = extract_facts(text)
    assert [f.value for f in facts] == ["4", "68", "47"]
    pack = pack_from_text(text, audience="менеджеры")
    src = source_numbers(pack)
    assert not ungrounded("68% используют каждый день", src)
    assert ungrounded("рост на 250%", src) == ["250"]
    assert strip_ungrounded("Всё хорошо. Рост на 250%.", src) == "Всё хорошо."
    assert numbers_in("1 200 пользователей и 2,5 часа") == ["1200", "2.5"]


def test_example_pack(pack):
    assert pack.tables and pack.facts and pack.must_include
