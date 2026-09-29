"""Токены дизайн-системы: палитра с ролями, гарнитуры, типографическая шкала, безопасная область.

Всё считается по разрешённым стилям (ридер уже подставил наследуемые кегли/цвета/шрифты),
поэтому «тема врёт» (Arial в теме, Play в тексте) больше не проблема.
"""
from __future__ import annotations

from collections import Counter

from ..models import Box, ColorRole, ColorToken, DesignSystem, SlotRole, TemplateSlide, TypeRole
from ..ooxml.colors import distance, hex_to_rgb, is_dark, luminance
from ..ooxml.reader import PresentationReader, RSlide


def font_usage(rslides: list[RSlide]) -> Counter:
    c: Counter = Counter()
    for sl in rslides:
        for s in sl.shapes:
            for r in s.runs:
                if r.text.strip():
                    c[r.family] += len(r.text.strip())
    return c


def extract_design(reader: PresentationReader, rslides: list[RSlide], tslides: list[TemplateSlide],
                   template_id: str, source_file: str, font_files: dict[str, str]) -> DesignSystem:
    usable = [t for t in tslides if t.usable] or tslides
    ok = {t.index for t in usable}
    used_slides = [rs for rs in rslides if rs.index in ok]
    fams = [f for f, n in font_usage(used_slides).most_common() if n >= 3]   # шрифт слайда с кодом — не гарнитура шаблона
    on_light, on_dark = _text_colors(used_slides)
    return DesignSystem(
        template_id=template_id, source_file=source_file,
        slide_w_in=reader.slide_w_in, slide_h_in=reader.slide_h_in,
        palette=_palette(reader, rslides),
        fonts=fams or ["Arial"],
        font_sizes=_font_sizes(rslides),
        type_scale=_type_scale(usable, fams),
        safe_area=_safe_area(usable),
        dark_share=sum(1 for t in usable if t.dark) / max(1, len(usable)),
        embedded_fonts=[str(p.partname).rsplit("/", 1)[-1] for p in reader.prs.part.package.iter_parts()
                        if str(p.partname).startswith("/ppt/fonts/")],
        font_files=font_files,
        text_on_light=on_light, text_on_dark=on_dark,
        notes=_notes(reader, rslides),
    )


def _text_colors(rslides: list[RSlide]) -> tuple[str, str]:
    light: Counter = Counter()
    dark: Counter = Counter()
    for sl in rslides:
        bg_dark = bool(sl.background.color) and is_dark(sl.background.color)
        for s in sl.shapes:
            under_dark = is_dark(s.fill) if s.fill and s.fill_kind in ("solid", "grad") else bg_dark
            for r in s.runs:
                if not r.color or not r.text.strip():
                    continue
                if under_dark and luminance(r.color) > 0.5:
                    dark[r.color] += len(r.text)
                elif not under_dark and luminance(r.color) < 0.2:
                    light[r.color] += len(r.text)
    return (light.most_common(1)[0][0] if light else "000000", dark.most_common(1)[0][0] if dark else "FFFFFF")


