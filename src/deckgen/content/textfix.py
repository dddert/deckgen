"""Детерминированная вычитка текста слайда — без модели.

Ловит то, что модель и обрезка по лимиту оставляют в тексте: латинские буквы внутри русских слов
(«прoект» с латинской «o»), иероглифы, повтор слова («в в»), пробел перед запятой, двойную пунктуацию,
незакрытую скобку после обрезки, дефис вместо тире, прямые кавычки, «3.5 ч» вместо «3,5 ч»,
предлог или запятую в конце фразы, строчную первую букву, жёсткие переносы строк из брифа.

Цифры не меняются по значению: numbers_in() до и после вычитки совпадают (запятая и точка
в дроби нормализуются одинаково) — сверка с источниками не ломается.

Используется писателем (clean), сокращением, фиксами rewrite/text_hygiene и аудитом content.text_quality.
"""
from __future__ import annotations

import re
import unicodedata

# латиница, неотличимая от кириллицы на слайде, и обратно
_LAT2CYR = dict(zip("AaBCcEeHKkMOoPpTXxYy", "АаВСсЕеНКкМОоРрТХхУу"))
_CYR2LAT = {v: k for k, v in _LAT2CYR.items()}
_CYR = re.compile(r"[а-яёА-ЯЁ]")
_LAT = re.compile(r"[a-zA-Z]")
_CJK = re.compile(r"[⺀-⿿぀-ヿ㄀-ㄯ㐀-䶿一-鿿가-힯豈-﫿]+")
_CJK_PUNCT = {"，": ", ", "。": ". ", "：": ": ", "；": "; ", "！": "! ", "？": "? ", "（": " (", "）": ") ",
              "、": ", ", "「": "«", "」": "»"}
_INVISIBLE = re.compile(r"[­​-‏⁠﻿]")
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+")
# служебные слова, которыми фраза не может закончиться (местоимения, «это», «что» — могут)
_DANGLING = {"и", "в", "во", "на", "с", "со", "о", "об", "обо", "по", "для", "к", "ко", "у", "из", "от", "до", "за",
             "а", "но", "или", "либо", "при", "без", "над", "под", "через", "чтобы", "между", "про", "также",
             "the", "and", "of", "for", "to", "in", "on", "with", "a", "an", "or"}
_UNITS = r"(?:%|‰|п\.\s?п\.|ч\b|час|мин|сек|с\b|млн|млрд|тыс|трлн|раз|₽|руб|\$|€|x\b|×|дн|мес|недел|лет|год|балл|pp\b)"


def is_ru(text: str) -> bool:
    return len(_CYR.findall(text or "")) >= len(_LAT.findall(text or ""))


def fix_homoglyphs(text: str) -> str:
    """«прoект» → «проект», «Cкорость» → «Скорость», «Micrоsoft» → «Microsoft».
    Части слова через дефис — отдельно: «AI-ассистент» смешанным не считается."""
    def one(m: re.Match) -> str:
        w = m.group()
        c, lat = len(_CYR.findall(w)), len(_LAT.findall(w))
        if not c or not lat:
            return w
        if c >= lat and all(ch in _LAT2CYR for ch in w if _LAT.match(ch)):
            return "".join(_LAT2CYR.get(ch, ch) for ch in w)
        if lat > c and all(ch in _CYR2LAT for ch in w if _CYR.match(ch)):
            return "".join(_CYR2LAT.get(ch, ch) for ch in w)
        return w
    return _WORD.sub(one, text)


def _dedupe_words(text: str) -> str:
    """«в в», «данные данные» → одно слово (регистр не важен, цифры не трогаем)."""
    return re.sub(r"\b([A-Za-zА-Яа-яЁё]+)(\s+\1\b)+", r"\1", text, flags=re.I)


def _is_marker_close(text: str, i: int) -> bool:
    return bool(re.match(r"^\s*\w{1,2}$", text[:i].split("\n")[-1]))     # «1)», «а)» — маркер списка


def _balance(text: str) -> str:
    """Незакрытая скобка/кавычка — хвост после обрезки («… (on-premise») или лишний символ."""
    for op, cl in (("(", ")"), ("«", "»"), ("[", "]")):
        opens: list[int] = []
        bad_close: list[int] = []
        for i, ch in enumerate(text):
            if ch == op:
                opens.append(i)
            elif ch == cl:
                if opens:
                    opens.pop()
                elif not (cl == ")" and _is_marker_close(text, i)):
                    bad_close.append(i)
        for i in reversed(bad_close):
            text = text[:i] + text[i + 1:]
        for i in reversed(opens):
            tail = text[i + 1:]
            if len(tail) <= 60 and not re.search(r"[.!?]\s", tail):
                text = text[:i].rstrip()      # оборванная скобка в конце — убираем вместе с хвостом
            else:
                text = text[:i] + text[i + 1:]
    return text


def _quotes(text: str, ru: bool) -> str:
    if not ru:
        return text
    text = re.sub(r'"([^"\n]+)"', r"«\1»", text)
    text = re.sub(r"„([^“\n]+)“", r"«\1»", text)
    return text.replace('"', "")


