"""파일 입력 벤치마크 러너.

오디오 파일(들)을 STT→LLM→TTS 파이프라인에 태워 단계별 지연을 측정/기록한다.
동일 입력으로 API 조합(.env)을 바꿔가며 돌리면 metrics.csv에 누적되어 비교표가 된다.

사용:
  python run_file.py samples/q1.wav
  python run_file.py samples/*.wav --repeat 3
  python run_file.py samples/q1.wav --label "openai-baseline"
"""
from __future__ import annotations

import argparse
import sys

from harness.config import Config
from harness.metrics import write_outputs
from harness.pipeline import run_turn
from harness.stt import get_stt_provider
from harness.tts import get_tts_provider


def main() -> int:
    ap = argparse.ArgumentParser(description="STT→LLM→TTS 파일 벤치마크")
    ap.add_argument("audio", nargs="+", help="오디오 파일 경로(여러 개 가능)")
    ap.add_argument("--repeat", type=int, default=1, help="각 파일 반복 횟수")
    ap.add_argument("--label", default="", help="입력 라벨 접두사(비교용)")
    args = ap.parse_args()

    cfg = Config.load()
    try:
        stt = get_stt_provider(cfg)
        tts = get_tts_provider(cfg)
    except RuntimeError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2

    print(f"STT={stt.name}:{stt.model}  TTS={tts.name}:{tts.model}  server={cfg.moly_server_url}")
    print("=" * 64)

    any_error = False
    for path in args.audio:
        for r in range(args.repeat):
            label = f"{args.label}:{path}" if args.label else path
            if args.repeat > 1:
                label += f"#{r + 1}"
            m = run_turn(cfg, stt, tts, audio_path=path, input_label=label)
            print("\n".join(m.summary_lines()))
            if "cost_usd" in m.extra:
                cl = m.extra.get("cost_lines", {})
                parts = " · ".join(f"{k} ${v:.4f}" for k, v in cl.items())
                print(f"  💲 부분 실측 비용: ${m.extra['cost_usd']:.4f}  ({parts})")
            if m.error:
                any_error = True
                print(f"  ⚠️ 오류: {m.error}")
            if m.audio_out_path:
                print(f"  🔊 합성 오디오: {m.audio_out_path}")
            jp, cp = write_outputs(m, cfg.out_dir)
            print(f"  📄 {jp}  |  CSV: {cp}")
            print("-" * 64)

    return 1 if any_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
