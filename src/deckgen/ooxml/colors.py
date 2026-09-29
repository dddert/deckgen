"""Цвета DrawingML: схема темы + clrMap мастера + модификаторы (lumMod/lumOff/tint/shade).

v1 брал только srgbClr и первую тему, поэтому 'scheme:tx1' / 'bg1' оставались неразрешёнными,
а контраст считался по значениям по умолчанию. Здесь любой цвет сводится к RRGGBB.
"""
from __future__ import annotations

import colorsys
from dataclasses import dataclass, field

from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_COLOR_TAGS = ("srgbClr", "schemeClr", "sysClr", "prstClr", "scrgbClr", "hslClr")
_PRESET = {"black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000", "blue": "0000FF",
           "yellow": "FFFF00", "gray": "808080", "grey": "808080", "darkGray": "A9A9A9", "lightGray": "D3D3D3"}
DEFAULT_CLRMAP = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2", **{f"accent{i}": f"accent{i}" for i in range(1, 7)},
                  "hlink": "hlink", "folHlink": "folHlink"}


@dataclass
class Theme:
    colors: dict[str, str] = field(default_factory=dict)   # dk1, lt1, accent1 ... -> RRGGBB
    major_font: str = "Arial"
    minor_font: str = "Arial"
    name: str = ""

    @classmethod
    def from_xml(cls, blob: bytes) -> "Theme":
        root = etree.fromstring(blob)
        th = cls(name=root.get("name") or "")
        cs = root.find(f".//{{{A}}}clrScheme")
        if cs is not None:
            for c in cs:
                key = etree.QName(c).localname
                if len(c):
                    v = c[0].get("val") if etree.QName(c[0]).localname == "srgbClr" else c[0].get("lastClr") or c[0].get("val")
                    if v and len(v) == 6:
                        th.colors[key] = v.upper()
        fs = root.find(f".//{{{A}}}fontScheme")
        if fs is not None:
            mj = fs.find(f"{{{A}}}majorFont/{{{A}}}latin")
            mn = fs.find(f"{{{A}}}minorFont/{{{A}}}latin")
            th.major_font = (mj.get("typeface") if mj is not None else None) or th.major_font
            th.minor_font = (mn.get("typeface") if mn is not None else None) or th.minor_font
        return th


@dataclass
class ColorContext:
    theme: Theme
    clrmap: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CLRMAP))

    def scheme(self, name: str) -> str | None:
        key = self.clrmap.get(name, name)
        return self.theme.colors.get(key) or self.theme.colors.get(name)

    def resolve(self, parent: etree._Element | None) -> str | None:
        """parent — элемент, внутри которого лежит цвет (solidFill, a:buClr, gs ...)."""
        if parent is None:
            return None
        el = None
        for child in parent:
            if etree.QName(child).localname in _COLOR_TAGS:
                el = child
                break
        if el is None:
            return None
        tag = etree.QName(el).localname
        base: str | None
        if tag == "srgbClr":
            base = (el.get("val") or "").upper() or None
        elif tag == "schemeClr":
            base = self.scheme(el.get("val") or "")
        elif tag == "sysClr":
            base = (el.get("lastClr") or ("000000" if el.get("val") == "windowText" else "FFFFFF")).upper()
        elif tag == "prstClr":
            base = _PRESET.get(el.get("val") or "")
        elif tag == "scrgbClr":
            base = rgb_to_hex(*(min(255, int(int(el.get(k, "0")) / 100000 * 255)) for k in ("r", "g", "b")))
        else:
            base = None
        if not base or len(base) != 6:
            return None
        return apply_mods(base, el)


def apply_mods(hexv: str, el: etree._Element) -> str:
    r, g, b = hex_to_rgb(hexv)
    for m in el:
        name = etree.QName(m).localname
        val = int(m.get("val", "0")) / 100000
        if name in ("lumMod", "lumOff", "satMod"):
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            if name == "lumMod":
                l *= val
            elif name == "lumOff":
                l += val
            else:
                s *= val
            r, g, b = (int(round(c * 255)) for c in colorsys.hls_to_rgb(h, min(1, max(0, l)), min(1, max(0, s))))
        elif name == "tint":        # к белому
            r, g, b = (int(round(c + (255 - c) * (1 - val))) for c in (r, g, b))
        elif name == "shade":       # к чёрному
            r, g, b = (int(round(c * val)) for c in (r, g, b))
    return rgb_to_hex(r, g, b)


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"{max(0, min(255, r)):02X}{max(0, min(255, g)):02X}{max(0, min(255, b)):02X}"


