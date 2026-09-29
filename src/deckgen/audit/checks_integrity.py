"""Целостность (Приложение 1) + сверка цифр с источниками (детерминированная часть вопроса 4)."""
from __future__ import annotations

import re
import zipfile
from difflib import SequenceMatcher

from ..content.grounding import source_numbers, ungrounded
from ..models import CheckCategory, Finding, Severity
from .base import SERVICE_KINDS, AuditContext, check, finding

C = CheckCategory.integrity
_PLACEHOLDER = re.compile(r"lorem ipsum|\bxxx+\b|\bх{3,}\b|\btodo\b|вставьте|вставить (текст|фото|qr)|\{\{|\}\}|\[f\d+\]|"
                          r"^(заголовок|подзаголовок|текст|основной текст|описание|пункт|имя фамилия|должность|название раздела)$",
                          re.I | re.M)


@check("integrity.file_opens", C, "Файл не открывается")
def file_opens(ctx: AuditContext) -> list[Finding]:
    if ctx.open_error:
        return [finding("integrity.file_opens", 0, f"Файл не открывается: {ctx.open_error}", severity=Severity.error)]
    try:
        with zipfile.ZipFile(ctx.pptx) as z:
            bad = z.testzip()
        if bad:
            return [finding("integrity.file_opens", 0, f"Повреждённая часть архива: {bad}", severity=Severity.error)]
    except zipfile.BadZipFile as e:
        return [finding("integrity.file_opens", 0, f"Не zip-архив: {e}", severity=Severity.error)]
    return []


@check("integrity.leftover_placeholder", C, "Остался текст-заглушка (lorem ipsum, XXX, TODO, «вставьте текст», демо-текст шаблона)",
       fix="leftover_placeholder")
def leftover_placeholder(ctx: AuditContext) -> list[Finding]:
    out = []
    for sv in ctx.slides:
        for s in sv.text_shapes():
            slot = sv.ts.slot(s.id)
            same_as_demo = slot is not None and slot.role.value not in ("footer", "ordinal") and slot.demo_text.strip() \
                and s.text.strip() == slot.demo_text.strip()
            m = _PLACEHOLDER.search(s.text.strip())
            if same_as_demo or m:
                out.append(finding("integrity.leftover_placeholder", sv.index,
                                   f"Заглушка «{(m.group(0) if m else s.text)[:40]}»", shape=s, severity=Severity.error))
    return out


@check("integrity.empty_slide", C, "Пустой слайд или слайд с одним заголовком", fix="drop_slide")
def empty_slide(ctx: AuditContext) -> list[Finding]:
    out = []
    for sv in ctx.slides:
        if sv.kind in SERVICE_KINDS:
            continue
        ps = ctx.plan.slides[sv.index] if sv.index < len(ctx.plan.slides) else None
        title_ids = {t.shape_id for t in sv.ts.texts if t.role.value == "title"}
        body = [s for s in sv.text_shapes() if s.id not in title_ids]
        visual = [s for s in sv.shapes if s.kind == "frame" or (s.kind == "pic" and sv.is_new(s))]
        chars = sum(len(s.text) for s in body)
        if not visual and chars < 25:
            out.append(finding("integrity.empty_slide", sv.index, "На слайде только заголовок" if chars else "Пустой слайд",
                               severity=Severity.error, title=ps.title if ps else ""))
    return out


@check("integrity.raster_slide", C, "Слайд оказался картинкой, а не редактируемыми объектами")
def raster_slide(ctx: AuditContext) -> list[Finding]:
    out = []
    for sv in ctx.slides:
        shapes = [s for s in sv.shapes if s.kind != "grp"]
        pics = [s for s in shapes if s.kind == "pic"]
        if shapes and len(pics) == len(shapes) and any(p.box.area > 0.8 for p in pics):
            out.append(finding("integrity.raster_slide", sv.index, "Слайд — одна картинка", severity=Severity.error))
    return out


@check("integrity.chart_labels", C, "У диаграммы нет подписей осей, единиц или легенды", fix="chart_labels")
def chart_labels(ctx: AuditContext) -> list[Finding]:
    out = []
    for i, ps in enumerate(ctx.plan.slides):
        ch = ps.visual.chart if ps.visual else None
        if ch is None:
            continue
        miss = []
        if ch.chart_type not in ("pie", "doughnut"):
            if not ch.x_title:
                miss.append("подпись оси X")
            if not ch.y_title:
                miss.append("ось Y / единицы")
        if (len(ch.series) > 1 or ch.chart_type in ("pie", "doughnut")) and not ch.legend:
            miss.append("легенда")
        if miss:
            out.append(finding("integrity.chart_labels", i, "Нет: " + ", ".join(miss), box=ps.visual.box))
    return out


@check("integrity.duplicate_slides", C, "Два слайда дублируют друг друга", fix="drop_slide")
def duplicate_slides(ctx: AuditContext) -> list[Finding]:
    texts = [(sv.index, " ".join(s.text for s in sv.text_shapes())) for sv in ctx.slides]
    out = []
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            a, b = texts[i][1], texts[j][1]
            if len(a) > 40 and len(b) > 40 and SequenceMatcher(None, a, b).quick_ratio() > 0.9 \
                    and SequenceMatcher(None, a, b).ratio() > 0.9:
                out.append(finding("integrity.duplicate_slides", texts[j][0], f"Повторяет слайд {texts[i][0] + 1}"))
    return out


@check("content.numbers_grounded", CheckCategory.content, "Цифры на слайде есть в исходных материалах (детерминированная сверка)",
       fix="strip_numbers")
def numbers_grounded(ctx: AuditContext) -> list[Finding]:
    if ctx.pack is None:
        return []
    src = source_numbers(ctx.pack)
    out = []
    for sv in ctx.slides:
        for s in sv.text_shapes():
            slot = sv.ts.slot(s.id)
            if slot is not None and slot.role.value in ("ordinal", "footer"):
                continue
            bad = ungrounded(s.text, src)
            if bad:
                out.append(finding("content.numbers_grounded", sv.index, f"Цифры {bad} не найдены в материалах", shape=s,
                                   severity=Severity.error, numbers=bad))
    return out
