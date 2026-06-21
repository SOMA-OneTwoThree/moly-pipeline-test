"""환경설정 로드 — .env 또는 OS 환경변수에서 읽는다."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _parse_float(s: str, default: float) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    moly_server_url: str
    stt_provider: str
    tts_provider: str
    openai_api_key: str
    elevenlabs_api_key: str
    elevenlabs_agent_id: str
    stt_model: str
    stt_language: str
    tts_model: str
    tts_voice: str
    tts_format: str
    tts_speed: float
    out_dir: str

    @staticmethod
    def load() -> "Config":
        return Config(
            moly_server_url=_env("MOLY_SERVER_URL", "http://localhost:3000/api/chat"),
            stt_provider=_env("STT_PROVIDER", "openai").lower(),
            tts_provider=_env("TTS_PROVIDER", "openai").lower(),
            openai_api_key=_env("OPENAI_API_KEY"),
            elevenlabs_api_key=_env("ELEVENLABS_API_KEY"),
            elevenlabs_agent_id=_env("ELEVENLABS_AGENT_ID"),
            stt_model=_env("STT_MODEL", "gpt-4o-transcribe"),
            stt_language=_env("STT_LANGUAGE", ""),
            tts_model=_env("TTS_MODEL", "gpt-4o-mini-tts"),
            tts_voice=_env("TTS_VOICE", "coral"),
            tts_format=_env("TTS_FORMAT", "pcm").lower(),
            tts_speed=_parse_float(_env("TTS_SPEED", "1.0"), 1.0),
            out_dir=_env("OUT_DIR", "runs"),
        )
