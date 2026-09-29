"""Сверка цифр с исходными материалами — детерминированно, без модели.

Используется трижды: writer отбрасывает выдуманные цифры в слотах-показателях, composer не пускает
их в текст, аудит (content.numbers_grounded) подсвечивает то, что всё-таки просочилось.
"""
from __future__ import annotations

import re

from ..models import ContentPack

_NUM = re.compile(r"(?<![\w.])\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_SMALL_OK = 12            # «3 клиента», «4 месяца», «шаг 2» — порядковые/счётные, словами тоже встречаются


def norm_number(s: str) -> str:
    v = re.sub(r"[   ]", "", s).replace(",", ".")
    if "." in v:
        v = v.rstrip("0").rstrip(".")
    return v or "0"


def numbers_in(text: str) -> list[str]:
    return [norm_number(m.group()) for m in _NUM.finditer(text or "")]


def source_numbers(pack: ContentPack) -> set[str]:
    out: set[str] = set()
    chunks = [pack.source_text, pack.brief, pack.title, *pack.must_include]
    chunks += [f.text + " " + str(f.value or "") for f in pack.facts]
    for t in pack.tables:
        chunks.append(" ".join(t.columns))
        chunks += [" ".join(str(c) for c in r) for r in t.rows]
    for c in chunks:
        out |= set(numbers_in(c))
    # производные, которые честно выводятся из данных: суммы/разности не считаем, но «68 %» == «68%»
    return out


def ungrounded(text: str, sources: set[str]) -> list[str]:
    bad = []
    for n in numbers_in(text):
        if n in sources:
            continue
        try:
            if float(n) <= _SMALL_OK and float(n).is_integer():
                continue
        except ValueError:
            pass
        bad.append(n)
    return bad


def strip_ungrounded(text: str, sources: set[str]) -> str:
    """Предложения с выдуманными цифрами удаляются целиком (цифру нельзя «чуть поправить»)."""
    parts = re.split(r"(?<=[.!?])\s+|\n", text)
    kept = [p for p in parts if p.strip() and not ungrounded(p, sources)]
    return "\n".join(kept) if "\n" in text else " ".join(kept)
