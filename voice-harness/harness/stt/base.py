"""STT provider 인터페이스. moly-server의 LLMProvider 추상화와 동일한 역할."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol


@dataclass
class STTEvent:
    """전사 진행 이벤트.
    - delta: 부분 전사(text 채워짐, final=False)
    - final: 확정 전사(text=전체, final=True)
    - audio_end: 오디오/발화 전송 종료 신호(실시간 STT 전용; text 없음, 꼬리지연 기준점)
    """
    text: str = ""
    final: bool = False
    audio_end: bool = False
    at: float | None = None  # 이벤트 실제 발생 시각(perf_counter). 있으면 pipeline이 이 값으로 마크
    usage: dict | None = None  # (final 이벤트) 토큰 usage — 정확 비용 산정용. 없으면 None


class STTProvider(Protocol):
    name: str
    model: str

    def transcribe_stream(self, audio_path: str, language: str = "") -> Iterator[STTEvent]:
        """오디오 파일을 (가능하면 스트리밍으로) 전사. delta STTEvent들을 순서대로
        yield하고 마지막에 final=True 이벤트로 확정 전사를 방출한다."""
        ...
