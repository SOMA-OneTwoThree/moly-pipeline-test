"""metrics.csv 집계 — API 조합별 지연 분위수(p50/p90/p99)·비용·성공률.

음성 대화는 매 요청의 일관성이 중요하므로 평균이 아닌 분위수(특히 p90/p99)로 본다.
같은 입력을 여러 조합으로 --repeat 돌려 metrics.csv에 쌓은 뒤 이걸로 비교한다.

사용:
  python aggregate.py                 # runs/metrics.csv 요약
  python aggregate.py runs/metrics.csv
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict

import numpy as np

# 분위수로 볼 지연 지표(초 단위 컬럼) — 표시는 ms
_LAT = [
    ("e2e_speech_end", "E2E(발화종료→첫음성)"),
    ("end_to_end", "E2E(입력→첫음성)"),
    ("stt_tail", "STT 꼬리"),
    ("llm_ttft", "LLM TTFT"),
    ("first_sentence_latency", "LLM 첫문장"),
    ("tts_first_audio_latency", "TTS 첫소리"),
]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pct(vals, p):
    return float(np.percentile(vals, p)) if vals else None


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "runs/metrics.csv"
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        print(f"파일 없음: {path}", file=sys.stderr)
        return 1
    if not rows:
        print("데이터 없음")
        return 0

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = (r.get("stt_model", ""), r.get("llm_model", ""), r.get("tts_model", ""))
        groups[key].append(r)

    print(f"metrics.csv: {len(rows)}행 · 조합 {len(groups)}개\n" + "=" * 72)
    for (stt, llm, tts), rs in sorted(groups.items()):
        n = len(rs)
        ok = sum(1 for r in rs if r.get("success") in ("1", "True", "true"))
        costs = [c for c in (_f(r.get("cost_usd")) for r in rs) if c is not None]
        tps = [t for t in (_f(r.get("tokens_per_sec")) for r in rs) if t is not None]
        print(f"\n▸ STT={stt}  LLM={llm}  TTS={tts}")
        print(f"  n={n}  성공률={ok}/{n}  평균비용=${np.mean(costs):.5f}" if costs
              else f"  n={n}  성공률={ok}/{n}")
        if tps:
            print(f"  tokens/sec  p50 {np.median(tps):.0f} · p90 {_pct(tps, 90):.0f}")
        print(f"  {'지표':<22}{'p50':>9}{'p90':>9}{'p99':>9}   (n)")
        for col, label in _LAT:
            vals = [v for v in (_f(r.get(col)) for r in rs) if v is not None]
            if not vals:
                continue
            print(f"  {label:<22}{_pct(vals,50)*1000:>7.0f}ms{_pct(vals,90)*1000:>7.0f}ms"
                  f"{_pct(vals,99)*1000:>7.0f}ms   ({len(vals)})")
    print("\n" + "=" * 72)
    print("※ p90/p99는 표본이 적으면 보간값 — 조합당 20~50회 반복 권장")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
