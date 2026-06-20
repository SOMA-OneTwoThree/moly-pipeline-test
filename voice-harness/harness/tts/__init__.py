"""TTS provider 팩토리 — TTS_PROVIDER env로 선택."""
from __future__ import annotations

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
    if name == "mock":
        from .mock_tts import MockTTS
        return MockTTS()
    raise RuntimeError(f'알 수 없는 TTS_PROVIDER: "{name}" (사용 가능: openai, mock)')


__all__ = ["get_tts_provider", "TTSProvider"]
