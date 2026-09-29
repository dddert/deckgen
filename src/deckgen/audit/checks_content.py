"""Качество текста и нумерации — детерминированно, без модели.

content.ordinal_sequence: номера шагов («01 02 03») на слайде идут подряд, без повторов и пропусков.
content.text_quality: опечатки, которые ловятся правилами (смешение алфавитов, иероглифы, повтор слова,
пробелы и пунктуация, незакрытые скобки, оборванная фраза, строчная буква в начале) — см. content/textfix.py.
"""
from __future__ import annotations

import re

from ..content.textfix import issues
from ..models import CheckCategory, Finding, Severity, SlotRole
from .base import AuditContext, check, finding

C = CheckCategory.content
_PLAIN = {SlotRole.number, SlotRole.ordinal, SlotRole.label, SlotRole.name, SlotRole.footer}


def _num(text: str) -> int | None:
    m = re.fullmatch(r"\s*0?(\d{1,3})[.)]?\s*", text or "")
    return int(m.group(1)) if m else None


@check("content.ordinal_sequence", C, "Номера шагов на слайде идут по порядку: без повторов, пропусков и перестановок",
       fix="renumber_ordinals")
def ordinal_sequence(ctx: AuditContext) -> list[Finding]:
    out = []
    for sv in ctx.slides:
        rows = []
        for s in sv.text_shapes():
            slot = sv.ts.slot(s.id)
            if slot is None or slot.role != SlotRole.ordinal:
                continue
            key = (slot.item if slot.item is not None else 10_000, _num(slot.demo_text) or 0, s.box.cy, s.box.cx)
            rows.append((key, s, _num(s.text)))
        if len(rows) < 2:
            continue
        rows.sort(key=lambda r: r[0])
        vals = [v for _, _, v in rows]
        start = vals[0] if vals[0] is not None else 1
        if vals != list(range(start, start + len(vals))):
            shown = " ".join(s.text.strip() for _, s, _ in rows)
            out.append(finding("content.ordinal_sequence", sv.index, f"Нумерация не по порядку: {shown}",
                               severity=Severity.error, values=[s.text.strip() for _, s, _ in rows]))
    return out


@check("content.text_quality", C, "Текст без механических ошибок: алфавиты, повторы, пробелы, пунктуация, скобки, "
       "оборванные фразы", fix="text_hygiene")
def text_quality(ctx: AuditContext) -> list[Finding]:
    lang = (ctx.pack.language if ctx.pack else None) or "ru"
    out = []
    for sv in ctx.slides:
        for s in sv.text_shapes():
            slot = sv.ts.slot(s.id)
            if slot is not None and slot.role in (SlotRole.ordinal, SlotRole.footer):
                continue
            sentence = slot is not None and slot.role not in _PLAIN
            if slot is not None and slot.demo_text.strip() == s.text.strip():
                continue                               # текст шаблона (декор, подпись дизайнера) — не наш
            found = issues(s.text, lang, sentence=sentence)
            if found:
                out.append(finding("content.text_quality", sv.index, "; ".join(found[:3]), shape=s,
                                   severity=Severity.warning, issues=found))
    return out
