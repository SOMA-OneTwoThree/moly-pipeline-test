"""STT 단독 벤치마크 — LLM·TTS 없이 전사 단계만 측정한다.

실시간 STT의 순수 지연(첫 delta, 발화종료→확정 꼬리지연, 전체)을 격리해서 본다.
LLM/TTS 변동에 가려지지 않으므로 STT 자체 성능·API 비교에 적합.

사용:
  python run_stt.py samples/test1.m4a
  python run_stt.py samples/*.wav --repeat 3
  STT_MODEL=gpt-4o-mini-transcribe python run_stt.py samples/test1.m4a   # 더 빠른 모델 비교
"""
from __future__ import annotations

import argparse
import sys

from harness.config import Config
from harness.metrics import now
from harness.stt import get_stt_provider


def _ms(a, b):
    return "  —  " if (a is None or b is None) else f"{(b - a) * 1000:6.0f}ms"


def run_one(cfg, stt, path: str) -> bool:
    t_start = now()
    t_first = t_audio_end = t_done = None
    transcript = ""
    err = ""
    try:
        for ev in stt.transcribe_stream(path, language=cfg.stt_language):
            at = getattr(ev, "at", None) or now()
            if getattr(ev, "audio_end", False):
                t_audio_end = at  # 마지막(턴 종료)이 이김
            elif ev.final:
                transcript = ev.text
                t_done = at
            elif t_first is None:
                t_first = at
    except Exception as e:  # noqa: BLE001
        err = str(e)

    print(f"  입력      : {path}")
    print(f"  전사      : {transcript!r}")
    print(f"  STT 첫delta: {_ms(t_start, t_first)}   STT 확정(요청→완료): {_ms(t_start, t_done)}")
    if t_audio_end is not None:
        print(f"  ⚡ 발화종료→확정(꼬리지연): {_ms(t_audio_end, t_done)}")
    if err:
        print(f"  ⚠️ 오류: {err}")
    print("-" * 56)
    return not err


def main() -> int:
    ap = argparse.ArgumentParser(description="STT 단독 벤치마크")
    ap.add_argument("audio", nargs="+")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()

    cfg = Config.load()
    try:
        stt = get_stt_provider(cfg)
    except RuntimeError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2

    print(f"STT={stt.name}:{stt.model}   (LLM·TTS 미사용 — 전사 단계만)")
    print("=" * 56)
    ok = True
    for path in args.audio:
        for _ in range(args.repeat):
            ok = run_one(cfg, stt, path) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
