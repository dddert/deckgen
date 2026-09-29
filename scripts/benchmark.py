"""Замер времени по этапам на всех шаблонах датасета: python scripts/benchmark.py [content_pack]"""
import json
import sys
import time
from pathlib import Path

from deckgen.content.pack import load_pack
from deckgen.pipeline.orchestrator import Pipeline
from deckgen.settings import Settings

s = Settings.load()
pack = load_pack(sys.argv[1] if len(sys.argv) > 1 else "data/content_packs/example_product_launch")
rows = []
for t in sorted(Path(s.run.templates_dir).glob("*.pptx")):
    p = Pipeline(s, log=print)
    t0 = time.perf_counter()
    res = p.run(t, pack)
    rows.append({"template": t.name, "total_s": round(time.perf_counter() - t0, 1), "mode": res.mode,
                 **{k: round(v, 1) for k, v in res.timings.items()}, "llm": res.llm_stats.get("llm", {})})
print(json.dumps(rows, ensure_ascii=False, indent=2))
