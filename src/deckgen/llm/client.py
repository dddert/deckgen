"""OpenAI-совместимый клиент: vLLM локально (RTX A6000), инференс VK (топ-10), любой /v1/chat/completions.

- complete_json: ответ строго по JSON-схеме (vLLM guided decoding) + валидация pydantic + повтор с ошибкой;
- потокобезопасен, число одновременных запросов ограничено (vLLM сам батчит — это главный резерв скорости);
- картинки для VLM ужимаются до 1280 px JPEG — вдвое меньше токенов на изображение;
- provider: offline -> клиента нет, агенты работают эвристиками (прогон без GPU, тесты, падение сервера).
"""
from __future__ import annotations

import base64
import io
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..settings import ModelEndpoint

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


@dataclass
class ChatMessage:
    role: str
    content: str
    images: list[bytes] = field(default_factory=list)


def _image_part(png: bytes, max_px: int = 1280) -> dict:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(png)) as im:
            im = im.convert("RGB")
            if max(im.size) > max_px:
                k = max_px / max(im.size)
                im = im.resize((int(im.width * k), int(im.height * k)))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88)
            data, mime = buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001
        data, mime = png, "image/png"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}


def _to_openai(m: ChatMessage) -> dict:
    if not m.images:
        return {"role": m.role, "content": m.content}
    return {"role": m.role, "content": [{"type": "text", "text": m.content}] + [_image_part(i) for i in m.images]}


class LLMClient:
    def __init__(self, ep: ModelEndpoint, name: str = "llm"):
        from openai import OpenAI
        self.ep, self.name = ep, name
        self.client = OpenAI(base_url=ep.base_url, api_key=ep.api_key or "none", timeout=ep.timeout_seconds, max_retries=1)
        self._sem = threading.BoundedSemaphore(max(1, ep.max_concurrency))
        self._lock = threading.Lock()
        self._alive: bool | None = None
        self.stats = {"calls": 0, "errors": 0, "json_retries": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}

    # ------------------------------------------------------------------
    def alive(self) -> bool:
        """Сервер отвечает? (кэшируется на время прогона)"""
        if self._alive is None:
            try:
                self.client.with_options(timeout=5, max_retries=0).models.list()
                self._alive = True
            except Exception:  # noqa: BLE001
                self._alive = False
        return self._alive

    def complete(self, messages: list[ChatMessage], **kw: Any) -> str:
        extra = dict(self.ep.extra_body or {})
        extra.update(kw.pop("extra_body", {}) or {})
        rf = kw.pop("response_format", None)
        t0 = time.perf_counter()
        try:
            with self._sem:
                resp = self.client.chat.completions.create(
                    model=self.ep.model, messages=[_to_openai(m) for m in messages],
                    temperature=kw.get("temperature", self.ep.temperature), top_p=kw.get("top_p", self.ep.top_p),
                    max_tokens=kw.get("max_tokens", self.ep.max_tokens), seed=kw.get("seed"),
                    response_format=rf, extra_body=extra or None,
                )
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self.stats["errors"] += 1
            raise LLMError(f"{self.name}: {e}") from e
        with self._lock:
            self.stats["calls"] += 1
            self.stats["seconds"] += time.perf_counter() - t0
            if resp.usage:
                self.stats["prompt_tokens"] += resp.usage.prompt_tokens or 0
                self.stats["completion_tokens"] += resp.usage.completion_tokens or 0
        return resp.choices[0].message.content or ""

    def complete_json(self, messages: list[ChatMessage], schema: type[T], **kw: Any) -> T:
        if self.ep.structured_output:
            kw["response_format"] = {"type": "json_schema",
                                     "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}}
        msgs, last = list(messages), None
        for _ in range(max(1, self.ep.json_retries + 1)):
            text = self.complete(msgs, **dict(kw))
            try:
                return schema.model_validate(json.loads(_strip(text)))
            except (json.JSONDecodeError, ValidationError) as e:
                last = e
                with self._lock:
                    self.stats["json_retries"] += 1
                msgs = msgs + [ChatMessage("assistant", text[:4000]),
                               ChatMessage("user", f"Ответ не прошёл проверку схемы: {str(e)[:600]}. Верни только исправленный JSON.")]
        raise LLMError(f"{self.name}: невалидный JSON после повторов: {last}")


def _strip(text: str) -> str:
    t = text.strip()
    if "</think>" in t:
        t = t.split("</think>", 1)[1].strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    s, e = t.find("{"), t.rfind("}")
    return t[s:e + 1] if s >= 0 and e > s else t


def make_client(ep: ModelEndpoint, name: str) -> LLMClient | None:
    if ep.provider in ("offline", "none", ""):
        return None
    if ep.provider != "openai_compat":
        raise ValueError(f"unknown provider {ep.provider}")
    return LLMClient(ep, name)
