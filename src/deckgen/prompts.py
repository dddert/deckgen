"""Реестр агентов и скиллов: тексты — отдельными файлами (prompts/, skills/), версии — в registry.yaml,
закреплённые версии — в config.yaml. Скилл — переиспользуемый блок знаний (сторителлинг, лимиты плотности,
выбор графика), который подключается в промпт агента по имени@версии. В артефакты пишутся фактические версии.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

from .settings import Settings


class PromptRegistry:
    def __init__(self, settings: Settings):
        reg = Path(settings.prompts.registry)
        self.root = reg.parent
        self.skills_root = Path(settings.prompts.skills_dir)
        self.index = yaml.safe_load(reg.read_text(encoding="utf-8"))
        self.pinned = settings.prompts.model_dump(exclude={"registry", "skills_dir"})
        self.env = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=False)
        self._cache: dict[str, str] = {}

    def version(self, agent: str) -> str:
        return self.pinned[agent]

    def entry(self, agent: str) -> dict:
        return self.index["agents"][agent]["versions"][self.version(agent)]

    def skills_of(self, agent: str) -> list[str]:
        return list(self.entry(agent).get("skills", []))

    def skill_text(self, ref: str) -> str:
        """ref = 'storytelling/v1'"""
        if ref not in self._cache:
            name, ver = ref.split("/")
            meta = self.index["skills"][name]["versions"][ver]
            self._cache[ref] = (self.skills_root / meta["file"]).read_text(encoding="utf-8").strip()
        return self._cache[ref]

    def render(self, agent: str, **ctx) -> str:
        e = self.entry(agent)
        text = (self.root / e["file"]).read_text(encoding="utf-8")
        skills = "\n\n".join(self.skill_text(s) for s in self.skills_of(agent))
        return self.env.from_string(text).render(skills=skills, **ctx).strip()

    def versions(self) -> dict[str, str]:
        out = dict(self.pinned)
        for agent in self.pinned:
            try:
                for s in self.skills_of(agent):
                    out[f"skill:{s.split('/')[0]}"] = s.split("/")[1]
            except KeyError:
                continue
        return out

    def catalog(self) -> dict:
        return {"agents": {a: {"pinned": self.pinned.get(a), "description": v.get("description"),
                               "versions": {k: {"changelog": x.get("changelog"), "skills": x.get("skills", [])}
                                            for k, x in v["versions"].items()}}
                           for a, v in self.index["agents"].items()},
                "skills": {s: {"description": v.get("description"), "source": v.get("source"),
                               "versions": list(v["versions"])} for s, v in self.index.get("skills", {}).items()}}
