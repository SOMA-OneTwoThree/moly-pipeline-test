"""OpenAI TTS — gpt-4o-mini-tts (청크 스트리밍).

response_format=pcm → 24kHz 16-bit mono raw PCM(컨테이너 없음 → 최저지연, 즉시 재생/이어붙이기 용이).
with_streaming_response로 첫 오디오 청크를 가능한 빨리 받는다.
"""
from __future__ import annotations

from typing import Iterator

from openai import OpenAI

_SAMPLE_RATE = 24000  # gpt-4o-mini-tts pcm 출력 고정


class OpenAITTS:
    def __init__(
        self,
        model: str = "gpt-4o-mini-tts",
        voice: str = "coral",
        fmt: str = "pcm",
        speed: float = 1.0,
        api_key: str = "",
    ) -> None:
        self.name = "openai"
        self.model = model
        self.voice = voice
        self.fmt = fmt
        self.speed = speed  # 말하는 빠르기 0.25~4.0 (tts-1/tts-1-hd 지원). 1.0=기본
        self.sample_rate = _SAMPLE_RATE
        self._client = OpenAI(api_key=api_key or None)

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        kwargs: dict = {
            "model": self.model,
            "voice": self.voice,
            "input": text,
            "response_format": self.fmt,
        }
        # speed는 tts-1 계열만 지원. gpt-4o-mini-tts는 instructions로 제어하므로 speed 생략.
        if self.speed != 1.0 and self.model.startswith("tts-1"):
            kwargs["speed"] = self.speed
        with self._client.audio.speech.with_streaming_response.create(**kwargs) as response:
            for chunk in response.iter_bytes(4096):
                if chunk:
                    yield chunk
