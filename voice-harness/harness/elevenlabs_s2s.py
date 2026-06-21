"""ElevenLabs Agents(구 Conversational AI) 음성 에이전트 어댑터.

내부는 STT+LLM+TTS 오케스트레이션이지만 인터페이스가 '오디오 입력 → 오디오 응답'이라
OpenAI Realtime S2S와 동일한 측정 틀(S2SResult)로 잰다. 자체 LLM 사용(우리 moly-server 미경유).

인증: private agent는 API 키를 WS에 직접 못 붙인다 → REST로 서명 URL 발급 후 그 URL로 연결.
측정점은 realtime_s2s.py와 동일 정의(발화 종료 → 첫 응답 음성).

⚠️ 공정성: EL은 자체 VAD로 턴 종료를 감지한다(수동 commit 불가). 그래서 EL 지연엔
   'end-pointing 지연'이 포함된다 — OpenAI(수동 commit) 대비 구조적으로 약간 불리.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
import urllib.request
import wave

from websocket import create_connection, WebSocketTimeoutException

from .realtime_s2s import S2SResult, _trim_trailing_silence

_SIGNED_URL = "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id={agent_id}"
_PUBLIC_URL = "wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}"
_CHUNK_MS = 40
_DEFAULT_SR = 16000
_TAIL_SILENCE_MS = 600           # VAD가 발화 종료를 감지하도록 끝에 덧붙이는 무음
EL_RATE_PER_MIN = 0.10           # ~$0.10/min (Creator/Pro). LLM 비용 별도(미포함, 추정치).


class ElevenLabsS2S:
    name = "elevenlabs"

    def __init__(self, api_key: str, agent_id: str, debug: bool = False) -> None:
        self.api_key = api_key
        self.agent_id = agent_id
        self.model = "elevenlabs"  # 비교표 라벨용
        self.debug = debug

    def _connect(self):
        """private agent: 서명 URL 발급(키는 헤더로만) 후 연결. 키 없으면 공개 agent로 직접 연결."""
        if self.api_key:
            req = urllib.request.Request(
                _SIGNED_URL.format(agent_id=self.agent_id),
                headers={"xi-api-key": self.api_key},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                signed = json.loads(resp.read().decode("utf-8"))["signed_url"]
            return create_connection(signed, timeout=30)
        return create_connection(_PUBLIC_URL.format(agent_id=self.agent_id), timeout=30)

    def converse(self, audio_path: str, out_dir: str = "runs") -> S2SResult:
        r = S2SResult()
        try:
            ws = self._connect()
        except Exception as e:  # noqa: BLE001
            r.error = f"연결 실패: {e}"
            return r

        audio_out: list[bytes] = []
        text_out: list[str] = []
        state = {"in_sr": _DEFAULT_SR, "out_fmt": "pcm_16000", "out_sr": _DEFAULT_SR, "done": False}

        def handle(msg: dict) -> None:
            ts = time.perf_counter()
            t = msg.get("type", "")
            if t == "conversation_initiation_metadata":
                meta = msg.get("conversation_initiation_metadata_event", {}) or {}
                state["in_sr"] = _sr_of(meta.get("user_input_audio_format"), _DEFAULT_SR)
                state["out_fmt"] = meta.get("agent_output_audio_format") or state["out_fmt"]
                state["out_sr"] = _sr_of(state["out_fmt"], _DEFAULT_SR)
            elif t == "audio":
                if r.t_first_audio_out is None:
                    r.t_first_audio_out = ts
                b = (msg.get("audio_event") or {}).get("audio_base_64", "")
                if b:
                    audio_out.append(base64.b64decode(b))
            elif t == "agent_response":
                txt = (msg.get("agent_response_event") or {}).get("agent_response", "")
                if txt:
                    text_out.append(txt)
            elif t == "agent_response_complete":
                state["done"] = True
            elif t == "ping":
                eid = (msg.get("ping_event") or {}).get("event_id")
                try:
                    ws.send(json.dumps({"type": "pong", "event_id": eid}))
                except Exception:  # noqa: BLE001
                    pass
            elif self.debug:
                print(f"    [event] {t}")

        try:
            r.t_start = time.perf_counter()
            ws.send(json.dumps({"type": "conversation_initiation_client_data"}))

            # 1) 초기 메타데이터 수신(입력 포맷 확보, 최대 ~3s)
            ws.settimeout(3.0)
            deadline = time.perf_counter() + 3.0
            while time.perf_counter() < deadline:
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    break
                if not raw:
                    break
                msg = json.loads(raw)
                handle(msg)
                if msg.get("type") == "conversation_initiation_metadata":
                    break

            in_sr = state["in_sr"]
            bpc = (in_sr * _CHUNK_MS // 1000) * 2

            # 2) 입력 오디오 디코딩(메타 rate) + 실시간 페이스 전송
            pcm = _trim_trailing_silence(_decode(audio_path, in_sr), in_sr)
            ws.settimeout(0.005)
            target = time.perf_counter()
            for off in range(0, len(pcm), bpc):
                ws.send(_chunk(pcm[off:off + bpc]))
                _drain(ws, handle)
                target += _CHUNK_MS / 1000
                slp = target - time.perf_counter()
                if slp > 0:
                    time.sleep(slp)

            # 3) 발화 종료 기준점(R2) + VAD 트리거용 무음
            r.t_audio_end = time.perf_counter()
            silence = b"\x00\x00" * int(in_sr * _TAIL_SILENCE_MS / 1000)
            for off in range(0, len(silence), bpc):
                ws.send(_chunk(silence[off:off + bpc]))
                _drain(ws, handle)
                time.sleep(_CHUNK_MS / 1000)

            # 4) 응답 완료까지 블로킹 수신
            ws.settimeout(30.0)
            while not state["done"]:
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
            try:
                r.audio_out_path = _write_audio(
                    b"".join(audio_out), audio_path, out_dir, state["out_fmt"], state["out_sr"])
            except Exception as e:  # noqa: BLE001
                r.extra["audio_write_error"] = str(e)
        # 비용: 분당 추정(LLM 제외). 토큰 usage가 없어 duration 기반.
        if r.total is not None:
            r.extra["duration_s"] = round(r.total, 3)
            r.extra["cost_usd"] = round(r.total / 60.0 * EL_RATE_PER_MIN, 6)
        return r


# --- 모듈 헬퍼 ---
def _chunk(pcm: bytes) -> str:
    return json.dumps({"user_audio_chunk": base64.b64encode(pcm).decode("ascii")})


def _drain(ws, handle) -> None:
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


def _decode(path: str, sr: int) -> bytes:
    cmd = ["ffmpeg", "-v", "quiet", "-i", path, "-ar", str(sr), "-ac", "1", "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg 디코딩 실패: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout


def _sr_of(fmt: "str | None", default: int) -> int:
    """'pcm_16000' / 'mp3_44100' 등에서 sample rate 추출."""
    if not fmt:
        return default
    m = re.search(r"(\d{4,6})", fmt)
    return int(m.group(1)) if m else default


def _write_audio(data: bytes, src: str, out_dir: str, out_fmt: str, out_sr: int) -> str:
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0]
    fmt = (out_fmt or "").lower()
    if "mp3" in fmt:
        path = os.path.join(out_dir, f"s2s-reply-{base}-el.mp3")
        with open(path, "wb") as f:
            f.write(data)
        return path
    # pcm(또는 미상) → 16-bit mono wav. (ulaw 등 특수 포맷이면 agent 출력을 pcm으로 바꾸길 권장)
    path = os.path.join(out_dir, f"s2s-reply-{base}-el.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(out_sr)
        w.writeframes(data)
    return path
