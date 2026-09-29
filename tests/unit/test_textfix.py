"""Детерминированная вычитка текста и нумерация: опечатки, хвосты обрезки, цифры фактов, номера шагов."""
import pytest

from deckgen.composing.composer import ordinal_text
from deckgen.composing.fitter import truncate_words
from deckgen.composing.writer import _fact_number, _heading
from deckgen.content.grounding import numbers_in
from deckgen.content.pack import _table, extract_facts
from deckgen.content.textfix import issues, sanitize, unwrap
from deckgen.models import Fact


@pytest.mark.parametrize("raw,want", [
    ("прoект в в срок ,  готов..", "Проект в срок, готов."),     # латинская «o», повтор, пробелы, «..»
    ("Результаты пилота в", "Результаты пилота"),               # предлог в конце после обрезки
    ("Развёртывание (on-premise", "Развёртывание"),             # незакрытая скобка
    ("экономия 3.5 ч в неделю", "Экономия 3,5 ч в неделю"),     # десятичная запятая, заглавная
    ("Цель - убедить совет", "Цель — убедить совет"),           # дефис → тире
    ('"Проект" готов', "«Проект» готов"),
    ("Хорошо，отлично。", "Хорошо, отлично."),                  # CJK-пунктуация
    ("Micrоsoft Teams", "Microsoft Teams"),                     # кириллическая «о» в латинском слове
    ("Рост 68 %", "Рост 68%"),
    ("Итоги 功能 пилота", "Итоги пилота"),                      # иероглифы в русском тексте
])
def test_sanitize_fixes(raw, want):
    assert sanitize(raw) == want
    assert numbers_in(sanitize(raw)) == numbers_in(raw)         # вычитка не меняет цифры


@pytest.mark.parametrize("text", ["AI-ассистент для NPS", "v2.0 релиз 12.05.2025", "iPhone в каждой команде",
                                  "eNPS вырос", "Время ответа снизилось с 12 до 4 минут.", "1) первый шаг",
                                  "Среднее время — 5 с", "Что это даёт"])
def test_sanitize_keeps_correct_text(text):
    assert sanitize(text) == text
    assert not issues(text)


def test_issues_report():
    assert any("алфавит" in x for x in issues("прoект"))
    assert any("повтор" in x for x in issues("данные данные"))
    assert any("оборванная" in x for x in issues("Пилот охватил команды и"))
    assert any("скобка" in x for x in issues("Решение (on-premise"))
    assert not issues("5 мин", sentence=False)


def test_unwrap_joins_hard_wrapped_brief():
    text = "Пилот длился 4 месяца,\nохватил 3 команды.\nДалее — план:\n- шаг один\nСледующий абзац."
    assert unwrap(text).split("\n") == ["Пилот длился 4 месяца, охватил 3 команды.", "Далее — план:", "- шаг один",
                                        "Следующий абзац."]
    facts = extract_facts("Время ответа снизилось\nс 12 до 4 минут за квартал.")
    assert [f.text for f in facts] == ["Время ответа снизилось с 12 до 4 минут за квартал."]


def test_heading_keeps_acronyms_and_dash():
    assert _heading("NPS вырос до 62 пунктов") == "NPS вырос"
    assert _heading("AI-ассистент — помощник в работе") == "AI-ассистент"
    assert not _heading("Результаты пилота в командах продаж").endswith(" в")


def test_fact_number_prefers_value():
    assert _fact_number(Fact(id="f1", text="Время ответа снизилось с 12 до 4 минут", value=4, unit="мин")) == "4 мин"
    assert _fact_number(Fact(id="f2", text="Время ответа снизилось с 12 до 4 минут")) == "4 мин"
    assert _fact_number(Fact(id="f3", text="Доля активных — 68% пользователей")) == "68%"
    assert _fact_number(Fact(id="f4", text="Экономия 3.5 часа в неделю", value=3.5)) == "3,5 ч"


def test_truncate_closes_brackets():
    out = truncate_words("Решение разворачивается в контуре заказчика (on-premise, без внешних сервисов)", 55)
    assert "(" not in out and not out.endswith((" в", ","))


def test_ordinal_text_follows_demo_format():
    assert [ordinal_text(n, "01") for n in (1, 10)] == ["01", "10"]
    assert ordinal_text(3, "1.") == "3."
    assert ordinal_text(2, "1)") == "2)"
    assert ordinal_text(4, "7") == "4"


def test_table_title_is_russian():
    t = _table("adoption_by_month", [["Месяц", "Активные пользователи"], ["Янв", "10"]])
    assert t.title == "Активные пользователи"
    assert _table("выручка", [["Месяц", "Сумма"]]).title == "выручка"


