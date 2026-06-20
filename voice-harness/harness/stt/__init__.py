"""STT provider 팩토리 — STT_PROVIDER env로 선택(moly-server getProvider와 동일 패턴)."""
from __future__ import annotations

from ..config import Config
from .base import STTEvent, STTProvider


def get_stt_provider(cfg: Config) -> STTProvider:
    name = cfg.stt_provider
    if name == "openai":
        if not cfg.openai_api_key:
            raise RuntimeError("STT_PROVIDER=openai 인데 OPENAI_API_KEY가 없습니다. .env에 키를 주입하세요.")
        from .openai_stt import OpenAISTT
        return OpenAISTT(model=cfg.stt_model, api_key=cfg.openai_api_key)
    if name == "openai_realtime":
        if not cfg.openai_api_key:
            raise RuntimeError("STT_PROVIDER=openai_realtime 인데 OPENAI_API_KEY가 없습니다. .env에 키를 주입하세요.")
        from .openai_realtime_stt import OpenAIRealtimeSTT
        return OpenAIRealtimeSTT(model=cfg.stt_model, api_key=cfg.openai_api_key)
    if name == "mock":
        from .mock_stt import MockSTT
        return MockSTT()
    raise RuntimeError(f'알 수 없는 STT_PROVIDER: "{name}" (사용 가능: openai, openai_realtime, mock)')


__all__ = ["get_stt_provider", "STTEvent", "STTProvider"]
