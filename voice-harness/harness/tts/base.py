"""TTS provider 인터페이스. 문장 텍스트 → 오디오 청크 스트림."""
from __future__ import annotations

from typing import Iterator, Protocol


class TTSProvider(Protocol):
    name: str
    model: str
    sample_rate: int   # PCM 재생/저장용 (예: 24000)
    fmt: str           # "pcm" | "wav" | "mp3"

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        """text를 음성으로 합성. 오디오 바이트 청크를 도착 순서대로 yield.
        첫 청크가 빨리 나올수록 체감 지연이 낮다(저지연 목표)."""
        ...