def luminance(h: str) -> float:
    def ch(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = hex_to_rgb(h)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def is_dark(h: str) -> bool:
    return luminance(h) < 0.18


# ---------------------------------------------------------------------------- APCA (воспринимаемый контраст)
# WCAG 2 считает чёрный на фирменном синем #0077FF «хорошим» (5.1:1), а белый — «плохим» (4.1:1), хотя глазом
# всё наоборот. APCA (кандидат в WCAG 3, формула APCA-W3 0.0.98G-4g) учитывает полярность и насыщенные цвета:
# чёрный на #0077FF — Lc 37 (нечитаемо), белый — Lc −73 (хорошо). Пороги по документации APCA:
# Lc 90 — основной текст, 75 — минимум для текста колонками, 60 — прочий содержательный текст,
# 45 — крупный текст/заголовки (≥ 24 pt или ≥ 18 pt bold), 30 — абсолютный минимум.

def _apca_y(h: str) -> float:
    r, g, b = hex_to_rgb(h)
    return 0.2126729 * (r / 255) ** 2.4 + 0.7151522 * (g / 255) ** 2.4 + 0.0721750 * (b / 255) ** 2.4


def apca_lc(text: str, bg: str) -> float:
    """Контраст APCA Lc текста к фону: > 0 — тёмный на светлом, < 0 — светлый на тёмном; модуль ~0..106."""
    ty, by = _apca_y(text), _apca_y(bg)
    if ty <= 0.022:
        ty += (0.022 - ty) ** 1.414
    if by <= 0.022:
        by += (0.022 - by) ** 1.414
    if abs(by - ty) < 0.0005:
        return 0.0
    if by > ty:
        sapc = (by ** 0.56 - ty ** 0.57) * 1.14
        return 0.0 if sapc < 0.1 else (sapc - 0.027) * 100
    sapc = (by ** 0.65 - ty ** 0.62) * 1.14
    return 0.0 if sapc > -0.1 else (sapc + 0.027) * 100


def apca_min(size_pt: float, bold: bool = False) -> float:
    """Минимальный |Lc| для текста данного кегля на слайде (проектор/экран — берём уровни «содержательного» текста)."""
    if size_pt >= 24 or (bold and size_pt >= 18):
        return 45.0
    if size_pt >= 14:
        return 60.0
    return 70.0


def best_text_color(bg: str, candidates: list[str]) -> str:
    """Самый читаемый цвет текста для фона из кандидатов (цвета шаблона + белый/почти чёрный)."""
    return max(candidates, key=lambda c: abs(apca_lc(c, bg)))


def readable_tint(color: str, bg: str, min_lc: float, max_t: float = 0.6) -> str | None:
    """Тот же оттенок, но читаемый: акцент шаблона осветляется на тёмном фоне (затемняется на светлом)
    ровно настолько, чтобы |Lc| ≥ min_lc. None — если для этого пришлось бы потерять цвет."""
    target = "FFFFFF" if apca_lc(color, bg) <= 0 and _apca_y(bg) < 0.36 else "000000"
    for i in range(1, int(max_t * 20) + 1):
        c = mix(color, target, i / 20)
        if abs(apca_lc(c, bg)) >= min_lc:
            return c
    return None


def mix(a: str, b: str, t: float) -> str:
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    return rgb_to_hex(int(ra + (rb - ra) * t), int(ga + (gb - ga) * t), int(ba + (bb - ba) * t))


def distance(a: str, b: str) -> float:
    """Перцептивно взвешенное RGB-расстояние (0..~765)."""
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    rm = (ra + rb) / 2
    return ((2 + rm / 256) * (ra - rb) ** 2 + 4 * (ga - gb) ** 2 + (2 + (255 - rm) / 256) * (ba - bb) ** 2) ** 0.5
