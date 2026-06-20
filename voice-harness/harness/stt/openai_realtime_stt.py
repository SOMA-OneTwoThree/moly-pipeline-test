"""OpenAI Realtime STT — WebSocket 실시간 스트리밍 전사.

파일 벤치마크에서 '실시간 대화'를 재현한다:
  1) ffmpeg로 입력 오디오를 PCM16 24kHz mono로 디코딩
  2) WebSocket으로 40ms 청크를 실시간 페이스로 흘려보냄(말하는 속도 모사)
  3) server VAD가 발화 중 부분 전사(delta)를 스트리밍, 발화 종료를 감지해 확정(completed)
  4) 마지막 실제 음성 청크를 보낸 직후 audio_end 신호 → 이후 '꼬리 지연' 측정

이렇게 하면 파일 통째 전송(배치) 대비, 발화 종료 후 남는 진짜 지연만 측정된다.
"""
from __future__ import annotations

import base64
import json
import subprocess
import time
from typing import Iterator

from websocket import create_connection, WebSocketTimeoutException

from .base import STTEvent

_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
_SR = 24000
_CHUNK_MS = 40
_VAD_SILENCE_MS = 500  # VAD 발화 종료 판정 침묵 길이. 문장 중간 멈춤(<500ms)엔 안 끊김 = 단일 전사


class OpenAIRealtimeSTT:
    def __init__(self, model: str = "gpt-4o-transcribe", api_key: str = "") -> None:
        self.name = "openai_realtime"
        self.model = model
        self._key = api_key
        self.sample_rate = _SR

    # --- 공개 인터페이스 ---
    def transcribe_stream(self, audio_path: str, language: str = "") -> Iterator[STTEvent]:
        pcm = self._decode(audio_path)
        ws = create_connection(
            _URL,
            header=[f"Authorization: Bearer {self._key}"],  # GA: OpenAI-Beta 헤더 제거됨
            timeout=30,
        )
        try:
            ws.send(json.dumps(self._session_update(language)))
            bpc = (_SR * _CHUNK_MS // 1000) * 2  # bytes per 40ms chunk (16-bit mono)
            ws.settimeout(0.005)  # 거의 논블로킹 폴링

            segments: list[str] = []   # VAD가 자연 멈춤에서 끊은 조각 전사들
            last_done: float | None = None
            saw_stop = False           # speech_stopped(발화 종료) 수신 여부

            def consume(events: list[STTEvent]) -> list[STTEvent]:
                """final/audio_end는 흡수해 상태로 모으고, 흘려보낼(yield) 이벤트만 반환."""
                nonlocal saw_stop, last_done
                out: list[STTEvent] = []
                for ev in events:
                    if ev.audio_end:
                        saw_stop = True
                        out.append(ev)            # 발화 종료 → pipeline 꼬리지연 기준점(마지막이 이김)
                    elif ev.final:
                        segments.append(ev.text)  # 조각 전사 누적
                        last_done = ev.at
                    else:
                        out.append(ev)            # delta
                return out

            target = time.perf_counter()

            # 1) 녹음 전체를 실시간 페이스로 전송(말 속도 모사). 중간에 끊지 않음 → 전체 전사 보존.
            #    delta는 흘리고, 조각 전사·발화종료(speech_stopped)는 consume에서 모은다.
            for off in range(0, len(pcm), bpc):
                ws.send(self._append(pcm[off : off + bpc]))
                for ev in consume(self._drain(ws)):
                    yield ev
                target += _CHUNK_MS / 1000
                slp = target - time.perf_counter()
                if slp > 0:
                    time.sleep(slp)
            file_send_end = time.perf_counter()  # speech_stopped 못 받을 때의 fallback 기준점

            # 2) 무음 주입 없음(파일 자체 끝 무음에 의존). 남은 전사를 짧은 idle로 마저 수신.
            #    t_stt_done은 마지막 조각의 실제 도착시각(last_done)을 쓰므로 idle이 꼬리지연을 부풀리지 않는다.
            ws.settimeout(0.3)
            hard_deadline = time.perf_counter() + 15.0
            while time.perf_counter() < hard_deadline:
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    break  # 0.3s 동안 새 이벤트 없음 → 수신 종료
                except Exception:  # noqa: BLE001
                    break
                if not raw:
                    break
                for ev in consume(self._handle(json.loads(raw))):
                    yield ev

            # VAD가 speech_stopped를 안 줬으면 파일 전송 종료를 fallback 기준점으로
            if not saw_stop:
                yield STTEvent(audio_end=True, at=file_send_end)

            transcript = " ".join(s for s in segments if s).strip()
            yield STTEvent(text=transcript, final=True, at=last_done)
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    # --- 내부 ---
    def _decode(self, path: str) -> bytes:
        cmd = ["ffmpeg", "-v", "quiet", "-i", path, "-ar", str(_SR), "-ac", "1", "-f", "s16le", "-"]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not proc.stdout:
            raise RuntimeError(f"ffmpeg 디코딩 실패: {proc.stderr.decode('utf-8','replace')[:200]}")
        return proc.stdout

    def _session_update(self, language: str) -> dict:
        transcription: dict = {"model": self.model}
        if language:
            transcription["language"] = language
        return {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": _SR},
                        "transcription": transcription,
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": _VAD_SILENCE_MS,  # 문장 중간 멈춤에 안 끊기게 + 현실적 턴 감지
                        },
                    }
                },
            },
        }

    @staticmethod
    def _append(pcm_chunk: bytes) -> str:
        return json.dumps(
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm_chunk).decode("ascii")}
        )

    def _drain(self, ws) -> list[STTEvent]:
        """버퍼에 쌓인 이벤트를 논블로킹으로 모두 읽어 STTEvent로 변환."""
        out: list[STTEvent] = []
        while True:
            try:
                raw = ws.recv()
            except WebSocketTimeoutException:
                break
            except Exception:  # noqa: BLE001 — SSL want-read 등 일시적 → 폴링 종료
                break
            if not raw:
                break
            out.extend(self._handle(json.loads(raw)))
        return out

    @staticmethod
    def _handle(msg: dict) -> list[STTEvent]:
        ts = time.perf_counter()
        t = msg.get("type", "")
        if t == "error":
            err = msg.get("error", {})
            raise RuntimeError(f"Realtime API 오류: {err.get('message', msg)}")
        if t == "input_audio_buffer.speech_stopped":
            # VAD가 감지한 실제 발화 종료 시점 → 꼬리지연 기준점(여러 번이면 마지막이 턴 종료)
            return [STTEvent(audio_end=True, at=ts)]
        if t.endswith("input_audio_transcription.delta"):
            d = msg.get("delta", "")
            return [STTEvent(text=d, final=False, at=ts)] if d else []
        if t.endswith("input_audio_transcription.completed"):
            return [STTEvent(text=msg.get("transcript", ""), final=True, at=ts)]
        return []
