"""Готовый .pptx -> один самодостаточный .html (текст — текстом, графики — inline SVG, картинки — base64).

Рендерится из итогового файла тем же ридером, что и аудит: фон слайда/макета/мастера, декор макета,
фигуры с заливками/градиентами/скруглениями, текст с кеглями и цветами шаблона, таблицы, графики.
Открывается офлайн в Chrome / Firefox / Safari / Яндекс Браузере; ← → листают, F — на весь экран.
"""
from __future__ import annotations

import base64
import html
import io
import math
from pathlib import Path

from ..models import ChartSpec, DeckPlan, DesignSystem
from ..ooxml.reader import PresentationReader, RShape

W_PX = 1280


class _Img:
    def __init__(self, reader: PresentationReader, max_px: int):
        self.reader, self.max_px, self.cache = reader, max_px, {}
        self.parts = {str(p.partname).lstrip("/"): p for p in reader.prs.part.package.iter_parts()}

    def uri(self, partname: str) -> str | None:
        if partname in self.cache:
            return self.cache[partname]
        part = self.parts.get(partname)
        if part is None:
            return None
        try:
            from PIL import Image
            with Image.open(io.BytesIO(part.blob)) as im:
                alpha = im.mode in ("RGBA", "LA", "P")
                im = im.convert("RGBA" if alpha else "RGB")
                if max(im.size) > self.max_px:
                    k = self.max_px / max(im.size)
                    im = im.resize((int(im.width * k), int(im.height * k)))
                buf = io.BytesIO()
                if alpha:
                    im.save(buf, "PNG", optimize=True)
                    mime = "image/png"
                else:
                    im.save(buf, "JPEG", quality=85)
                    mime = "image/jpeg"
            uri = f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"
        except Exception:  # noqa: BLE001 — EMF/SVG и пр.
            uri = None
        self.cache[partname] = uri
        return uri


