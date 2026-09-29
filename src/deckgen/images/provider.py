"""Картинки для слотов-фото: ассеты контент-пакета -> генерация text-to-image (⭐) -> ничего (слот убирается).

Модель: FLUX.2 [klein] 4B (Apache 2.0, 4 шага). На одной RTX A6000 рядом с vLLM (gpu-util 0.72) работает с
cpu-offload; перед загрузкой проверяется свободная VRAM — если её нет, генерация пропускается, колода
собирается из графики шаблона и пакета (устойчивость важнее картинки).
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from ..llm.client import ChatMessage, LLMClient, LLMError
from ..models import Asset, ColorRole, ContentPack, DesignSystem, OutlineSlide
from ..prompts import PromptRegistry
from ..settings import T2ISettings

_LOCK = threading.Lock()
_PIPE = None


class ImageProvider:
    def __init__(self, pack: ContentPack, cfg: T2ISettings, cache_dir: Path, design: DesignSystem,
                 llm: LLMClient | None, prompts: PromptRegistry | None, seed: int = 42, log=lambda m: None):
        self.pack, self.cfg, self.cache, self.design = pack, cfg, cache_dir, design
        self.llm, self.prompts, self.seed, self.log = llm, prompts, seed, log
        self.used: set[str] = set()
        self.generated = 0
        self._t2i_ok: bool | None = None

    @property
    def available(self) -> bool:
        return any(a.kind in ("photo", "screenshot") for a in self.pack.assets) or self.cfg.enabled

    def get(self, o: OutlineSlide, aspect: float, dark: bool) -> tuple[str, str] | None:
        asset = self._pack_asset(o)
        if asset is not None:
            self.used.add(asset.id)
            return asset.path, "pack"
        if not self.cfg.enabled or self.generated >= self.cfg.max_images_per_deck:
            return None
        prompt = self._prompt(o, aspect, dark)
        path = self._generate(prompt, aspect)
        if path:
            self.generated += 1
            return str(path), "t2i"
        return None

    def _pack_asset(self, o: OutlineSlide) -> Asset | None:
        free = [a for a in self.pack.assets if a.kind in ("photo", "screenshot") and a.id not in self.used]
        if not free:
            return None
        words = set((o.title + " " + (o.image or "")).lower().split())
        return max(free, key=lambda a: len(words & {t.lower() for t in a.tags}))

    def _prompt(self, o: OutlineSlide, aspect: float, dark: bool) -> str:
        primary = next((c.hex for c in self.design.palette if c.role == ColorRole.primary), "0077FF")
        accents = [c.hex for c in self.design.palette if c.role == ColorRole.accent][:3]
        if self.llm is not None and self.prompts is not None:
            try:
                from pydantic import BaseModel

                class _P(BaseModel):
                    prompt: str
                text = self.prompts.render("image_prompt", title=o.title, message=o.message, image=o.image or o.title,
                                           style="modern tech corporate", primary=primary, accents=accents, dark=dark,
                                           aspect=f"{aspect:.2f}:1")
                return self.llm.complete_json([ChatMessage("user", text)], _P, max_tokens=200, temperature=0.6).prompt
            except LLMError:
                pass
        return (f"clean corporate 3D illustration about {o.image or o.title}, glossy abstract shapes, "
                f"brand color #{primary}, {'dark' if dark else 'light'} background, soft studio light, no text, no logos")

    def _generate(self, prompt: str, aspect: float) -> Path | None:
        w = self.cfg.width
        h = max(256, int(round(w / max(0.3, aspect) / 16) * 16))
        key = hashlib.md5(f"{self.cfg.model}|{prompt}|{w}x{h}|{self.seed}".encode()).hexdigest()[:16]
        out = self.cache / f"t2i_{key}.png"
        if out.exists():
            return out
        pipe = self._pipe()
        if pipe is None:
            return None
        try:
            import torch
            with _LOCK:
                img = pipe(prompt=prompt, width=w, height=h, guidance_scale=self.cfg.guidance,
                           num_inference_steps=self.cfg.steps,
                           generator=torch.Generator(device="cuda").manual_seed(self.seed)).images[0]
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out)
            return out
        except Exception as e:  # noqa: BLE001
            self.log(f"t2i: генерация не удалась ({e})")
            return None

    def _pipe(self):
        global _PIPE
        if self._t2i_ok is False:
            return None
        with _LOCK:
            if _PIPE is not None:
                return _PIPE
            try:
                import torch
                if not torch.cuda.is_available():
                    raise RuntimeError("нет CUDA")
                free, _ = torch.cuda.mem_get_info()
                if free / 2**30 < self.cfg.min_free_vram_gb:
                    raise RuntimeError(f"свободно {free / 2**30:.1f} ГБ VRAM < {self.cfg.min_free_vram_gb}")
                from diffusers import Flux2KleinPipeline
                pipe = Flux2KleinPipeline.from_pretrained(self.cfg.model, torch_dtype=torch.bfloat16)
                if self.cfg.cpu_offload:
                    pipe.enable_model_cpu_offload()
                else:
                    pipe.to("cuda")
                _PIPE = pipe
                self._t2i_ok = True
                return _PIPE
            except Exception as e:  # noqa: BLE001
                self._t2i_ok = False
                self.log(f"t2i: модель не загружена ({e}) — картинки из шаблона/пакета")
                return None
