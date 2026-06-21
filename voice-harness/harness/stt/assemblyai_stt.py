"""AssemblyAI STT — Universal Streaming v3 (WebSocket).

OpenAI realtime·Deepgram과 동일 기준: 오디오를 실시간 페이스로 흘려보내고 발화 종료(끝 무음
trim 후 마지막 청크 전송) → 최종 전사까지의 꼬리지연을 잰다. v3는 'Turn' 이벤트로 전사를
주고, end_of_turn=true가 턴 종료. 종료 시 {"type":"Terminate"} 전송.
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Iterator

import numpy as np
from websocket import create_connection, WebSocketTimeoutException

from .base import STTEvent

_SR = 16000
_CHUNK_MS = 100  # AssemblyAI v3는 청크 50~1000ms 요구
_URL = f"wss://streaming.assemblyai.com/v3/ws?sample_rate={_SR}&encoding=pcm_s16le&format_turns=true"


def _decode_pcm(path: str, sr: int) -> bytes:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ar", str(sr), "-ac", "1", "-f", "s16le", "-"],
        capture_output=True,
    )
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError(f"ffmpeg 디코딩 실패: {out.stderr.decode('utf-8','replace')[:200]}")
    return out.stdout


def _trim_trailing(pcm: bytes, sr: int, thresh: int = 500, pad_ms: int = 120) -> bytes:
    a = np.frombuffer(pcm, dtype="<i2")
    loud = np.where(np.abs(a) > thresh)[0]
    if len(loud) == 0:
        return pcm
    end = min(len(a), int(loud[-1]) + int(sr * pad_ms / 1000))
    return a[:end].tobytes()


class AssemblyAISTT:
    def __init__(self, api_key: str, model: str = "universal-streaming") -> None:
        self.name = "assemblyai"
        self.model = model
        self._key = api_key

    def transcribe_stream(self, audio_path: str, language: str = "") -> Iterator[STTEvent]:
        pcm = _trim_trailing(_decode_pcm(audio_path, _SR), _SR)
        ws = create_connection(_URL, header=[f"Authorization: {self._key}"], timeout=30)

        turns: dict[int, str] = {}  # turn_order → 전사. 멈춤으로 나뉜 턴을 이어붙임
        state = {"last": None, "first": None}

        def handle(msg: dict) -> list[STTEvent]:
            if msg.get("type") != "Turn":
                return []
            txt = msg.get("transcript", "") or ""
            if not txt:
                return []
            turns[msg.get("turn_order", 0)] = txt
            state["last"] = time.perf_counter()
            if state["first"] is None:
                state["first"] = state["last"]
                return [STTEvent(text=txt, at=state["last"])]  # interim delta(첫 delta 타이밍)
            return []

        try:
            ws.settimeout(0.005)
            bpc = (_SR * _CHUNK_MS // 1000) * 2
            target = time.perf_counter()
            for off in range(0, len(pcm), bpc):
                ws.send_binary(pcm[off : off + bpc])
                for ev in _drain(ws, handle):
                    yield ev
                target += _CHUNK_MS / 1000
                slp = target - time.perf_counter()
                if slp > 0:
                    time.sleep(slp)

            yield STTEvent(audio_end=True, at=time.perf_counter())  # 발화 종료 기준점
            ws.send(json.dumps({"type": "Terminate"}))

            ws.settimeout(5.0)
            while True:
                try:
                    raw = ws.recv()
                except (WebSocketTimeoutException, OSError):
                    break
                if not raw:
                    break
                m = json.loads(raw)
                for ev in handle(m):
                    yield ev
                if m.get("type") == "Termination":
                    break
            transcript = " ".join(turns[k] for k in sorted(turns)).strip()
            yield STTEvent(text=transcript, final=True, at=state["last"])
        finally:
            try:
                ws.close()
            except OSError:
                pass


def _drain(ws, handle) -> list[STTEvent]:
    out: list[STTEvent] = []
    while True:
        try:
            raw = ws.recv()
        except (WebSocketTimeoutException, OSError):
            break
        if not raw:
            break
        out.extend(handle(json.loads(raw)))
    return out
