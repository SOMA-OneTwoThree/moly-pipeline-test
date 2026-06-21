"""Deepgram STT — Nova-3 실시간 스트리밍(WebSocket).

OpenAI realtime과 동일 기준으로 측정: 오디오를 실시간 페이스로 흘려보내고,
발화 종료(끝 무음 trim 후 마지막 청크 전송 시점) → 최종 전사까지의 꼬리지연을 잰다.
무음은 주입하지 않고 파일 끝 무음을 trim해 '마지막 말 직후'를 발화 종료로 본다.
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
_CHUNK_MS = 40
_URL = "wss://api.deepgram.com/v1/listen"


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


class DeepgramSTT:
    def __init__(self, api_key: str, model: str = "nova-3") -> None:
        self.name = "deepgram"
        self.model = model
        self._key = api_key

    def transcribe_stream(self, audio_path: str, language: str = "") -> Iterator[STTEvent]:
        pcm = _trim_trailing(_decode_pcm(audio_path, _SR), _SR)
        qs = (
            f"model={self.model}&encoding=linear16&sample_rate={_SR}&channels=1"
            f"&interim_results=true&endpointing=300&smart_format=true&language={language or 'multi'}"
        )
        ws = create_connection(
            f"{_URL}?{qs}", header=[f"Authorization: Token {self._key}"], timeout=30
        )
        segments: list[str] = []
        last_final: float | None = None

        def handle(msg: dict) -> list[STTEvent]:
            nonlocal last_final
            if msg.get("type") != "Results":
                return []
            alt = (msg.get("channel", {}).get("alternatives") or [{}])[0]
            txt = alt.get("transcript", "")
            if not txt:
                return []
            if msg.get("is_final"):
                segments.append(txt)
                last_final = time.perf_counter()
                return []
            return [STTEvent(text=txt, at=time.perf_counter())]  # interim delta(첫 delta 타이밍)

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
            ws.send(json.dumps({"type": "CloseStream"}))  # 즉시 최종화 요청

            ws.settimeout(5.0)
            while True:
                try:
                    raw = ws.recv()
                except (WebSocketTimeoutException, OSError):
                    break
                if not raw:
                    break
                for ev in handle(json.loads(raw)):
                    yield ev
                if msg_is_final_meta(raw):
                    break
            yield STTEvent(text=" ".join(segments).strip(), final=True, at=last_final)
        finally:
            try:
                ws.close()
            except OSError:
                pass


def msg_is_final_meta(raw: str) -> bool:
    try:
        return json.loads(raw).get("type") == "Metadata"
    except json.JSONDecodeError:
        return False


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
