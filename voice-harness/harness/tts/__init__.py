"""TTS provider 팩토리 — TTS_PROVIDER env로 선택."""
from __future__ import annotations

import os

from ..config import Config
from .base import TTSProvider


def get_tts_provider(cfg: Config) -> TTSProvider:
    name = cfg.tts_provider
    if name == "openai":
        if not cfg.openai_api_key:
            raise RuntimeError("TTS_PROVIDER=openai 인데 OPENAI_API_KEY가 없습니다. .env에 키를 주입하세요.")
        from .openai_tts import OpenAITTS
        return OpenAITTS(
            model=cfg.tts_model,
            voice=cfg.tts_voice,
            fmt=cfg.tts_format,
            speed=cfg.tts_speed,
            api_key=cfg.openai_api_key,
        )
    if name == "elevenlabs":
        if not cfg.elevenlabs_api_key:
            raise RuntimeError("TTS_PROVIDER=elevenlabs 인데 ELEVENLABS_API_KEY가 없습니다.")
        from .elevenlabs_tts import ElevenLabsTTS, _DEFAULT_VOICE
        model = cfg.tts_model if cfg.tts_model.startswith("eleven") else "eleven_flash_v2_5"
        voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or _DEFAULT_VOICE
        return ElevenLabsTTS(api_key=cfg.elevenlabs_api_key, model=model, voice_id=voice)
    if name == "mock":
        from .mock_tts import MockTTS
        return MockTTS()
    raise RuntimeError(f'알 수 없는 TTS_PROVIDER: "{name}" (사용 가능: openai, elevenlabs, mock)')


__all__ = ["get_tts_provider", "TTSProvider"]
