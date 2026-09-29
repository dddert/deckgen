import { useEffect, useState } from "react";
import { api, Example, Health, Job, RunView, Template, TemplateSummary, waitJob } from "./api";
import { Results } from "./Results";

const PURPOSES: Record<string, string> = {
  product: "Продукт / запуск",
  feature: "Фича",
  project: "Проект",
  initiative: "Инициатива",
  report: "Отчёт",
};
const KIND_RU: Record<string, string> = {
  title: "титул", section: "разделитель", agenda: "содержание", text: "текст", two_columns: "две колонки", cards: "карточки",
  factoids: "цифры", process: "шаги", table: "таблица", chart: "график", quote: "цитата", image: "иллюстрация", team: "команда",
  cta: "призыв", qa: "вопросы", thanks: "финал", other: "прочее",
};

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [tpl, setTpl] = useState<Template | null>(null);
  const [summary, setSummary] = useState<TemplateSummary | null>(null);
  const [parseJob, setParseJob] = useState<Job | null>(null);
  const [examples, setExamples] = useState<Example[]>([]);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [audience, setAudience] = useState("");
  const [purpose, setPurpose] = useState("product");
  const [slides, setSlides] = useState(12);
  const [files, setFiles] = useState<File[]>([]);
  const [example, setExample] = useState("");
  const [runJob, setRunJob] = useState<Job | null>(null);
  const [run, setRun] = useState<RunView | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    api.templates().then(setTemplates).catch((e) => setError(String(e)));
    api.examples().then(setExamples).catch(() => undefined);
  }, []);

  const pick = async (t: Template) => {
    setTpl(t);
    setSummary(null);
    setError("");
    try {
      const { job_id } = await api.parse(t.id);
      setSummary(await waitJob<TemplateSummary>(job_id, setParseJob));
    } catch (e) {
      setError(String(e));
    }
  };

  const upload = async (f: File) => {
    try {
      const r = await api.uploadTemplate(f);
      const list = await api.templates();
      setTemplates(list);
      const t = list.find((x) => x.id === r.id);
      if (t) pick(t);
    } catch (e) {
      setError(String(e));
    }
  };

  const useExample = (id: string) => {
    const ex = examples.find((e) => e.id === id);
    setExample(id);
    if (ex) {
      setText(ex.text);
      setTitle(ex.title);
      setAudience(ex.audience);
      setPurpose(ex.purpose);
    }
  };

  const generate = async () => {
    if (!tpl) return;
    setError("");
    setRun(null);
    const fd = new FormData();
    fd.append("template_id", tpl.id);
    fd.append("text", text);
    fd.append("title", title);
    fd.append("audience", audience);
    fd.append("purpose", purpose);
    fd.append("slides", String(slides));
    if (example) fd.append("example", example);
    files.forEach((f) => fd.append("files", f));
    try {
      const { job_id } = await api.createRun(fd);
      const res = await waitJob<{ run_id: string }>(job_id, setRunJob);
      setRun(await api.run(res.run_id));
    } catch (e) {
      setError(String(e));
    }
  };

  const busy = runJob?.status === "running" || runJob?.status === "queued";

  return (
    <div className="page">
      <header>
        <div className="brand">
          <span className="logo">◆</span> Цифровой дизайнер презентаций <small>v2</small>
        </div>
        {health && (
          <div className="status">
            <span className={health.llm ? "dot ok" : "dot warn"} /> {health.llm ? `модель: ${health.llm_model}` : "без модели (эвристики)"}
            <span className={health.render !== "none" ? "dot ok" : "dot warn"} /> рендер: {health.render}
            {health.t2i && (
              <>
                <span className="dot ok" /> text-to-image
              </>
            )}
          </div>
        )}
      </header>

      {error && <div className="error">{error}</div>}

      <section className="card">
        <h2>1. Шаблон</h2>
        <div className="templates">
          {templates.map((t) => (
            <button key={t.id} className={tpl?.id === t.id ? "tpl active" : "tpl"} onClick={() => pick(t)}>
              <b>{t.name}</b>
              <small>
                {t.origin === "upload" ? "загружен" : "датасет"} · {t.size_mb} МБ
              </small>
            </button>
          ))}
          <label className="tpl upload">
            <b>+ Загрузить .pptx</b>
            <small>незнакомый шаблон</small>
            <input type="file" accept=".pptx" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          </label>
        </div>
        {tpl && !summary && parseJob && (
          <p className="muted">
            Разбор шаблона… {parseJob.stage} {parseJob.elapsed}s
          </p>
        )}
        {summary && (
          <div className="summary">
            <div>
              <div className="swatches">
                {summary.palette.slice(0, 10).map((c) => (
                  <span key={c.hex} title={`#${c.hex} · ${c.role}`} style={{ background: `#${c.hex}` }} />
                ))}
              </div>
              <p>
                <b>{summary.fonts.join(", ")}</b> · {summary.size_in[0]}×{summary.size_in[1]} in · образцов {summary.slides_usable}/
                {summary.slides_total}
              </p>
              <p className="kinds">
                {Object.entries(summary.kinds).map(([k, items]) => (
                  <span key={k} className="chip">
                    {KIND_RU[k] ?? k}
                    {items.length ? ` ${items.join("/")}` : ""}
                  </span>
                ))}
              </p>
              {summary.notes.map((n) => (
                <p key={n} className="muted small">
                  {n}
                </p>
              ))}
            </div>
            <div className="previews">
              {summary.slides
                .filter((s) => s.preview)
                .slice(0, 12)
                .map((s) => (
                  <figure key={s.index} className={s.usable ? "" : "unusable"} title={s.reason || s.name}>
                    <img src={api.preview(summary.template_id, s.index)} loading="lazy" />
                    <figcaption>
                      {s.index + 1}. {KIND_RU[s.kind] ?? s.kind}
                      {s.items ? ` ×${s.items}` : ""}
                    </figcaption>
                  </figure>
                ))}
            </div>
          </div>
        )}
      </section>

      <section className="card">
        <h2>2. Содержание</h2>
        <div className="grid2">
          <div>
            <label>
              Текст пользователя — о чём презентация, факты, цифры
              <textarea value={text} onChange={(e) => setText(e.target.value)} rows={12}
                placeholder="Например: Мы запускаем AI-ассистента... Пилот шёл 4 месяца у 3 клиентов... 68% участников используют ежедневно..." />
            </label>
            {examples.length > 0 && (
              <p className="muted small">
                Пример:{" "}
                {examples.map((e) => (
                  <button key={e.id} className="link" onClick={() => useExample(e.id)}>
                    {e.title}
                  </button>
                ))}
              </p>
            )}
          </div>
          <div className="form">
            <label>
              Название (необязательно)
              <input value={title} onChange={(e) => setTitle(e.target.value)} />
            </label>
            <label>
              Аудитория
              <input value={audience} onChange={(e) => setAudience(e.target.value)} placeholder="руководители, клиенты…" />
            </label>
            <label>
              Назначение
              <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
                {Object.entries(PURPOSES).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Слайдов: {slides}
              <input type="range" min={6} max={20} value={slides} onChange={(e) => setSlides(+e.target.value)} />
            </label>
            <label>
              Материалы (csv, xlsx, md, txt, docx, pdf, картинки)
              <input type="file" multiple accept=".csv,.xlsx,.md,.txt,.docx,.pdf,.png,.jpg,.jpeg"
                onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
            </label>
            {files.length > 0 && <p className="muted small">{files.map((f) => f.name).join(", ")}</p>}
            <button className="primary" disabled={!tpl || (!text.trim() && !example) || busy} onClick={generate}>
              {busy ? "Генерация…" : "Сгенерировать 3 варианта"}
            </button>
          </div>
        </div>
      </section>

      {runJob && !run && (
        <section className="card">
          <h2>3. Генерация</h2>
          <div className="progress">
            <div style={{ width: `${Math.round(runJob.progress * 100)}%` }} />
          </div>
          <p>
            {runJob.stage || "в очереди"} · {runJob.elapsed}s <span className="muted">(бюджет ТЗ — 300 с)</span>
          </p>
          <pre className="log">{runJob.log.slice(-12).join("\n")}</pre>
        </section>
      )}

      {run && <Results run={run} onUpdate={setRun} />}
    </div>
  );
}
