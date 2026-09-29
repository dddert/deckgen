"""Контракты между слоями пайплайна (parsing -> planning -> composing -> render -> audit).

Координаты — доли слайда (0..1): в датасете два размера слайда (10×5.625 и 13.33×7.5 in),
в EMU переводим только при записи файла. Слои обмениваются только этими моделями.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Геометрия
# ---------------------------------------------------------------------------

class Box(BaseModel):
    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def inter(self, o: "Box") -> float:
        ix = max(0.0, min(self.x2, o.x2) - max(self.x, o.x))
        iy = max(0.0, min(self.y2, o.y2) - max(self.y, o.y))
        return ix * iy

    def contains_point(self, x: float, y: float, pad: float = 0.0) -> bool:
        return self.x - pad <= x <= self.x2 + pad and self.y - pad <= y <= self.y2 + pad

    def contains(self, o: "Box", pad: float = 0.003) -> bool:
        return self.x - pad <= o.x and self.y - pad <= o.y and o.x2 <= self.x2 + pad and o.y2 <= self.y2 + pad

    def visible(self) -> bool:
        return self.x2 > 0 and self.y2 > 0 and self.x < 1 and self.y < 1 and self.w > 0 and self.h > 0

    def moved(self, x: float | None = None, y: float | None = None,
              w: float | None = None, h: float | None = None) -> "Box":
        return Box(x=self.x if x is None else x, y=self.y if y is None else y,
                   w=self.w if w is None else w, h=self.h if h is None else h)


# ---------------------------------------------------------------------------
# Дизайн-система (parsing/design.py)
# ---------------------------------------------------------------------------

class ColorRole(str, Enum):
    background = "background"
    surface = "surface"
    text = "text"
    primary = "primary"
    accent = "accent"
    neutral = "neutral"


class ColorToken(BaseModel):
    name: str                      # accent1 / dk1 / custom_0077FF
    hex: str                       # 'RRGGBB'
    role: ColorRole
    usage: int = 0


class TypeRole(BaseModel):
    name: str                      # title / subtitle / heading / body / caption / number
    family: str
    size_pt: float
    bold: bool = False
    usage: int = 0


class DesignSystem(BaseModel):
    template_id: str
    source_file: str
    slide_w_in: float
    slide_h_in: float
    palette: list[ColorToken]
    fonts: list[str]               # по убыванию использования; первые две — разрешённые гарнитуры
    font_sizes: list[float]        # все кегли, которыми набран текст шаблона
    type_scale: list[TypeRole]
    safe_area: Box                 # поля: где живёт контент на слайдах-образцах
    dark_share: float = 0.0        # доля тёмных слайдов (для подбора цветов графиков)
    text_on_light: str = "000000"  # цвет текста шаблона на светлом фоне
    text_on_dark: str = "FFFFFF"   # ... и на тёмном (подписи графиков, таблиц)
    embedded_fonts: list[str] = []
    font_files: dict[str, str] = {}  # гарнитура -> путь к TTF для точного измерения текста
    notes: list[str] = []

    def role(self, name: str) -> TypeRole | None:
        return next((r for r in self.type_scale if r.name == name), None)


# ---------------------------------------------------------------------------
# Разбор слайдов-образцов (parsing/analyzer.py)
# ---------------------------------------------------------------------------

class SlideKind(str, Enum):
    title = "title"                # титул
    section = "section"            # разделитель
    agenda = "agenda"              # содержание
    text = "text"                  # заголовок + текст/список
    two_columns = "two_columns"
    cards = "cards"                # N карточек / преимуществ
    factoids = "factoids"          # крупные цифры
    process = "process"            # шаги / таймлайн / этапы
    table = "table"
    chart = "chart"
    quote = "quote"
    image = "image"                # иллюстрация / скриншот + текст
    team = "team"                  # спикер / команда
    cta = "cta"                    # призыв к действию / контакты / QR
    qa = "qa"
    thanks = "thanks"
    other = "other"


class SlotRole(str, Enum):
    title = "title"                # заголовок слайда
    subtitle = "subtitle"
    heading = "heading"            # заголовок карточки
    body = "body"                  # абзац
    bullets = "bullets"            # список
    number = "number"              # крупная цифра / показатель
    ordinal = "ordinal"            # порядковый номер «01», «1» — заполняется без модели
    label = "label"                # короткая подпись, дата, тег
    name = "name"                  # имя спикера
    caption = "caption"            # должность / пояснение мелким кеглем
    footer = "footer"              # колонтитул / номер слайда — не трогаем


class SlotPart(BaseModel):
    """Часть составного слота: «ХХ% + подпись» или «Заголовок + текст» в одной фигуре."""
    role: SlotRole
    size_pt: float
    bold: bool = False
    max_chars: int = 20
    demo: str = ""
    color: str | None = None       # цвет части (синий заголовок + серый текст в одной фигуре)
    lines: int = 1                 # сколько строк образца (через a:br) занимает часть — ручной перенос одной фразы


class TextSlot(BaseModel):
    shape_id: str
    role: SlotRole
    box: Box                       # рабочая рамка текста (внутренние поля, ограничения декором/контентом)
    text_box: Box | None = None    # исходная рамка текста образца (для пересчёта в рамку фигуры)
    grow_box: Box | None = None    # куда текст может «прорасти» (spAutoFit) — до низа карточки
    wide_box: Box | None = None    # заголовок в узкой рамке образца: до куда её можно расширить по свободному месту
    item: int | None = None        # номер элемента повторяющейся группы (0..n-1)
    demo_text: str                 # текст-заглушка образца (детектор «забытой заглушки»)
    size_pt: float
    family: str
    bold: bool = False
    color: str | None = None
    line_spacing: float = 1.0      # множитель интерлиньяжа
    paragraphs: int = 1            # абзацев в образце
    max_chars: int = 0             # оценка ёмкости по метрикам шрифта
    max_lines: int = 1
    autofit: Literal["none", "shape", "norm"] = "none"
    nowrap: bool = False           # wrap="none": строка не переносится (крупные цифры) — меряем только высоту
    bg_color: str | None = None    # фон под текстом (карта занятости: фон слайда, декор, пиксели картинок)
    limited: bool = False          # рамку сузила графика шаблона — ширина жёсткая (цифра в кольце диаграммы)
    parts: list[SlotPart] = []     # непусто — составной слот: писатель возвращает список частей
    container: bool = False        # у фигуры видимая заливка/обводка: пустой текст очищаем, фигуру не удаляем


class PictureRole(str, Enum):
    decor = "decor"                # графика шаблона — оставляем
    background = "background"
    logo = "logo"
    icon = "icon"
    photo = "photo"                # место под фото/скриншот контента
    chart_example = "chart_example"  # картинка-пример графика: место под нативный график


class PictureSlot(BaseModel):
    shape_id: str
    role: PictureRole
    box: Box
    item: int | None = None
    media: str | None = None       # partname картинки
    also: list[str] = []           # фигуры-спутники (рамка вокруг «Вставить фото») — удаляются/заменяются вместе


class ItemGroup(BaseModel):
    index: int
    box: Box
    shape_ids: list[str]           # все фигуры элемента: подложка, иконка, тексты
    roots: list[str] = []          # верхние фигуры элемента (двигаются при перераспределении)
    row: int = 0
    col: int = 0


class VisualArea(BaseModel):
    """Место под нативный график/таблицу на слайде-образце."""
    box: Box
    replace_ids: list[str] = []    # что удалить (картинка-пример, заглушка «Иллюстрация»)
    table_shape_id: str | None = None   # нативная таблица образца (переиспользуем стиль)
    source: Literal["chart", "table", "picture", "placeholder", "body", "free"] = "free"


class TemplateSlide(BaseModel):
    index: int
    layout_part: str
    layout_name: str = ""
    kind: SlideKind
    name: str = ""                 # «3 карточки с иконками»
    confidence: float = 0.5
    dark: bool = False
    bg_color: str = "FFFFFF"
    texts: list[TextSlot] = []
    items: list[ItemGroup] = []
    pictures: list[PictureSlot] = []
    visual: VisualArea | None = None
    all_shape_ids: list[str] = []
    shape_boxes: dict[str, Box] = {}   # эталонная геометрия (аудит: что внёс генератор)
    text_capacity: int = 0         # суммарная ёмкость текстовых слотов, знаков
    density: Literal["low", "medium", "high"] = "medium"
    usable: bool = True
    layout_photos: bool = False    # демо-фото зашиты в макет (не заменить на слайде) — такой образец избегаем
    reason: str = ""               # почему не используется / чем уточнён
    preview_png: str | None = None

    @property
    def n_items(self) -> int:
        return len(self.items)

    def slot(self, shape_id: str) -> TextSlot | None:
        return next((t for t in self.texts if t.shape_id == shape_id), None)


class TemplateModel(BaseModel):
    design: DesignSystem
    slides: list[TemplateSlide]
    parser_version: str = ""

    def usable(self) -> list[TemplateSlide]:
        return [s for s in self.slides if s.usable]


# ---------------------------------------------------------------------------
# Контент-пакет (content/)
# ---------------------------------------------------------------------------

class DeckPurpose(str, Enum):
    feature = "feature"
    product = "product"
    project = "project"
    initiative = "initiative"
    report = "report"


class Fact(BaseModel):
    id: str
    text: str
    value: str | float | None = None
    unit: str | None = None
    source: str | None = None


class DataTable(BaseModel):
    id: str
    title: str
    columns: list[str]
    rows: list[list[str | float | None]]
    source: str | None = None


class Asset(BaseModel):
    id: str
    kind: Literal["photo", "icon", "logo", "screenshot"] = "photo"
    path: str
    tags: list[str] = []


class ContentPack(BaseModel):
    id: str
    title: str
    purpose: DeckPurpose = DeckPurpose.project
    audience: str = ""
    brief: str
    language: str = "ru"
    target_slides: int | None = None
    tone: str | None = None
    must_include: list[str] = []
    facts: list[Fact] = []
    tables: list[DataTable] = []
    assets: list[Asset] = []
    source_text: str = ""          # всё, что прислал пользователь: основа для сверки цифр


# ---------------------------------------------------------------------------
# Каркас колоды (planning/skeleton.py)
# ---------------------------------------------------------------------------

class VizSpec(BaseModel):
    kind: Literal["chart", "table"] = "chart"
    data_ref: str
    chart_type: Literal["column", "bar", "line", "area", "pie", "doughnut"] | None = None
    series: list[str] = []         # какие колонки показать (пусто — все подходящие)
    caption: str | None = None


class OutlineSlide(BaseModel):
    index: int = 0
    kind: SlideKind
    title: str                     # заголовок-вывод
    message: str = ""              # мысль слайда одним предложением
    n_items: int = 0               # сколько карточек/цифр/шагов нужно
    points: list[str] = []         # тезисы (черновик, из них writer делает тексты)
    fact_refs: list[str] = []
    viz: VizSpec | None = None
    image: str | None = None       # о чём иллюстрация (для t2i / подбора картинки)
    section: str | None = None


class Outline(BaseModel):
    title: str
    slides: list[OutlineSlide]
    language: str = "ru"
    prompt_versions: dict[str, str] = {}
    source: Literal["llm", "offline"] = "llm"


# ---------------------------------------------------------------------------
# План колоды (composing) — что сделать с каждым клоном слайда-образца
# ---------------------------------------------------------------------------

class TextFill(BaseModel):
    shape_id: str
    paragraphs: list[str]          # абзацы; внутри абзаца '\n' -> перенос строки (a:br)
    size_pt: float | None = None   # кегль, если fitter уменьшил (всегда из шкалы шаблона)
    role: SlotRole = SlotRole.body
    fit_box: Box | None = None     # новая рамка фигуры (расширенный заголовок, выросший по тексту блок)
    nowrap: bool = False           # не переносить строку (крупная цифра шире своей рамки, как у дизайнера)
    text_area: Box | None = None   # плашка остаётся на месте, текст — в свободной части (внутренние поля bodyPr)


class ImageFill(BaseModel):
    shape_id: str
    path: str
    source: Literal["pack", "t2i", "template"] = "pack"


class ChartSeries(BaseModel):
    name: str
    values: list[float]
    color: str | None = None


class ChartSpec(BaseModel):
    chart_type: Literal["column", "bar", "line", "area", "pie", "doughnut"]
    categories: list[str]
    series: list[ChartSeries]
    x_title: str | None = None
    y_title: str | None = None
    legend: bool = True
    data_labels: bool = True
    number_format: str = "General"
    point_colors: list[str] = []     # цвета секторов pie/doughnut


class TableSpec(BaseModel):
    columns: list[str]
    rows: list[list[str]]


class VisualFill(BaseModel):
    kind: Literal["chart", "table"]
    box: Box
    remove_ids: list[str] = []
    table_shape_id: str | None = None   # заполнить нативную таблицу образца
    chart: ChartSpec | None = None
    table: TableSpec | None = None
    data_ref: str | None = None
    font_family: str | None = None
    font_size: float | None = None
    text_color: str | None = None      # под тёмный/светлый фон слайда
    grid_color: str | None = None
    header_fill: str | None = None


class StyleOp(BaseModel):
    """Точечная правка стиля по замечанию аудита (применяется поверх клона)."""
    shape_id: str
    op: Literal["recolor", "font", "size"]
    value: str | float
    match: str | None = None       # recolor: какой цвет менять (None — весь текст; "run:k" — k-я часть текста)


class PlannedSlide(BaseModel):
    index: int
    outline_index: int | None = None
    template_slide: int
    layout_part: str
    kind: SlideKind
    title: str = ""
    texts: list[TextFill] = []
    delete_ids: list[str] = []
    moves: dict[str, Box] = {}
    images: list[ImageFill] = []
    visual: VisualFill | None = None
    style_ops: list[StyleOp] = []
    notes: str | None = None


class DeckPlan(BaseModel):
    deck_id: str
    variant: str
    template_id: str
    template_file: str
    content_pack_id: str
    slides: list[PlannedSlide]
    prompt_versions: dict[str, str] = {}
    timings: dict[str, float] = {}
    llm_mode: Literal["llm", "offline", "mixed"] = "llm"


# ---------------------------------------------------------------------------
# Аудит
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    error = "error"
    warning = "warning"
    info = "info"


class CheckCategory(str, Enum):
    layout = "layout"
    template = "template"
    density = "density"
    integrity = "integrity"
    content = "content"


class Finding(BaseModel):
    id: str = ""                   # стабильный ключ для UI: slide:check:shape
    check_id: str
    category: CheckCategory
    deterministic: bool
    severity: Severity
    slide_index: int
    shape_id: str | None = None
    message: str
    box: Box | None = None
    fix_id: str | None = None      # None — только показать
    evidence: dict = Field(default_factory=dict)


class AuditReport(BaseModel):
    deck_id: str
    variant: str
    findings: list[Finding]
    checks_run: list[str]
    prompt_versions: dict[str, str] = {}
    duration_seconds: float = 0.0
    fixed: list[str] = []          # id замечаний, исправленных автоматически
    semantic_status: str = ""      # ok / почему VLM-проверки не выполнялись

    def count(self, severity: Severity | None = None) -> int:
        return sum(1 for f in self.findings if severity is None or f.severity == severity)
