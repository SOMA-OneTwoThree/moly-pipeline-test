"""STS 버전 비교 러너 — 같은 입력으로 여러 OpenAI Realtime 모델의 응답 지연을 측정·비교.

기존 RealtimeS2S(측정 로직)를 그대로 재사용하고, [모델 × 반복]을 돌려
모델별 중앙값 비교표 + runs/s2s_metrics.csv 를 남긴다. 측정 정의는 run_realtime.py와 동일:
  체감 지연 = 말 멈춤(t_audio_end) → 첫 응답 음성(t_first_audio_out)
  턴 총시간 = 입력 시작(t_start) → 응답 전체 합성 완료(t_done)

사용:
  python run_s2s_compare.py samples/test2.m4a
  python run_s2s_compare.py samples/test2.m4a --repeat 5
  python run_s2s_compare.py samples/test2.m4a --models gpt-realtime,gpt-realtime-1.5,gpt-realtime-2
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import time

from harness.config import Config
from harness.cost_model import cost_realtime_from_usage
from harness.realtime_s2s import RealtimeS2S

DEFAULT_MODELS = ["gpt-realtime", "gpt-realtime-1.5", "gpt-realtime-2"]


def _ms(v) -> str:
    return "—" if v is None else f"{v * 1000:.0f}ms"


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _cost_of(r) -> "float | None":
    """실측/추정 비용(USD). EL은 extra['cost_usd'](분당 추정)를 직접 채우므로 우선 사용,
    그 외(OpenAI)는 extra['usage'] 토큰으로 계산."""
    if r.extra.get("cost_usd") is not None:
        return r.extra["cost_usd"]
    usage = r.extra.get("usage")
    if not usage:
        return None
    try:
        return cost_realtime_from_usage(usage).total_usd
    except Exception:  # noqa: BLE001 — 비용 계산 실패가 측정을 깨면 안 됨
        return None


_EL_ALIASES = ("elevenlabs", "11labs", "el")


def _make_s2s(cfg, model: str, voice: str, effort: str):
    """모델명으로 provider 분기. EL이면 ElevenLabsS2S, 그 외 OpenAI RealtimeS2S."""
    if model.strip().lower() in _EL_ALIASES:
        if not cfg.elevenlabs_api_key or not cfg.elevenlabs_agent_id:
            raise RuntimeError(
                "elevenlabs 측정엔 ELEVENLABS_API_KEY와 ELEVENLABS_AGENT_ID가 필요합니다(.env).")
        from harness.elevenlabs_s2s import ElevenLabsS2S
        return ElevenLabsS2S(api_key=cfg.elevenlabs_api_key, agent_id=cfg.elevenlabs_agent_id)
    return RealtimeS2S(api_key=cfg.openai_api_key, model=model, voice=voice, effort=effort)


def main() -> int:
    ap = argparse.ArgumentParser(description="STS 모델 버전 지연 비교 (같은 입력, 중앙값)")
    ap.add_argument("audio", help="입력 오디오 파일(모든 모델 공통)")
    ap.add_argument("--repeat", type=int, default=5, help="모델당 반복 횟수(중앙값용, 기본 5)")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS), help="쉼표구분 모델 목록")
    ap.add_argument("--voice", default="", help="voice 고정(기본: 모델 기본값 marin)")
    ap.add_argument("--effort", default="", help="reasoning effort(minimal|low|medium|high|xhigh) — gpt-realtime-2 등 추론 모델용")
    args = ap.parse_args()

    cfg = Config.load()
    if not os.path.exists(args.audio):
        print(f"입력 파일 없음: {args.audio}", file=sys.stderr)
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    # OpenAI 키는 OpenAI 모델이 목록에 있을 때만 필요(EL 전용 실행 허용)
    needs_openai = any(m.lower() not in _EL_ALIASES for m in models)
    if needs_openai and not cfg.openai_api_key:
        print("OPENAI_API_KEY가 없습니다. voice-harness/.env에 주입하세요.", file=sys.stderr)
        return 2
    eff = f"  |  effort={args.effort}" if args.effort else ""
    print(f"입력: {args.audio}  |  모델 {len(models)}개 × {args.repeat}회{eff}  (S2S · moly-server 미사용)")
    print("=" * 72)

    run_ts = time.strftime("%Y%m%d-%H%M%S")
    rows: list[dict] = []        # 개별 실행 행(CSV용)
    summary: list[tuple] = []    # (model, [latency], [total], [cost], errors)

    for model in models:
        lat: list = []
        tot: list = []
        cost: list = []
        errors = 0
        try:
            s2s = _make_s2s(cfg, model, args.voice, args.effort)
        except RuntimeError as e:
            print(f"\n▶ {model}\n  ⚠️ {e} — 건너뜀")
            summary.append((model, [], [], [], args.repeat))
            continue
        print(f"\n▶ {model}")
        for i in range(args.repeat):
            r = s2s.converse(args.audio, out_dir=cfg.out_dir)
            c = _cost_of(r)
            if r.error:
                errors += 1
                print(f"  #{i + 1}: ⚠️ {r.error}")
            else:
                lat.append(r.latency)
                tot.append(r.total)
                if c is not None:
                    cost.append(c)
                cstr = f"${c:.4f}" if c is not None else "비용—"
                print(f"  #{i + 1}: 체감 {_ms(r.latency)} · 턴총 {_ms(r.total)} · {cstr}")
            rows.append({
                "run_ts": run_ts,
                "input": args.audio,
                "model": model,
                "rep": i + 1,
                "latency_ms": "" if r.latency is None else round(r.latency * 1000),
                "total_ms": "" if r.total is None else round(r.total * 1000),
                "cost_usd": "" if c is None else round(c, 6),
                "error": r.error,
            })
        summary.append((model, lat, tot, cost, errors))

    # --- 비교표(중앙값) ---
    print("\n" + "=" * 72)
    print("📊 모델별 비교 (중앙값)")
    print(f"{'모델':<22}{'성공/시도':>10}{'체감지연':>12}{'턴총시간':>14}{'비용':>12}")
    print("-" * 72)
    for model, lat, tot, cost, errors in summary:
        ok = len(lat)
        mcost = _med(cost)
        cstr = f"${mcost:.4f}" if mcost is not None else "—"
        print(f"{model:<22}{f'{ok}/{ok + errors}':>10}{_ms(_med(lat)):>12}{_ms(_med(tot)):>14}{cstr:>12}")
    print("-" * 72)
    print("체감지연 = 말 멈춤 → 첫 응답 음성 · 턴총시간 = 입력 → 응답 전체 합성 완료 (모두 중앙값)")
    print("주의: gpt-realtime-2는 reasoning effort에 따라 지연이 달라질 수 있음(현재 모델 기본값).")

    # --- CSV 누적(개별 실행 단위) ---
    csv_path = os.path.join(cfg.out_dir, "s2s_metrics.csv")
    _append_csv(csv_path, rows)
    print(f"\n📄 개별 실행 기록: {csv_path}")
    return 0


def _append_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fields = ["run_ts", "input", "model", "rep", "latency_ms", "total_ms", "cost_usd", "error"]
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow(r)


if __name__ == "__main__":
    raise SystemExit(main())
