"""ElevenLabs TTS — Flash v2.5 (저지연 스트리밍).

output_format=pcm_24000 → 우리 파이프라인(24kHz PCM)과 동일. 청크 스트리밍으로 첫 바이트를
빠르게 받는다. model_id로 Flash(저지연)/Multilingual v2(고품질) 전환.
"""
from __future__ import annotations

from typing import Iterator

import httpx

_SAMPLE_RATE = 24000
# 기본 보이스(Rachel). ELEVENLABS_VOICE_ID env로 교체 가능.
_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"


class ElevenLabsTTS:
    def __init__(
        self,
        api_key: str,
        model: str = "eleven_flash_v2_5",
        voice_id: str = _DEFAULT_VOICE,
    ) -> None:
        self.name = "elevenlabs"
        self.model = model
        self.voice = voice_id
        self.fmt = "pcm"
        self.sample_rate = _SAMPLE_RATE
        self._key = api_key

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}/stream"
        with httpx.stream(
            "POST",
            url,
            params={"output_format": "pcm_24000"},
            headers={"xi-api-key": self._key},
            json={"text": text, "model_id": self.model},
            timeout=60.0,
        ) as r:
            if r.status_code != 200:
                r.read()
                raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
            for chunk in r.iter_bytes(4096):
                if chunk:
                    yield chunk
