"""OpenAI Realtime 음성→음성(speech-to-speech).

Realtime 모델 하나가 오디오를 직접 듣고(이해) → 응답을 생성 → 오디오로 말한다.
STT→LLM→TTS 파이프라인과 달리 moly-server·TTS를 거치지 않는다(최저지연 비교군).

측정: 발화 종료(speech_stopped) → 첫 응답 오디오 = 음성→음성 체감 지연.
무음은 주입하지 않는다 — 녹음 파일의 끝 무음으로 VAD가 발화 종료를 감지.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import wave
from dataclasses import dataclass, field

import numpy as np
from websocket import create_connection, WebSocketTimeoutException

_SR = 24000
_CHUNK_MS = 40

_URL = "wss://api.openai.com/v1/realtime?model={model}"
_DEFAULT_MODEL = "gpt-realtime"
_DEFAULT_VOICE = "marin"
_INSTRUCTIONS = (
    "You are a warm, friendly English conversation partner. "
    "Reply in short, natural spoken English that is easy to say aloud."
)


@dataclass
class S2SResult:
    transcript_out: str = ""
    t_start: float = 0.0
    t_audio_end: float | None = None       # 발화 종료(speech_stopped)
    t_first_audio_out: float | None = None # 첫 응답 오디오
    t_done: float | None = None
    audio_out_path: str = ""
    error: str = ""
    extra: dict = field(default_factory=dict)

    def _d(self, a, b):
        return None if (a is None or b is None) else round(b - a, 4)

    @property
    def latency(self):  # 발화 종료 → 첫 응답 음성 (핵심 체감 지연)
        return self._d(self.t_audio_end, self.t_first_audio_out)

    @property
    def total(self):
        return self._d(self.t_start, self.t_done)


class RealtimeS2S:
    def __init__(self, api_key: str, model: str = "", voice: str = "", debug: bool = False,
                 effort: str = "") -> None:
        self.api_key = api_key
        self.model = model or _DEFAULT_MODEL
        self.voice = voice or _DEFAULT_VOICE
        self.debug = debug
        # reasoning effort(minimal|low|medium|high|xhigh). gpt-realtime-2 등 추론 모델만 의미.
        # 빈 값이면 미지정(모델 기본값) — v1/1.5는 reasoning 미지원이라 보내지 않는다.
        self.effort = effort.strip()

    def converse(self, audio_path: str, out_dir: str = "runs") -> S2SResult:
        r = S2SResult()
        pcm = self._decode(audio_path)
        ws = create_connection(
            _URL.format(model=self.model),
            header=[f"Authorization: Bearer {self.api_key}"],
            timeout=30,
        )
        audio_out: list[bytes] = []
        text_out: list[str] = []
        done = False

        def handle(msg: dict) -> None:
            nonlocal done
            ts = time.perf_counter()
            t = msg.get("type", "")
            if t == "error":
                raise RuntimeError(f"Realtime 오류: {msg.get('error', msg)}")
            elif t == "input_audio_buffer.speech_stopped":
                r.t_audio_end = ts  # 발화 종료(여러 번이면 마지막이 턴 종료)
            elif "audio.delta" in t and "transcript" not in t:
                if r.t_first_audio_out is None:
                    r.t_first_audio_out = ts
                b = msg.get("delta", "")
                if b:
                    audio_out.append(base64.b64decode(b))
            elif "audio_transcript.delta" in t or t.endswith("output_text.delta"):
                text_out.append(msg.get("delta", ""))
            elif t == "response.done":
                # 정확 비용 산정용 토큰 usage 캡처(있으면). cost_model.cost_realtime_from_usage 가 소비.
                usage = (msg.get("response") or {}).get("usage")
                if usage:
                    r.extra["usage"] = usage
                done = True
            elif self.debug and t not in _QUIET:
                print(f"    [event] {t}")

        try:
            r.t_start = time.perf_counter()
            ws.send(json.dumps(self._session_update()))

            pcm = _trim_trailing_silence(pcm, _SR)  # 끝 무음 제거 → '마지막 말 직후'를 턴 종료로
            bpc = (_SR * _CHUNK_MS // 1000) * 2
            ws.settimeout(0.005)
            target = time.perf_counter()

            # 1) 녹음(끝 무음 제거)을 실시간 페이스로 전송(말 속도 모사)
            for off in range(0, len(pcm), bpc):
                ws.send(self._append(pcm[off : off + bpc]))
                self._drain(ws, handle)
                target += _CHUNK_MS / 1000
                slp = target - time.perf_counter()
                if slp > 0:
                    time.sleep(slp)

            # 2) 턴 종료(발화 끝) 기준점 → 수동 commit + 응답 생성 요청(한 턴, 한 응답)
            r.t_audio_end = time.perf_counter()
            ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            ws.send(json.dumps({"type": "response.create"}))

            # 3) 응답 완료까지 블로킹 수신
            ws.settimeout(30.0)
            while not done:
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    break
                if not raw:
                    break
                handle(json.loads(raw))
            r.t_done = time.perf_counter()
        except Exception as e:  # noqa: BLE001
            r.error = str(e)
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

        r.transcript_out = "".join(text_out).strip()
        if audio_out:
            r.audio_out_path = self._write_wav(b"".join(audio_out), audio_path, out_dir)
        return r

    # --- 내부 ---
    def _drain(self, ws, handle) -> None:
        while True:
            try:
                raw = ws.recv()
            except WebSocketTimeoutException:
                break
            except Exception:  # noqa: BLE001
                break
            if not raw:
                break
            handle(json.loads(raw))

    def _session_update(self) -> dict:
        session: dict = {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": _INSTRUCTIONS,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": _SR},
                    "turn_detection": None,  # VAD 비활성 — 파일 전체를 한 턴으로 보고 수동 commit+response
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": _SR},
                    "voice": self.voice,
                },
            },
        }
        # 추론 모델(gpt-realtime-2)일 때만 effort 전달. 미지원 모델에 보내면 거부될 수 있어 옵션.
        if self.effort:
            session["reasoning"] = {"effort": self.effort}
        return {"type": "session.update", "session": session}

    @staticmethod
    def _append(chunk: bytes) -> str:
        return json.dumps(
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(chunk).decode("ascii")}
        )

    def _decode(self, path: str) -> bytes:
        cmd = ["ffmpeg", "-v", "quiet", "-i", path, "-ar", str(_SR), "-ac", "1", "-f", "s16le", "-"]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not proc.stdout:
            raise RuntimeError(f"ffmpeg 디코딩 실패: {proc.stderr.decode('utf-8','replace')[:200]}")
        return proc.stdout

    @staticmethod
    def _write_wav(pcm: bytes, src: str, out_dir: str) -> str:
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(src))[0]
        path = os.path.join(out_dir, f"s2s-reply-{base}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(_SR)
            w.writeframes(pcm)
        return path


def _trim_trailing_silence(pcm: bytes, sr: int, thresh: int = 500, pad_ms: int = 120) -> bytes:
    """끝부분 무음을 잘라 '마지막 말 직후'에서 턴이 끝나게 한다(앞부분은 보존)."""
    a = np.frombuffer(pcm, dtype="<i2")
    loud = np.where(np.abs(a) > thresh)[0]
    if len(loud) == 0:
        return pcm
    end = min(len(a), int(loud[-1]) + int(sr * pad_ms / 1000))
    return a[:end].tobytes()


# 디버그 출력에서 가릴 잡음 이벤트
_QUIET = {
    "session.created",
    "session.updated",
    "input_audio_buffer.speech_started",
    "input_audio_buffer.committed",
    "rate_limits.updated",
    "response.created",
}