def _palette(reader: PresentationReader, rslides: list[RSlide]) -> list[ColorToken]:
    use: Counter = Counter()
    bg: Counter = Counter()
    text: Counter = Counter()
    fills: Counter = Counter()
    for sl in rslides:
        if sl.background.color:
            bg[sl.background.color] += 1
            use[sl.background.color] += 40
        for s in sl.shapes:
            if s.fill and s.fill_kind in ("solid", "grad"):
                fills[s.fill] += 1
                use[s.fill] += 1 + int(s.box.area * 60)
            for c in s.grad:
                use[c] += 2
            if s.line:
                use[s.line] += 1
            for r in s.runs:
                if r.color and r.text.strip():
                    text[r.color] += len(r.text)
                    use[r.color] += 1 + len(r.text) // 15
    theme_names: dict[str, str] = {}
    for m in reader.prs.slide_masters:
        for k, v in reader.theme(m).colors.items():
            if k in ("dk1", "lt1", "dk2", "lt2") or k.startswith("accent"):
                theme_names.setdefault(v, k)
    tokens: dict[str, ColorToken] = {}
    for hexv, n in use.most_common():
        if n < 2 and hexv not in theme_names:
            continue
        tokens[hexv] = ColorToken(name=theme_names.get(hexv, f"custom_{hexv}"), hex=hexv, role=ColorRole.neutral, usage=n)
    for hexv, name in theme_names.items():
        tokens.setdefault(hexv, ColorToken(name=name, hex=hexv, role=ColorRole.neutral, usage=0))

    main_bg = bg.most_common(1)[0][0] if bg else "FFFFFF"
    for hexv in bg:
        if _chroma(hexv) < 60 or hexv == main_bg:     # насыщенный фон обложки — это фирменный цвет, а не «фон»
            tokens[hexv].role = ColorRole.background
    for hexv, _ in text.most_common(3):
        if tokens.get(hexv) and tokens[hexv].role != ColorRole.background and _chroma(hexv) < 60:
            tokens[hexv].role = ColorRole.text
    saturated = sorted((t for t in tokens.values() if _chroma(t.hex) >= 60 and t.role != ColorRole.background),
                       key=lambda t: -(t.usage + text.get(t.hex, 0) // 4))
    for i, t in enumerate(saturated):
        t.role = ColorRole.primary if i == 0 else ColorRole.accent
    for t in tokens.values():
        if t.role == ColorRole.neutral and t.hex in fills and distance(t.hex, main_bg) < 90:
            t.role = ColorRole.surface
    if not any(t.role == ColorRole.text for t in tokens.values()):
        dark_txt = "FFFFFF" if is_dark(main_bg) else "000000"
        tokens.setdefault(dark_txt, ColorToken(name="text", hex=dark_txt, role=ColorRole.text)).role = ColorRole.text
    return sorted(tokens.values(), key=lambda t: -t.usage)


def _chroma(h: str) -> int:
    r, g, b = hex_to_rgb(h)
    return max(r, g, b) - min(r, g, b)


def _font_sizes(rslides: list[RSlide]) -> list[float]:
    c: Counter = Counter()
    for sl in rslides:
        for s in sl.shapes:
            for r in s.runs:
                if r.text.strip():
                    c[r.size] += len(r.text.strip())
    return sorted(sz for sz, n in c.items() if n >= 3)


def _type_scale(slides: list[TemplateSlide], fams: list[str]) -> list[TypeRole]:
    by_role: dict[str, Counter] = {}
    fam_role: dict[str, Counter] = {}
    for t in slides:
        for s in t.texts:
            name = {SlotRole.title: "title", SlotRole.subtitle: "subtitle", SlotRole.heading: "heading",
                    SlotRole.body: "body", SlotRole.bullets: "body", SlotRole.number: "number",
                    SlotRole.caption: "caption", SlotRole.label: "caption"}.get(s.role)
            if not name:
                continue
            by_role.setdefault(name, Counter())[(s.size_pt, s.bold)] += max(1, len(s.demo_text))
            fam_role.setdefault(name, Counter())[s.family] += 1
    out = []
    default_fam = fams[0] if fams else "Arial"
    for name in ("title", "subtitle", "heading", "body", "caption", "number"):
        c = by_role.get(name)
        if not c:
            continue
        (size, bold), n = c.most_common(1)[0]
        fam = fam_role[name].most_common(1)[0][0] if name in fam_role else default_fam
        out.append(TypeRole(name=name, family=fam, size_pt=size, bold=bold, usage=n))
    return out


def _safe_area(slides: list[TemplateSlide]) -> Box:
    xs, ys, x2s, y2s = [], [], [], []
    for t in slides:
        for s in t.texts:
            if s.role in (SlotRole.footer,):
                continue
            xs.append(s.box.x)
            ys.append(s.box.y)
            x2s.append(s.box.x2)
            y2s.append(s.box.y2)
    if not xs:
        return Box(x=0.05, y=0.06, w=0.9, h=0.88)

    def pct(v, p):
        v = sorted(v)
        return v[min(len(v) - 1, max(0, int(len(v) * p)))]
    x, y = max(0.0, pct(xs, 0.05)), max(0.0, pct(ys, 0.05))
    x2, y2 = min(1.0, pct(x2s, 0.95)), min(1.0, pct(y2s, 0.95))
    return Box(x=x, y=y, w=max(0.3, x2 - x), h=max(0.3, y2 - y))


def _notes(reader: PresentationReader, rslides: list[RSlide]) -> list[str]:
    notes = []
    if any(s.name.startswith("Google Shape") for sl in rslides for s in sl.shapes):
        notes.append("Экспорт из Google Slides: дизайн живёт в слайдах-образцах, а не в макетах.")
    masters = list(reader.prs.slide_masters)
    if len(masters) > 1:
        notes.append(f"Мастеров: {len(masters)}; цвета и шрифты разрешаются по теме каждого.")
    dark = sum(1 for sl in rslides if sl.background.color and luminance(sl.background.color) < 0.18)
    if dark:
        notes.append(f"Тёмных слайдов-образцов: {dark} из {len(rslides)} — цвета графиков подбираются под фон.")
    return notes
