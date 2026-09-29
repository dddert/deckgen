"""Измерение текста для подгонки под рамки.

Встроенные в .pptx шрифты датасета (Play) упакованы в EOT с MTX-сжатием — PIL их не читает.
Поэтому ищем TTF гарнитуры в системе / assets/fonts (scripts/fetch_fonts.py кладёт туда
Google Fonts, найденные в шаблоне), иначе меряем «прокси»-шрифтом с запасом ширины.
Для шрифтов из шаблона, которых нет на сервере, аудит и fitter используют один и тот же
измеритель — оценки согласованы.
"""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path

_SEARCH_DIRS = [
    Path(__file__).resolve().parents[3] / "assets" / "fonts",
    Path.home() / ".fonts", Path.home() / ".local/share/fonts", Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
    Path.home() / "Library/Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
]
_PROXIES = ["Arial", "LiberationSans", "Liberation Sans", "DejaVuSans", "Helvetica", "NotoSans", "Roboto"]
PROXY_WIDTH_PAD = 1.06      # запас, когда меряем не родным шрифтом


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@functools.lru_cache(maxsize=1)
def _font_index() -> dict[str, list[Path]]:
    idx: dict[str, list[Path]] = {}
    for d in _SEARCH_DIRS:
        if not d.exists():
            continue
        for root, _, files in os.walk(d):
            for f in files:
                if f.lower().endswith((".ttf", ".otf")):
                    p = Path(root) / f
                    idx.setdefault(_norm(p.stem), []).append(p)
    return idx


def find_font_file(family: str, bold: bool = False) -> Path | None:
    idx = _font_index()
    fam = _norm(family)
    if not fam:
        return None
    want = [fam + "bold", fam + "semibold", fam + "medium", fam] if bold else [fam + "regular", fam, fam + "book", fam + "medium"]
    for w in want:
        if w in idx:
            return idx[w][0]
    cands = [(k, v) for k, v in idx.items() if k.startswith(fam)]
    if cands:
        cands.sort(key=lambda kv: (("bold" in kv[0]) != bold, len(kv[0])))
        return cands[0][1][0]
    return None


class TextMeasurer:
    """width/lines/height текста в дюймах. Кэширует шрифты и ширины слов."""

    def __init__(self, font_files: dict[str, str] | None = None):
        self.font_files = font_files or {}
        self._fonts: dict[tuple[str, bool], tuple[object, bool]] = {}
        self._w: dict[tuple[str, bool, str], float] = {}

    def _font(self, family: str, bold: bool):
        key = (family, bold)
        if key not in self._fonts:
            from PIL import ImageFont
            path = self.font_files.get(f"{family}|{'b' if bold else 'r'}") or self.font_files.get(family)
            p = Path(path) if path else find_font_file(family, bold)
            native = p is not None
            if p is None:
                p = next((f for f in (find_font_file(x, bold) for x in _PROXIES) if f), None)
            font = None
            if p is not None:
                try:
                    font = ImageFont.truetype(str(p), 100)
                except OSError:
                    font = None
            self._fonts[key] = (font, native and font is not None)
        return self._fonts[key]

    def is_native(self, family: str, bold: bool = False) -> bool:
        return self._font(family, bold)[1]

    def em_width(self, text: str, family: str, bold: bool) -> float:
        """Ширина текста в em (1 em = кегль)."""
        k = (family, bold, text)
        if k in self._w:
            return self._w[k]
        font, native = self._font(family, bold)
        if font is not None:
            w = font.getlength(text) / 100
            if not native:
                w *= PROXY_WIDTH_PAD
        else:
            w = sum(_char_em(c) for c in text) * PROXY_WIDTH_PAD
        if bold and not native:
            w *= 1.05
        self._w[k] = w
        return w

    def width_in(self, text: str, family: str, size_pt: float, bold: bool = False) -> float:
        return self.em_width(text, family, bold) * size_pt / 72

    def lines(self, text: str, family: str, size_pt: float, box_w_in: float, bold: bool = False) -> int:
        n = 0
        space = self.width_in(" ", family, size_pt, bold)
        for para in text.split("\n"):
            words = para.split()
            if not words:
                n += 1
                continue
            cur, count = 0.0, 1
            for w in words:
                ww = self.width_in(w, family, size_pt, bold)
                if cur == 0:
                    cur = ww
                elif cur + space + ww <= box_w_in:
                    cur += space + ww
                else:
                    count += 1
                    cur = ww
                while cur > box_w_in * 1.0001 and box_w_in > 0:   # слово длиннее строки — рвётся
                    count += 1
                    cur -= box_w_in
            n += count
        return max(1, n)

    def longest_word_fits(self, text: str, family: str, size_pt: float, box_w_in: float, bold: bool = False) -> bool:
        words = text.split()
        if not words:
            return True
        return max(self.width_in(w, family, size_pt, bold) for w in words) <= box_w_in * 1.0001

    def height_in(self, text: str, family: str, size_pt: float, box_w_in: float, bold: bool = False,
                  line_spacing: float = 1.0) -> float:
        n = self.lines(text, family, size_pt, box_w_in, bold)
        return n * size_pt * 1.2 * line_spacing / 72

    def capacity_chars(self, family: str, size_pt: float, box_w_in: float, box_h_in: float, bold: bool = False,
                       line_spacing: float = 1.0) -> tuple[int, int]:
        """(знаков, строк) — сколько обычного русского текста влезает в рамку."""
        line_h = size_pt * 1.2 * max(0.8, line_spacing) / 72
        lines = max(1, int((box_h_in + 0.02) / line_h))
        sample = "Проектная команда сократила сроки и повысила качество"
        per_char = self.width_in(sample, family, size_pt, bold) / len(sample)
        per_line = max(3, int(box_w_in / per_char * 0.92))       # 8% — потери на переносах по словам
        return per_line * lines, lines


def _char_em(c: str) -> float:
    if c == " ":
        return 0.28
    if c in ".,:;!|'":
        return 0.28
    if c.isdigit():
        return 0.56
    if c.isupper():
        return 0.68 if c not in "ШЩЖМФЮЫ" else 0.85
    if c in "шщжмфюы":
        return 0.72
    return 0.53
