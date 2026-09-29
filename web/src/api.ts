// Тонкий клиент к FastAPI (src/deckgen/api/app.py). Типы — зеркала pydantic-моделей из src/deckgen/models.py.

export type Box = { x: number; y: number; w: number; h: number };

export type Finding = {
  id: string;
  check_id: string;
  category: "layout" | "template" | "density" | "integrity" | "content";
  deterministic: boolean;
  severity: "error" | "warning" | "info";
  slide_index: number;
  shape_id?: string | null;
  message: string;
  box?: Box | null;
  fix_id?: string | null;
};

export type AuditReport = {
  findings: Finding[];
  checks_run: string[];
  fixed: string[];
  semantic_status: string;
  duration_seconds: number;
};

export type Template = { id: string; name: string; origin: "dataset" | "upload"; size_mb: number };

export type TemplateSummary = {
  template_id: string;
  file: string;
  size_in: [number, number];
  fonts: string[];
  palette: { hex: string; role: string }[];
  type_scale: { name: string; family: string; size_pt: number }[];
  slides_total: number;
  slides_usable: number;
  kinds: Record<string, number[]>;
  notes: string[];
  slides: { index: number; kind: string; name: string; usable: boolean; items: number; visual: string | null; reason: string; preview: boolean }[];
};

export type Job<T = unknown> = {
  id: string;
  status: "queued" | "running" | "done" | "error";
  stage: string;
  progress: number;
  result: T | null;
  error: string | null;
  log: string[];
  elapsed: number;
};

export type VariantView = {
  label: string;
  timings: Record<string, number>;
  files: Record<string, string>;
  slides: { index: number; title: string; kind: string; template_slide: number; png: string | null }[];
  audit: AuditReport;
  llm_mode: string;
};

export type RunView = {
  run_id: string;
  title: string;
  mode: string;
  notes: string[];
  timings: Record<string, number>;
  llm_stats: Record<string, { calls: number; prompt_tokens: number; completion_tokens: number; errors: number }>;
  variants: Record<string, VariantView>;
};

export type Health = { ok: boolean; version: string; llm: boolean; vlm: boolean; llm_model: string; render: string; t2i: boolean };
export type Example = { id: string; title: string; audience: string; purpose: string; text: string; tables: string[] };

async function j<T>(p: Promise<Response>): Promise<T> {
  const r = await p;
  if (!r.ok) {
    let msg = await r.text();
    try {
      msg = JSON.parse(msg).detail ?? msg;
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  return r.json();
}

const post = (url: string, body?: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });

export const api = {
  health: () => j<Health>(fetch("api/health")),
  templates: () => j<Template[]>(fetch("api/templates")),
  uploadTemplate: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return j<{ id: string; name: string }>(fetch("api/templates", { method: "POST", body: fd }));
  },
  parse: (id: string) => j<{ job_id: string }>(post(`api/templates/${id}/parse`)),
  preview: (id: string, index: number) => `api/templates/${id}/preview/${index}`,
  examples: () => j<Example[]>(fetch("api/examples")),
  createRun: (fd: FormData) => j<{ job_id: string }>(fetch("api/runs", { method: "POST", body: fd })),
  run: (id: string) => j<RunView>(fetch(`api/runs/${id}`)),
  fix: (run: string, variant: string, ids: string[]) => j<{ job_id: string }>(post(`api/runs/${run}/${variant}/fix`, { ids })),
  job: <T,>(id: string) => j<Job<T>>(fetch(`api/jobs/${id}`)),
  file: (run: string, path: string) => `api/files/${run}/${path}`,
  catalog: () => j<{ id: string; category: string; deterministic: boolean; description: string; fix: string | null }[]>(fetch("api/audit/catalog")),
};

export async function waitJob<T>(id: string, onTick: (j: Job<T>) => void, ms = 1200): Promise<T> {
  for (;;) {
    const job = await api.job<T>(id);
    onTick(job);
    if (job.status === "done") return job.result as T;
    if (job.status === "error") throw new Error(job.error ?? "ошибка задачи");
    await new Promise((r) => setTimeout(r, ms));
  }
}
