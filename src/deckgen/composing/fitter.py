"""Подгонка текста под рамку слота.

1) меряем текст метриками гарнитуры шаблона (parsing/fonts.py);
2) не влезает — уменьшаем кегль, но только до значения из шкалы шаблона и не ниже min_scale
   (иначе аудит «кегль не из шкалы»);
3) всё ещё не влезает — отдаём на сокращение модели (shorten), крайний случай — обрезка по словам.
Для spAutoFit-рамок возвращается новая высота (PowerPoint пересчитает её только после правки — ставим сами).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Box, TextSlot
from ..parsing.fonts import TextMeasurer


@dataclass
class Fit:
    size_pt: float | None       # None — кегль образца
    fits: bool
    height_in: float
    fit_box: Box | None
    max_chars: int              # сколько влезает на минимальном допустимом кегле


def fit(paragraphs: list[str], slot: TextSlot, m: TextMeasurer, sizes: list[float], min_scale: float,
        W: float, H: float) -> Fit:
    box = slot.grow_box or slot.box
    w_in, h_in = box.w * W, box.h * H
    if slot.parts and len(slot.parts) > 1:
        need = sum(m.height_in(t, slot.family, p.size_pt, w_in, p.bold, slot.line_spacing)
                   for t, p in zip(paragraphs, slot.parts) if t)
        ok = need <= h_in * 1.04 and all(m.longest_word_fits(t, slot.family, p.size_pt, w_in, p.bold)
                                         for t, p in zip(paragraphs, slot.parts) if t)
        return Fit(None, ok, need, _grow(slot, need, H), slot.max_chars)
    text = "\n".join(paragraphs)
    base = slot.size_pt
    if slot.nowrap:                          # без переноса: строк = абзацев; ширина — свободное место вокруг рамки
        demo_w = max((m.width_in(x, slot.family, base, slot.bold) for x in slot.demo_text.split("\n") if x), default=w_in)
        if slot.wide_box is not None:        # карта занятости: до кольца диаграммы / соседней фигуры
            allow = max(slot.wide_box.w * W, demo_w)
        elif slot.limited:
            allow = w_in
        else:
            allow = max(w_in, demo_w) * 1.6
        for size in [base] + sorted({s for s in sizes if base * min_scale - 0.01 <= s < base}, reverse=True):
            widest = max((m.width_in(x, slot.family, size, slot.bold) for x in paragraphs if x), default=0)
            if widest <= allow:
                return Fit(None if size == base else size, True, len(paragraphs) * size * 1.2 / 72, None, slot.max_chars)
        return Fit(None, False, 0, None, max(3, int(len(text) * allow / max(widest, 1e-6))))
    cands = [base] + sorted({s for s in sizes if base * min_scale - 0.01 <= s < base}, reverse=True)
    boxes = [(box, False)] + ([(slot.wide_box, True)] if slot.wide_box is not None else [])
    last = None
    for size in cands:                       # сначала родной кегль в расширенной рамке, потом меньший кегль
        for b, wide in boxes:
            bw, bh = b.w * W, b.h * H
            need = m.height_in(text, slot.family, size, bw, slot.bold, slot.line_spacing)
            ok = need <= bh * 1.04 and m.longest_word_fits(text, slot.family, size, bw, slot.bold)
            fb = b if wide else _grow(slot, need, H)
            last = Fit(None if size == base else size, ok, need, fb, 0)
            if ok:
                break
        if last.fits:
            break
    big = boxes[-1][0]
    last.max_chars, _ = m.capacity_chars(slot.family, cands[-1], big.w * W, big.h * H, slot.bold, slot.line_spacing)
    return last


def _grow(slot: TextSlot, need_in: float, H: float) -> Box | None:
    if slot.autofit != "shape" or slot.grow_box is None:
        return None
    h = max(slot.box.h, min(slot.grow_box.h, need_in / H + 0.01))
    return slot.box.moved(h=h) if h > slot.box.h + 0.002 else None


# служебные слова, на которых фраза не может заканчиваться («Запуск продукта для» -> «Запуск продукта»)
_TAIL = {"и", "в", "во", "на", "с", "со", "о", "об", "по", "для", "к", "ко", "у", "из", "от", "до", "за", "а", "но",
         "или", "что", "как", "при", "без", "над", "под", "через", "это", "чтобы", "также", "не", "же", "ли", "их",
         "его", "её", "их", "наш", "наши", "свой", "свои", "the", "and", "of", "for", "to", "in", "on", "with", "a"}


def clean_tail(text: str) -> str:
    words = text.strip().rstrip(",;:—–-( ").split()
    while len(words) > 1 and words[-1].lower().strip(",;:—–-(«\"") in _TAIL:
        words.pop()
    return " ".join(words).rstrip(",;:—–-( ")


# окончания прилагательных/причастий в косвенных падежах: без существительного фраза оборвана
# («Руководители продуктовых», «Рост числа активных»)
_ADJ_TAIL = re.compile(r"[а-яё]{3,}(?:ых|их|ого|его|ому|ему|ыми|ими)$")
_BARE_NUM = re.compile(r"[\d.,]+")


def cut_tail(words: list[str]) -> list[str]:
    """Хвост после обрезки по словам: цифра без единицы («Пилот длился 4») и прилагательное без
    существительного — признаки оборванной фразы. Год («до конца 2026») — законченная мысль."""
    words = list(words)
    while len(words) > 1:
        w = words[-1].strip(",;:«»\"()").lower()
        if (_BARE_NUM.fullmatch(w) and not re.fullmatch(r"(?:19|20)\d\d", w)) or _ADJ_TAIL.fullmatch(w):
            words.pop()
            continue
        break
    return words


def truncate_words(text: str, limit: int) -> str:
    """Крайний случай: режем по границе предложения/фразы/слова. Многоточия нет: «…» на слайде читается как
    ошибка вёрстки, а оборванная фраза без служебного слова в конце — как законченная мысль."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]
    for sep in (". ", "; ", ": ", " — ", " – ", ", ", " (", " и ", " или "):
        k = cut.rfind(sep)
        if k > limit * 0.5:
            return _close(clean_tail(cut[:k]))
    words, out = text.split(), ""
    for w in words:
        if len(out) + len(w) + (1 if out else 0) > limit:
            break
        out = f"{out} {w}".strip()
    if not out:
        return words[0]                       # одно длинное слово — оставляем целиком (fitter уменьшит кегль)
    return _close(clean_tail(" ".join(cut_tail(clean_tail(out).split()))))


def _close(text: str) -> str:
    """Обрезка внутри скобок/кавычек: «Развёртывание (on-premise» → «Развёртывание»."""
    for op, cl in (("(", ")"), ("«", "»")):
        k = text.rfind(op)
        if k >= 0 and text.count(op) > text.count(cl):
            text = clean_tail(text[:k]) if k > 0 else text[1:]
    return text