def test_proofread_accepts_only_safe_corrections(settings):
    """Корректор исправляет опечатку, но правка с другой цифрой или переписанным смыслом отбрасывается."""
    from types import SimpleNamespace

    from deckgen.composing.writer import proofread
    from deckgen.models import ContentPack, OutlineSlide, SlideKind, SlotRole
    from deckgen.prompts import PromptRegistry

    class Stub:
        def complete_json(self, messages, schema, **kw):
            fixed = {"Ассистент сокращает время ответа в 3 раза": "Ассистент сокращает время ответа в 3 раза.",
                     "Сотрудники экономят 6,5 часов в неделю": "Сотрудники экономят 7 часов в неделю",
                     "Пилот прошол в трёх командах": "Пилот прошёл в трёх командах",
                     "Внедрение займёт один квартал": "Мы полностью перепишем процессы компании"}
            prompt = messages[0].content
            return schema(**{k: next(v for o, v in fixed.items() if f"({k}): «{o}»" in prompt) for k in schema.model_fields})

    slot = SimpleNamespace(shape_id="1", role=SlotRole.body)
    sp = [SimpleNamespace(slot=slot)]
    texts = {"1": ["Ассистент сокращает время ответа в 3 раза", "Сотрудники экономят 6,5 часов в неделю",
                   "Пилот прошол в трёх командах", "Внедрение займёт один квартал"]}
    pack = ContentPack(id="p", title="t", brief="", language="ru")
    o = OutlineSlide(kind=SlideKind.text, title="Итоги")
    out = proofread(o, texts, sp, pack, Stub(), PromptRegistry(settings))["1"]
    assert out[0] == "Ассистент сокращает время ответа в 3 раза."
    assert out[1] == "Сотрудники экономят 6,5 часов в неделю"          # цифра изменилась — правка отброшена
    assert out[2] == "Пилот прошёл в трёх командах"
    assert out[3] == "Внедрение займёт один квартал"                   # смысл переписан — отброшено


def test_truncation_leaves_no_dangling_tail():
    from deckgen.composing.writer import _drop_repeat
    assert _heading("Пилот длился 4 месяца у трёх клиентов") == "Пилот длился 4 месяца"      # цифра с единицей
    assert truncate_words("Руководители продуктовых команд получают отчёт", 25) == "Руководители"
    assert truncate_words("Пилот длился 4 месяца у трёх клиентов", 16) == "Пилот длился"   # не «…длился 4»
    assert truncate_words("Дорожная карта до конца 2026 года и дальше", 28).endswith("2026")  # год — не обрывок
    # повтор заголовка снимается только на границе фразы, а не посреди предложения
    assert _drop_repeat("Пилот длился 4 месяца у трёх клиентов", ["Пилот длился 4"], "ru").startswith("Пилот")
    assert _drop_repeat("Скорость ответа — выросла втрое за квартал", ["Скорость ответа"], "ru") == \
        "Выросла втрое за квартал"


def test_wrapped_demo_line_is_one_part():
    """Строка образца, перенесённая вручную (a:br), — одна часть: текст пишется один раз, без склейки."""
    from lxml import etree

    from deckgen.models import TextFill
    from deckgen.parsing.analyzer import _merge_wrapped
    from deckgen.render.pptx_builder import _set_composite

    segs = [("Шрифт для заголовков —", 18.0, False, "p", None), ("VK Sans Display", 18.0, False, "br", None),
            ("Основной цвет — синий, но можно", 14.0, False, "p", None), ("свободно использовать.", 14.0, False, "br", None)]
    merged, lines = _merge_wrapped(segs)
    assert [m[0] for m in merged] == ["Шрифт для заголовков — VK Sans Display", "Основной цвет — синий, но можно свободно использовать."]
    assert lines == [2, 2]

    A = "http://schemas.openxmlformats.org/drawingml/2006/main"
    xml = (f'<p:txBody xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="{A}">'
           '<a:p><a:r><a:rPr sz="1800"/><a:t>Шрифт для заголовков — </a:t></a:r><a:br/>'
           '<a:r><a:rPr sz="1800"/><a:t>VK Sans Display</a:t></a:r></a:p>'
           '<a:p><a:r><a:rPr sz="1400"/><a:t>Основной цвет — синий, но можно </a:t></a:r><a:br/>'
           '<a:r><a:rPr sz="1400"/><a:t>свободно использовать.</a:t></a:r></a:p></p:txBody>')
    txb = etree.fromstring(xml)
    tf = TextFill(shape_id="1", paragraphs=["Мы запускаем AI-ассистента", "Внутри VK WorkSpace"])
    _set_composite(txb, txb.findall(f"{{{A}}}p"), tf, lines)
    paras = ["".join(t.text or "" for t in p.iter(f"{{{A}}}t")) for p in txb.findall(f"{{{A}}}p")]
    assert paras == ["Мы запускаем AI-ассистента", "Внутри VK WorkSpace"]
    assert not txb.findall(f".//{{{A}}}br")


def test_composite_part_does_not_repeat_previous():
    from deckgen.composing.writer import _drop_prefix_parts
    assert _drop_prefix_parts(["Руководители", "Руководители продуктовых направлений"]) == \
        ["", "Руководители продуктовых направлений"]
    assert _drop_prefix_parts(["68%", "пользователей активны"]) == ["68%", "пользователей активны"]
