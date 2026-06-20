"""OpenAI STT — gpt-4o-transcribe (스트리밍 전사).

whisper-1은 스트리밍 미지원이라 제외. gpt-4o-transcribe / gpt-4o-mini-transcribe는
transcriptions 엔드포인트에서 stream=True로 전사 delta를 SSE로 받는다.
"""
from __future__ import annotations

from typing import Iterator

from openai import OpenAI

from .base import STTEvent


class OpenAISTT:
    def __init__(self, model: str = "gpt-4o-transcribe", api_key: str = "") -> None:
        self.name = "openai"
        self.model = model
        self._client = OpenAI(api_key=api_key or None)

    def transcribe_stream(self, audio_path: str, language: str = "") -> Iterator[STTEvent]:
        kwargs: dict = {"model": self.model, "stream": True}
        if language:
            kwargs["language"] = language
        with open(audio_path, "rb") as f:
            kwargs["file"] = f
            stream = self._client.audio.transcriptions.create(**kwargs)
            final_text = ""
            usage: dict | None = None
            for event in stream:
                etype = getattr(event, "type", "")
                if etype == "transcript.text.delta":
                    delta = getattr(event, "delta", "") or ""
                    final_text += delta
                    if delta:
                        yield STTEvent(text=delta, final=False)
                elif etype == "transcript.text.done":
                    final_text = getattr(event, "text", final_text) or final_text
                    # 정확 비용용 토큰 usage(있으면). SDK 객체 → dict 정규화.
                    u = getattr(event, "usage", None)
                    if u is not None:
                        usage = u if isinstance(u, dict) else getattr(u, "model_dump", lambda: None)()
            yield STTEvent(text=final_text, final=True, usage=usage)
