"""델타 스트림 → 문장 경계 분할.

moly-server 계약: "문장/단어 분할이 필요하면 소비자(TTS) 책임". delta는 토큰/단어/
문장 경계를 보장하지 않으므로, 들어오는 대로 버퍼에 누적하다가 문장 끝 구두점을
만나면 그 지점까지를 한 문장으로 방출한다. 이렇게 해야 첫 문장이 끝나는 즉시 TTS를
시작(LLM은 계속 생성)하여 end-to-end 지연을 낮출 수 있다.

한계(MVP): 'Mr.' '3.14' 같은 약어/소수점도 문장 끝으로 오인할 수 있다. 측정 목적에는
충분하며, 필요 시 후속 단계에서 보강한다.
"""
from __future__ import annotations

from typing import Iterator

# 영어/한국어/일반 문장 종결 부호
_SENTENCE_ENDERS = set(".!?…。！？\n")


class SentenceSplitter:
    def __init__(self, min_chars: int = 1) -> None:
        self._buf: str = ""
        self._min = min_chars

    def feed(self, delta: str) -> Iterator[str]:
        """delta 조각을 넣고, 완성된 문장이 있으면 순서대로 방출."""
        self._buf += delta
        while True:
            idx = self._first_boundary(self._buf)
            if idx == -1:
                break
            sentence = self._buf[: idx + 1].strip()
            self._buf = self._buf[idx + 1 :]
            if len(sentence) >= self._min:
                yield sentence

    def flush(self) -> Iterator[str]:
        """스트림 종료 시 남은 버퍼(종결부호 없이 끝난 꼬리)를 방출."""
        tail = self._buf.strip()
        self._buf = ""
        if len(tail) >= self._min:
            yield tail

    @staticmethod
    def _first_boundary(s: str) -> int:
        for i, ch in enumerate(s):
            if ch in _SENTENCE_ENDERS:
                return i
        return -1
