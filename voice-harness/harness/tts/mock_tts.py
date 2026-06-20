"""Mock TTS — 키 없이 배선 검증용. 글자수에 비례한 무음 PCM을 청크로 방출한다."""
from __future__ import annotations

import time
from typing import Iterator

_SAMPLE_RATE = 24000


class MockTTS:
    def __init__(self) -> None:
        self.name = "mock"
        self.model = "mock-tts"
        self.fmt = "pcm"
        self.sample_rate = _SAMPLE_RATE

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        # 글자당 ~60ms 무음. 첫 청크 전 약간의 '합성 지연'을 흉내.
        time.sleep(0.05)
        total_samples = max(1, int(len(text) * 0.06 * self.sample_rate))
        chunk_samples = self.sample_rate // 10  # 100ms 청크
        emitted = 0
        while emitted < total_samples:
            n = min(chunk_samples, total_samples - emitted)
            emitted += n
            time.sleep(0.01)
            yield b"\x00\x00" * n  # 16-bit 무음
