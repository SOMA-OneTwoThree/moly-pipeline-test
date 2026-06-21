"""STT → LLM → TTS 오케스트레이션.

핵심: LLM delta를 문장 단위로 쪼개, 첫 문장이 완성되는 즉시 TTS를 시작한다(별도 스레드).
LLM이 다음 문장을 생성하는 동안 TTS가 앞 문장을 합성 → 파이프라이닝으로 체감 지연 최소화.
모든 경계에서 perf_counter 타임스탬프를 찍어 TurnMetrics로 돌려준다.
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import uuid
import wave

import numpy as np

from .config import Config
from .llm_client import LLMStreamError, stream_chat
from .metrics import TurnMetrics, combo_id, now, slug
from .sentence_splitter import SentenceSplitter
from .stt.base import STTProvider
from .tts.base import TTSProvider


def run_turn(
    cfg: Config,
    stt: STTProvider,
    tts: TTSProvider,
    *,
    audio_path: str,
    input_label: str,
) -> TurnMetrics:
    m = TurnMetrics(
        input_label=input_label,
        stt_provider=stt.name,
        stt_model=stt.model,
        tts_provider=tts.name,
        tts_model=tts.model,
    )
    m.t_start = now()
    m.run_id = uuid.uuid4().hex[:8]
    m.timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    m.input_audio_sec = _audio_duration(audio_path)

    # 1) STT (스트리밍 전사)
    try:
        transcript = ""
        for ev in stt.transcribe_stream(audio_path, language=cfg.stt_language):
            ev_at = getattr(ev, "at", None) or now()  # provider가 실제 발생시각을 주면 그걸 사용
            if getattr(ev, "audio_end", False):
                # 실시간 STT: 발화 종료(speech_stopped) — 꼬리지연 기준점. 여러 번이면 마지막(턴 종료)이 이김
                m.t_audio_end = ev_at
                continue
            if ev.final:
                transcript = ev.text
                m.t_stt_done = ev_at
                if getattr(ev, "usage", None):
                    m.extra["stt_usage"] = ev.usage  # 정확 STT 비용 산정용
            elif m.t_stt_first is None:
                m.t_stt_first = ev_at
        m.transcript = transcript
    except Exception as e:  # noqa: BLE001 — provider 오류를 턴 결과로 흡수
        m.error = f"STT: {e}"
        return m

    if not transcript.strip():
        m.error = "빈 전사(STT 결과 없음)"
        return m

    # 2)+3) LLM 스트림 + TTS(별도 스레드, 겹쳐 실행)
    sentences: "queue.Queue[str | None]" = queue.Queue()
    audio_chunks: list[bytes] = []
    lock = threading.Lock()
    reply_parts: list[str] = []

    def tts_worker() -> None:
        while True:
            s = sentences.get()
            if s is None:
                return
            with lock:
                if m.t_tts_req is None:
                    m.t_tts_req = now()
            try:
                for chunk in tts.synthesize_stream(s):
                    with lock:
                        if m.t_tts_first_audio is None:
                            m.t_tts_first_audio = now()
                    audio_chunks.append(chunk)
                m.t_tts_done = now()
            except Exception as e:  # noqa: BLE001
                with lock:
                    if not m.error:
                        m.error = f"TTS: {e}"

    worker = threading.Thread(target=tts_worker, daemon=True)
    worker.start()

    splitter = SentenceSplitter()
    llm_usage: dict = {}
    m.t_llm_req = now()
    try:
        for delta in stream_chat(cfg.moly_server_url, transcript, on_usage=llm_usage.update):
            if m.t_llm_first is None:
                m.t_llm_first = now()
            reply_parts.append(delta)
            for sentence in splitter.feed(delta):
                if m.t_first_sentence is None:
                    m.t_first_sentence = now()
                sentences.put(sentence)
        m.t_llm_done = now()
        for sentence in splitter.flush():
            if m.t_first_sentence is None:
                m.t_first_sentence = now()
            sentences.put(sentence)
    except LLMStreamError as e:
        m.error = f"LLM: {e}"
    finally:
        sentences.put(None)  # TTS 워커 종료 신호
        worker.join(timeout=180)

    m.reply = "".join(reply_parts)
    if llm_usage:
        m.extra["llm_usage"] = llm_usage
        if llm_usage.get("model"):
            m.llm_model = llm_usage["model"]  # moly-server가 알려준 실제 LLM 모델

    # 응답 음성 길이(pcm 기준)
    if tts.fmt == "pcm" and audio_chunks:
        sr = getattr(tts, "sample_rate", 24000)
        m.output_audio_sec = round(sum(len(c) for c in audio_chunks) / 2 / sr, 2)

    if audio_chunks:
        try:
            m.audio_out_path = _write_audio(cfg, tts, audio_chunks, m)
        except Exception as e:  # noqa: BLE001
            m.extra["audio_write_error"] = str(e)

    # 실측 비용: STT usage + LLM usage(모델별 단가) + TTS 문자수 — 모두 정확
    try:
        from .cost_model import cost_llm_from_usage, cost_stt_from_usage, cost_tts_from_chars
        c_stt = cost_stt_from_usage(m.extra["stt_usage"]) if m.extra.get("stt_usage") else 0.0
        c_llm = cost_llm_from_usage(llm_usage, m.llm_model) if llm_usage else 0.0
        c_tts = cost_tts_from_chars(len(m.reply or ""))
        m.extra["cost_stt"] = round(c_stt, 6)
        m.extra["cost_llm"] = round(c_llm, 6)
        m.extra["cost_tts"] = round(c_tts, 6)
        m.extra["cost_usd"] = round(c_stt + c_llm + c_tts, 6)
    except Exception as e:  # noqa: BLE001 — 비용 산정 실패가 측정을 깨면 안 됨
        m.extra["cost_error"] = str(e)
    return m


def _apply_fades(pcm: bytes, sr: int, fade_in_ms: float = 12.0, fade_out_ms: float = 20.0) -> bytes:
    """PCM16 시작/끝에 짧은 페이드 램프를 적용해 onset 클릭(팍 튀는 소리)을 제거한다."""
    if not pcm:
        return pcm
    a = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    n_in = min(len(a), int(sr * fade_in_ms / 1000))
    n_out = min(len(a), int(sr * fade_out_ms / 1000))
    if n_in > 0:
        a[:n_in] *= np.linspace(0.0, 1.0, n_in, dtype=np.float32)
    if n_out > 0:
        a[-n_out:] *= np.linspace(1.0, 0.0, n_out, dtype=np.float32)
    return np.clip(a, -32768, 32767).astype("<i2").tobytes()


def _write_audio(cfg: Config, tts: TTSProvider, chunks: list[bytes], m: TurnMetrics) -> str:
    # 조합별 폴더로 분리 → 여러 API 조합 결과가 충돌하지 않고 같은 샘플을 A/B 청취 가능.
    combo = combo_id(m.stt_model, m.llm_model, m.tts_model)
    raw = m.input_label.split("#")[0].split(":")[-1]  # 라벨 접두/반복접미 제거
    sample = slug(os.path.splitext(os.path.basename(raw))[0])
    out_d = os.path.join(cfg.out_dir, "audio", combo)
    os.makedirs(out_d, exist_ok=True)
    data = b"".join(chunks)

    if tts.fmt == "pcm":
        sr = getattr(tts, "sample_rate", 24000)
        data = _apply_fades(data, sr)  # 시작/끝 onset 클릭(팍 튀는 소리) 제거
        path = os.path.join(out_d, f"{sample}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(data)
        return path

    path = os.path.join(out_d, f"{sample}.{tts.fmt}")
    with open(path, "wb") as f:
        f.write(data)
    return path


def _audio_duration(path: str) -> "float | None":
    """ffprobe로 오디오 길이(초). 실패 시 None."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True,
        )
        return round(float(out.stdout.strip()), 2)
    except (ValueError, OSError):
        return None