def write_html(pptx: Path, plan: DeckPlan, design: DesignSystem, out: Path, max_img_px: int = 1600) -> Path:
    reader = PresentationReader(pptx)
    H_PX = int(W_PX * reader.slide_h_in / reader.slide_w_in)
    px_in = W_PX / reader.slide_w_in
    imgs = _Img(reader, max_img_px)
    fonts_css = _font_faces(design)
    fam = ",".join(f"'{f}'" for f in design.fonts[:2]) + ",'Segoe UI',Arial,sans-serif"
    parts = [f"<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
             f"<title>{html.escape(plan.slides[0].title if plan.slides else plan.deck_id)}</title><style>{fonts_css}"
             f"html,body{{margin:0;background:#1d1f23;font-family:{fam}}}"
             f".deck{{display:flex;flex-direction:column;align-items:center;gap:28px;padding:28px 0}}"
             f".slide{{position:relative;width:{W_PX}px;height:{H_PX}px;overflow:hidden;box-shadow:0 6px 30px #0008;"
             f"transform-origin:top center}}"
             f".el{{position:absolute;box-sizing:border-box}}.tx{{display:flex;flex-direction:column;overflow:visible}}"
             f".tx p{{margin:0;white-space:pre-wrap;word-wrap:break-word}}"
             f"table.t{{border-collapse:collapse;width:100%;height:100%}}table.t td{{padding:4px 8px;border-bottom:1px solid #8884}}"
             f".num{{position:fixed;right:14px;bottom:10px;color:#aaa;font:12px Arial}}</style></head><body><div class='deck'>"]
    for i, rs in enumerate(reader.slides()):
        bg = _bg_css(rs.background, imgs)
        parts.append(f"<section class='slide' id='s{i + 1}' style='{bg}'>")
        for s in reader.read_decor(i):
            parts.append(_shape(s, px_in, W_PX, H_PX, imgs, None))
        ps = plan.slides[i] if i < len(plan.slides) else None
        for s in rs.shapes:
            chart = ps.visual.chart if (ps and ps.visual and s.kind == "frame" and s.chart_part) else None
            parts.append(_shape(s, px_in, W_PX, H_PX, imgs, chart))
        parts.append("</section>")
    parts.append("</div><div class='num' id='num'></div><script>"
                 "const S=[...document.querySelectorAll('.slide')];let k=0;"
                 "function fit(){const w=Math.min(1,(innerWidth-40)/" + str(W_PX) + ");"
                 "S.forEach(s=>{s.style.transform=`scale(${w})`;s.style.marginBottom=`${(w-1)*" + str(H_PX) + "}px`})}"
                 "function go(n){k=Math.max(0,Math.min(S.length-1,n));S[k].scrollIntoView({behavior:'smooth',block:'center'});"
                 "document.getElementById('num').textContent=(k+1)+' / '+S.length}"
                 "addEventListener('keydown',e=>{if(e.key==='ArrowRight'||e.key==='PageDown'||e.key===' ')go(k+1);"
                 "if(e.key==='ArrowLeft'||e.key==='PageUp')go(k-1);"
                 "if(e.key==='f')document.documentElement.requestFullscreen&&document.documentElement.requestFullscreen()});"
                 "addEventListener('resize',fit);fit();go(0);</script></body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(p for p in parts if p), encoding="utf-8")
    return out


def _font_faces(design: DesignSystem) -> str:
    css = []
    for key, path in design.font_files.items():
        fam, _, w = key.partition("|")
        try:
            data = base64.b64encode(Path(path).read_bytes()).decode()
        except OSError:
            continue
        css.append(f"@font-face{{font-family:'{fam}';src:url(data:font/ttf;base64,{data});font-weight:{700 if w == 'b' else 400}}}")
    return "".join(css)


def _bg_css(bg, imgs: _Img) -> str:
    if bg.kind == "img" and bg.image_part and imgs.uri(bg.image_part):
        return f"background:url({imgs.uri(bg.image_part)}) center/cover"
    if bg.kind == "grad" and len(bg.grad) >= 2:
        return f"background:linear-gradient(135deg,{','.join('#' + c for c in bg.grad)})"
    return f"background:#{bg.color or 'FFFFFF'}"


def _shape(s: RShape, px_in: float, W: int, H: int, imgs: _Img, chart: ChartSpec | None) -> str:
    if s.kind == "grp" or not s.box.visible():
        return ""
    b = s.box
    pos = f"left:{b.x * W:.1f}px;top:{b.y * H:.1f}px;width:{b.w * W:.1f}px;height:{b.h * H:.1f}px;"
    if s.rot:
        pos += f"transform:rotate({s.rot:.1f}deg);"
    if s.kind == "pic" or (s.kind == "sp" and s.fill_kind == "img" and s.image_part and not s.text):
        uri = imgs.uri(s.image_part) if s.image_part else None
        if not uri:
            return ""
        l, t, r, bt = s.crop
        vw, vh = max(1e-3, 1 - l - r), max(1e-3, 1 - t - bt)
        bw, bh = b.w * W / vw, b.h * H / vh
        radius = "border-radius:50%;" if s.geometry == "ellipse" else ""
        return (f"<div class='el' style='{pos}{radius}background:url({uri}) no-repeat;"
                f"background-size:{bw:.1f}px {bh:.1f}px;background-position:{-l * bw:.1f}px {-t * bh:.1f}px'></div>")
    if s.kind == "frame" and s.table:
        font = s.table_font
        style = f"font-size:{(font.size if font else 12) * px_in / 72:.1f}px;color:#{(font.color if font else None) or '222222'}"
        rows = "".join("<tr>" + "".join(f"<td>{'<b>' if ri == 0 else ''}{html.escape(c)}{'</b>' if ri == 0 else ''}</td>"
                                        for c in r) + "</tr>" for ri, r in enumerate(s.table))
        return f"<div class='el' style='{pos}{style}'><table class='t'>{rows}</table></div>"
    if s.kind == "frame" and chart is not None:
        return f"<div class='el' style='{pos}'>{_svg(chart, int(b.w * W), int(b.h * H))}</div>"
    if s.kind not in ("sp", "cxn"):
        return ""
    css = pos
    if s.fill_kind == "solid" and s.fill:
        css += f"background:#{s.fill};"
    elif s.fill_kind == "grad" and len(s.grad) >= 2:
        css += f"background:linear-gradient(135deg,{','.join('#' + c for c in s.grad)});"
    if s.line:
        css += f"border:1px solid #{s.line};"
    if s.geometry == "ellipse":
        css += "border-radius:50%;"
    elif s.geometry in ("roundRect", "round2SameRect", "snipRoundRect"):
        css += f"border-radius:{min(b.w * W, b.h * H) * 0.1:.0f}px;"
    if s.kind == "cxn":
        return f"<div class='el' style='{pos}border-top:1px solid #{s.line or '888888'};height:0'></div>"
    if not s.text.strip():
        return f"<div class='el' style='{css}'></div>" if ("background" in css or "border" in css) else ""
    l, t, r, bt = s.insets
    css += f"padding:{t * H:.1f}px {r * W:.1f}px {bt * H:.1f}px {l * W:.1f}px;"
    css += {"ctr": "justify-content:center;", "b": "justify-content:flex-end;"}.get(s.anchor, "justify-content:flex-start;")
    paras = []
    for p in s.paras:
        align = {"ctr": "center", "r": "right", "just": "justify"}.get(p.align, "left")
        spans = []
        for run in p.runs:
            if run.text == "\n":
                spans.append("<br>")
                continue
            spans.append(f"<span style='font-size:{run.size * px_in / 72:.1f}px;color:#{run.color or '000000'};"
                         f"font-weight:{700 if run.bold else 400};{'font-style:italic;' if run.italic else ''}"
                         f"font-family:\"{html.escape(run.family)}\",inherit'>{html.escape(run.text)}</span>")
        lh = 1.2 * p.line_spacing
        bullet = "• " if p.bullet and p.text.strip() else ""
        paras.append(f"<p style='text-align:{align};line-height:{lh:.2f};padding-left:{p.level * 18}px'>{bullet}{''.join(spans)}</p>")
    return f"<div class='el tx' style='{css}'>{''.join(paras)}</div>"


def _svg(ch: ChartSpec, w: int, h: int) -> str:
    fg = "#888"
    if ch.chart_type in ("pie", "doughnut"):
        vals = ch.series[0].values if ch.series else []
        total = sum(vals) or 1
        cx, cy, r = w / 2, h / 2 - 14, min(w, h - 40) / 2 - 6
        a0, out = -math.pi / 2, []
        for i, v in enumerate(vals):
            a1 = a0 + 2 * math.pi * v / total
            large = 1 if a1 - a0 > math.pi else 0
            color = (ch.point_colors or [ch.series[0].color or "0077FF"])[i % max(1, len(ch.point_colors or [1]))]
            out.append(f"<path d='M{cx},{cy} L{cx + r * math.cos(a0):.1f},{cy + r * math.sin(a0):.1f} "
                       f"A{r},{r} 0 {large} 1 {cx + r * math.cos(a1):.1f},{cy + r * math.sin(a1):.1f} Z' fill='#{color}'/>")
            a0 = a1
        hole = f"<circle cx='{cx}' cy='{cy}' r='{r * 0.55:.1f}' fill='transparent' stroke='none'/>" if ch.chart_type == "doughnut" else ""
        legend = "".join(f"<text x='{10 + (i % 4) * (w / 4):.0f}' y='{h - 6 - 14 * (i // 4)}' font-size='11' fill='{fg}'>■ "
                         f"{html.escape(c)}</text>" for i, c in enumerate(ch.categories))
        mask = (f"<mask id='m{id(ch)}'><rect width='{w}' height='{h}' fill='white'/><circle cx='{cx}' cy='{cy}' r='{r * 0.55:.1f}' "
                f"fill='black'/></mask>") if ch.chart_type == "doughnut" else ""
        group = f"<g mask='url(#m{id(ch)})'>" if mask else "<g>"
        return f"<svg width='{w}' height='{h}'>{mask}{group}{''.join(out)}</g>{hole}{legend}</svg>"
    pad_l, pad_b, pad_t = 44, 36, 16
    vmax = max((v for s in ch.series for v in s.values), default=1) or 1
    n, m = max(1, len(ch.categories)), max(1, len(ch.series))
    gw = (w - pad_l - 10) / n
    ih = h - pad_b - pad_t
    out = [f"<svg width='{w}' height='{h}' font-size='11' fill='{fg}'>",
           f"<line x1='{pad_l}' y1='{h - pad_b}' x2='{w - 10}' y2='{h - pad_b}' stroke='{fg}'/>"]
    for k in range(1, 5):
        y = h - pad_b - ih * k / 4
        out.append(f"<line x1='{pad_l}' y1='{y:.1f}' x2='{w - 10}' y2='{y:.1f}' stroke='{fg}' stroke-opacity='.25'/>"
                   f"<text x='{pad_l - 6}' y='{y + 4:.1f}' text-anchor='end'>{vmax * k / 4:.0f}</text>")
    for si, s in enumerate(ch.series):
        color = "#" + (s.color or "0077FF")
        if ch.chart_type in ("line", "area"):
            pts = [(pad_l + gw * (i + 0.5), h - pad_b - v / vmax * ih) for i, v in enumerate(s.values)]
            out.append(f"<polyline points='{' '.join(f'{x:.1f},{y:.1f}' for x, y in pts)}' fill='none' stroke='{color}' stroke-width='2.5'/>")
            out += [f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3.5' fill='{color}'/><text x='{x:.1f}' y='{y - 8:.1f}' text-anchor='middle'>"
                    f"{v:g}</text>" for (x, y), v in zip(pts, s.values)]
        else:
            bw = gw * 0.7 / m
            for i, v in enumerate(s.values):
                bh = v / vmax * ih
                x = pad_l + gw * i + gw * 0.15 + bw * si
                out.append(f"<rect x='{x:.1f}' y='{h - pad_b - bh:.1f}' width='{bw:.1f}' height='{bh:.1f}' fill='{color}'/>"
                           f"<text x='{x + bw / 2:.1f}' y='{h - pad_b - bh - 4:.1f}' text-anchor='middle'>{v:g}</text>")
    for i, c in enumerate(ch.categories):
        out.append(f"<text x='{pad_l + gw * (i + 0.5):.1f}' y='{h - pad_b + 15}' text-anchor='middle'>{html.escape(str(c))[:18]}</text>")
    if ch.legend and len(ch.series) > 1:
        out += [f"<text x='{pad_l + i * 150}' y='{h - 4}' fill='#{s.color or '0077FF'}'>■ {html.escape(s.name)[:22]}</text>"
                for i, s in enumerate(ch.series)]
    out.append("</svg>")
    return "".join(out)
