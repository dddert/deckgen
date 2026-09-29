import { useMemo, useState } from "react";
import { api, Finding, RunView, waitJob } from "./api";

const SEV = { error: "ошибка", warning: "предупр.", info: "инфо" } as const;
const CAT: Record<string, string> = {
  layout: "Вёрстка", template: "Шаблон", density: "Плотность", integrity: "Целостность", content: "Содержание",
};

export function Results({ run, onUpdate }: { run: RunView; onUpdate: (r: RunView) => void }) {
  const names = Object.keys(run.variants);
  const [variant, setVariant] = useState(names[0]);
  const [slide, setSlide] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [fixing, setFixing] = useState("");
  const v = run.variants[variant];
  const findings = v.audit.findings;
  const onSlide = useMemo(() => findings.filter((f) => f.slide_index === slide), [findings, slide]);
  const cnt = (sev: Finding["severity"], list = findings) => list.filter((f) => f.severity === sev).length;

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const applyFixes = async () => {
    setFixing("Исправление…");
    try {
      const { job_id } = await api.fix(run.run_id, variant, Array.from(selected));
      await waitJob(job_id, (j) => setFixing(`${j.stage} ${j.elapsed}s`));
      onUpdate(await api.run(run.run_id));
      setSelected(new Set());
    } catch (e) {
      alert(String(e));
    }
    setFixing("");
  };

  const png = v.slides[slide]?.png;

  return (
    <section className="card">
      <h2>
        3. Результат: «{run.title}» <small className="muted">за {run.timings.total?.toFixed(0)} с · режим {run.mode}</small>
      </h2>
      {run.notes.map((n) => (
        <p key={n} className="warnline">
          {n}
        </p>
      ))}

      <div className="variants">
        {names.map((n) => {
          const x = run.variants[n];
          return (
            <button key={n} className={n === variant ? "variant active" : "variant"} onClick={() => { setVariant(n); setSlide(0); setSelected(new Set()); }}>
              <b>{x.label}</b>
              <small>
                {x.slides.length} слайдов · {cnt("error", x.audit.findings)} ош. · {cnt("warning", x.audit.findings)} пред. · {x.timings.total?.toFixed(0)} с
              </small>
              <span className="thumbs">
                {x.slides.slice(0, 6).map((s) => (s.png ? <img key={s.index} src={api.file(run.run_id, s.png)} loading="lazy" /> : null))}
              </span>
            </button>
          );
        })}
      </div>

      <div className="downloads">
        {Object.entries(v.files).map(([k, p]) => (
          <a key={k} className="btn" href={api.file(run.run_id, p)} target={k === "html" ? "_blank" : undefined} download={k !== "html"}>
            ⬇ {k.toUpperCase()}
          </a>
        ))}
        <span className="muted small">
          Аудит: детерминированных {findings.filter((f) => f.deterministic).length} · контекстуальных (VLM) {findings.filter((f) => !f.deterministic).length} ·
          исправлено автоматически {v.audit.fixed.length} · VLM: {v.audit.semantic_status}
        </span>
      </div>

      <div className="audit">
        <div className="strip">
          {v.slides.map((s, i) => {
            const f = findings.filter((x) => x.slide_index === i);
            return (
              <button key={i} className={i === slide ? "sthumb active" : "sthumb"} onClick={() => setSlide(i)} title={s.title}>
                {s.png ? <img src={api.file(run.run_id, s.png)} loading="lazy" /> : <span>{i + 1}</span>}
                {cnt("error", f) > 0 && <em className="badge err">{cnt("error", f)}</em>}
                {cnt("error", f) === 0 && cnt("warning", f) > 0 && <em className="badge warn">{cnt("warning", f)}</em>}
              </button>
            );
          })}
        </div>
        <div className="viewer">
          <div className="stage">
            {png ? <img src={api.file(run.run_id, png)} /> : <div className="nopng">нет рендера (LibreOffice не найден)</div>}
            {onSlide
              .filter((f) => f.box)
              .map((f) => (
                <div key={f.id} className={`box ${f.severity} ${selected.has(f.id) ? "sel" : ""}`} title={f.message}
                  style={{ left: `${f.box!.x * 100}%`, top: `${f.box!.y * 100}%`, width: `${f.box!.w * 100}%`, height: `${f.box!.h * 100}%` }} />
              ))}
          </div>
          <p className="muted small">
            Слайд {slide + 1} / {v.slides.length}: {v.slides[slide]?.title} · образец шаблона №{v.slides[slide]?.template_slide} · {v.slides[slide]?.kind}
          </p>
        </div>
        <div className="findings">
          <h3>Замечания слайда ({onSlide.length})</h3>
          {onSlide.length === 0 && <p className="muted">Нет замечаний</p>}
          {onSlide.map((f) => (
            <label key={f.id} className={`finding ${f.severity}`}>
              <input type="checkbox" disabled={!f.fix_id} checked={selected.has(f.id)} onChange={() => toggle(f.id)} />
              <span>
                <b>{CAT[f.category] ?? f.category}</b> · {SEV[f.severity]} {f.deterministic ? "" : "· 🤖 VLM"}
                <br />
                {f.message}
                <br />
                <code>{f.check_id}</code> {f.fix_id ? <em>исправление: {f.fix_id}</em> : <em>только показать</em>}
              </span>
            </label>
          ))}
          <div className="fixbar">
            <button className="link" onClick={() => setSelected(new Set(findings.filter((f) => f.fix_id).map((f) => f.id)))}>
              выбрать все исправимые ({findings.filter((f) => f.fix_id).length})
            </button>
            <button className="primary" disabled={!selected.size || !!fixing} onClick={applyFixes}>
              {fixing || `Исправить выбранное (${selected.size})`}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
