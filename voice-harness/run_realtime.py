"""Realtime 음성→음성 벤치마크 — Realtime 모델 하나로 음성 입력 → 음성 응답.

moly-server·TTS 미사용. STT→LLM→TTS 파이프라인의 최저지연 비교군.

사용:
  python run_realtime.py samples/test1.m4a
  REALTIME_VOICE=cedar python run_realtime.py samples/test1.m4a
  S2S_DEBUG=1 python run_realtime.py samples/test1.m4a    # 이벤트 타입 출력(디버그)
"""
from __future__ import annotations

import os
import sys

from harness.config import Config
from harness.cost_model import cost_realtime_from_usage
from harness.realtime_s2s import RealtimeS2S


def _ms(v):
    return "—" if v is None else f"{v * 1000:.0f}ms"


def main() -> int:
    if len(sys.argv) < 2:
        print("사용: python run_realtime.py <오디오파일> [...]", file=sys.stderr)
        return 2

    cfg = Config.load()
    if not cfg.openai_api_key:
        print("OPENAI_API_KEY가 없습니다. .env에 주입하세요.", file=sys.stderr)
        return 2

    model = (os.environ.get("REALTIME_MODEL") or "").strip()
    voice = (os.environ.get("REALTIME_VOICE") or "").strip()
    debug = os.environ.get("S2S_DEBUG", "") not in ("", "0", "false")

    s2s = RealtimeS2S(api_key=cfg.openai_api_key, model=model, voice=voice, debug=debug)
    print(f"Realtime 음성→음성  model={s2s.model}  voice={s2s.voice}  (moly-server·TTS 미사용)")
    print("=" * 60)

    rc = 0
    for path in sys.argv[1:]:
        r = s2s.converse(path, out_dir=cfg.out_dir)
        print(f"  입력 : {path}")
        print(f"  응답(텍스트) : {r.transcript_out!r}")
        print(f"  ⏱  체감 지연 (말 멈춤 → 첫 응답 음성) :  {_ms(r.latency)}")
        print(f"  (상세) 턴 총시간 {_ms(r.total)}")
        usage = r.extra.get("usage")
        if usage:
            try:
                cb = cost_realtime_from_usage(usage)
                parts = " · ".join(f"{k} ${v:.4f}" for k, v in cb.lines.items() if v > 0)
                print(f"  💲 이 턴 실측 비용: ${cb.total_usd:.4f}  ({parts})")
            except Exception as e:  # noqa: BLE001 — 비용 출력 실패가 벤치를 깨면 안 됨
                print(f"  💲 비용 계산 건너뜀: {e}")
        if r.audio_out_path:
            print(f"  🔊 응답 음성: {r.audio_out_path}")
        if r.error:
            print(f"  ⚠️ 오류: {r.error}")
            rc = 1
        print("-" * 60)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
