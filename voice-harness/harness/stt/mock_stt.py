"""Mock STT — 키/네트워크 없이 파이프라인 배선을 검증하기 위한 가짜 provider.

audio_path 와 같은 경로의 .txt 파일이 있으면 그 내용을 전사로 사용하고,
없으면 고정 문장을 반환한다. 짧은 지연을 흉내내어 delta를 쪼개 방출한다.
"""
from __future__ import annotations

import os
import time
from typing import Iterator

from .base import STTEvent

_DEFAULT = "안녕, 오늘 뭐 했어?"


class MockSTT:
    def __init__(self, model: str = "mock-stt") -> None:
        self.name = "mock"
        self.model = model

    def transcribe_stream(self, audio_path: str, language: str = "") -> Iterator[STTEvent]:
        sidecar = os.path.splitext(audio_path)[0] + ".txt"
        if os.path.exists(sidecar):
            with open(sidecar, encoding="utf-8") as f:
                text = f.read().strip()
        else:
            text = _DEFAULT
        # delta를 단어 단위로 쪼개 약간의 지연과 함께 방출(스트리밍 흉내)
        words = text.split()
        for i, w in enumerate(words):
            time.sleep(0.02)
            yield STTEvent(text=(w if i == 0 else " " + w), final=False)
        yield STTEvent(text=text, final=True)