def _punct(text: str, ru: bool) -> str:
    text = re.sub(r"[ \t]+([,.;:!?%»)])", r"\1", text)            # пробел перед знаком
    text = re.sub(r"([«(])[ \t]+", r"\1", text)
    text = re.sub(r",{2,}", ",", text)
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)                 # «..» → «.», «...» не трогаем
    text = re.sub(r"([!?])\1+", r"\1", text)
    text = re.sub(r"[,;:]+([.!?])", r"\1", text)                    # «,.» → «.»
    text = re.sub(r"\.,", ".", text)
    text = re.sub(r"(?<=[а-яё]),(?=[А-Яа-яЁё])", ", ", text)        # «слово,слово»
    text = re.sub(r"(?<=[а-яё]{2})([.!?;])(?=[А-ЯЁ])", r"\1 ", text)
    if ru:
        text = re.sub(r"(?<=\S)[ \t]+(?:-{1,2}|–)[ \t]+(?=\S)", " — ", text)   # дефис/короткое тире между словами
        text = re.sub(r"(?<![\w.,])(\d+)\.(\d{1,3})(?![\d.])(?=\s?" + _UNITS + ")", r"\1,\2", text)   # 3.5 ч → 3,5 ч
    return text


def _cjk(text: str) -> str:
    for a, b in _CJK_PUNCT.items():
        text = text.replace(a, b)
    return _CJK.sub(" ", text)


def trim_dangling(text: str) -> str:
    """Предлог/союз/запятая в конце фразы — след обрезки («Результаты пилота в»)."""
    t = text.rstrip()
    while True:
        t2 = re.sub(r"[\s,;:—–\-]+$", "", t)
        m = re.search(r"(?:^|\s)([A-Za-zА-Яа-яЁё]+)$", t2)
        if m and m.group(1).lower() in _DANGLING and len(t2.split()) > 1 \
                and not (m.group(1).lower() == "с" and re.search(r"\d\s*с$", t2)):   # «5 с» — секунды
            t2 = t2[: m.start()].rstrip()
        if t2 == t:
            return t
        t = t2


def capitalize_first(text: str) -> str:
    """Первая буква — прописная, если первое слово не «iPhone», «eNPS», не код («v2») и не после цифры."""
    m = re.search(r"[A-Za-zА-Яа-яЁё][\w-]*", text)
    if not m or text[: m.start()].strip() not in ("", "«", "(", "—"):
        return text                    # «5 минут», «3,5 ч» — не предложение
    w = m.group()
    if not w[0].islower() or any(ch.isupper() for ch in w[1:]) or any(ch.isdigit() for ch in w):
        return text
    return text[: m.start()] + w[0].upper() + text[m.start() + 1:]


def sanitize(text: str, lang: str = "ru", sentence: bool = True) -> str:
    """Полная вычитка одного текста. sentence=False — для цифр и меток: без заглавной буквы и обрезки хвоста."""
    if not text:
        return text
    t = _INVISIBLE.sub("", unicodedata.normalize("NFC", str(text)))
    ru = lang.startswith("ru") and is_ru(t)
    if ru:
        t = _cjk(t)
    t = fix_homoglyphs(t)
    lines = []
    for line in t.split("\n"):
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        line = _dedupe_words(line)
        line = _quotes(line, ru)
        line = _punct(line, ru)
        line = _balance(line)
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        if sentence and line:
            line = capitalize_first(trim_dangling(line))
        lines.append(line)
    return "\n".join(lines).strip("\n")


def issues(text: str, lang: str = "ru", sentence: bool = True) -> list[str]:
    """Что не так с текстом (для аудита). Пусто — чисто."""
    if not text or not text.strip():
        return []
    out = []
    ru = lang.startswith("ru") and is_ru(text)
    mixed = next((w for w in _WORD.findall(text) if _CYR.search(w) and _LAT.search(w)), None)
    if mixed:
        out.append(f"смешение алфавитов: «{mixed}»")
    if ru and _CJK.search(text):
        out.append("иероглифы в русском тексте")
    if _INVISIBLE.search(text):
        out.append("невидимые символы")
    m = re.search(r"\b([A-Za-zА-Яа-яЁё]+)\s+\1\b", text, re.I)
    if m:
        out.append(f"повтор слова: «{m.group()}»")
    if re.search(r"[ \t]{2,}", text.strip()):
        out.append("двойной пробел")
    if re.search(r"[ \t][,.;:!?]", text):
        out.append("пробел перед знаком препинания")
    if re.search(r",,|(?<!\.)\.\.(?!\.)|[,;:][.!?]", text):
        out.append("двойная пунктуация")
    opens = text.count("(")
    closes = sum(1 for i, ch in enumerate(text) if ch == ")" and not _is_marker_close(text, i))
    if opens != closes:
        out.append("незакрытая скобка")
    if text.count("«") != text.count("»"):
        out.append("незакрытая кавычка")
    if sentence:
        for line in text.split("\n"):
            line = line.strip()
            if line and trim_dangling(line) != line:
                out.append(f"оборванная фраза: «…{line[-25:]}»")
                break
            if line and capitalize_first(line) != line:
                out.append(f"строчная буква в начале: «{line[:25]}…»")
                break
    return out


def unwrap(text: str) -> str:
    """Склеивает жёсткие переносы строк брифа: строка без точки в конце + строка со строчной буквы
    (или предыдущая кончается запятой/тире) — это одна фраза, а не два абзаца."""
    if not text:
        return text
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if out and s and out[-1].strip():
            prev = out[-1].rstrip()
            bullet = re.match(r"^([-•*·–—]|\d+[.)])\s", s)
            if not bullet and not re.search(r"[.!?:;…]$", prev) and (s[0].islower() or prev.endswith((",", "—", "-", "("))):
                out[-1] = prev + " " + s
                continue
        out.append(line)
    return "\n".join(out)
