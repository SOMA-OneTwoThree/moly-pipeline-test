"""두 음성 아키텍처의 분당 비용 추정 — 파이프라인(STT→LLM→TTS) vs 실시간 S2S.

지연(latency)은 metrics.py가 측정한다. 이 모듈은 *비용*만 다룬다.

모든 단가는 2026년 6월 공식 페이지 기준(아래 SOURCES 참고). 일부 항목은
근사/3자 출처이며 dataclass 주석에 ⚠로 표시했다. 단가가 바뀌면 RATES만 고치면 된다.

사용:
    python -m harness.cost_model                 # 기본 시나리오 표
    python -m harness.cost_model --minutes 10 --turns 20 --cache-hit 0.9
    python -m harness.cost_model --sweep out.csv # 턴×캐시 그리드 → CSV
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, asdict, replace


# ─────────────────────────────────────────────────────────────────────────────
# 검증된 단가 (2026-06). 출처는 파일 하단 SOURCES.
# 단위 표기: per-MTok = USD / 1,000,000 토큰 ; per-Mchar = USD / 1,000,000 문자.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Rates:
    # --- OpenAI STT (gpt-4o-mini-transcribe) ---
    stt_per_min: float = 0.003          # OpenAI 공식 "estimated cost" / 분 (추정용)
    stt_in_per_mtok: float = 1.25       # 오디오 입력 토큰 (실측 usage 기반 정확비용용)
    stt_out_per_mtok: float = 5.0       # 텍스트 출력 토큰

    # --- OpenAI TTS (tts-1) ---
    tts_per_mchar: float = 15.0         # $15 / 1M 문자 (공식, verbatim)

    # --- Anthropic Claude Sonnet 4.6 ---
    llm_in_per_mtok: float = 3.0        # 입력
    llm_out_per_mtok: float = 15.0      # 출력
    llm_cache_read_per_mtok: float = 0.30   # 캐시 히트(0.1x 입력)

    # --- OpenAI gpt-realtime (GA, 2025-08) ---
    rt_audio_in_per_mtok: float = 32.0
    rt_audio_out_per_mtok: float = 64.0
    rt_audio_cache_in_per_mtok: float = 0.40
    rt_text_in_per_mtok: float = 4.0    # gpt-realtime GA 텍스트 입력
    rt_text_out_per_mtok: float = 16.0  # ⚠ 모델 페이지 $16 vs 인덱스(gpt-realtime-2) $24 불일치. GA면 $16.

    # --- 오디오 토큰↔시간 매핑 (⚠ 커뮤니티 측정값, 공식 verbatim 아님) ---
    rt_in_tok_per_min: float = 600.0    # 사용자 오디오 ≈ 1 tok / 100ms
    rt_out_tok_per_min: float = 1200.0  # 어시스턴트 오디오 ≈ 1 tok / 50ms

    # --- 발화량 환산 (조정 가능한 가정) ---
    text_tok_per_min_speech: float = 190.0   # ~150 wpm ≈ 190 tok/분
    chars_per_min_speech: float = 900.0      # ~900 자/분


@dataclass
class Scenario:
    minutes: float = 10.0      # 대화 wall-clock 분
    user_frac: float = 0.5     # 사용자가 말하는 시간 비율
    turns: int = 20            # 턴 수 (컨텍스트/오디오 재청구 횟수)
    llm_context_tok: int = 500 # 턴마다 실리는 컨텍스트(히스토리+시스템)
    cache_hit: float = 0.0     # 재청구분 캐시 히트율 0~1
    rebill: str = "linear"     # 실시간 오디오 재청구 모델: "linear"(보수) | "quadratic"(최악)

    @property
    def asst_frac(self) -> float:
        return max(0.0, 1.0 - self.user_frac)


@dataclass
class Breakdown:
    arch: str
    total_usd: float
    per_min_usd: float
    lines: dict  # 항목별 비용

    def render(self) -> str:
        out = [f"[{self.arch}]  총 ${self.total_usd:.4f}  ·  분당 ${self.per_min_usd:.4f}"]
        for k, v in self.lines.items():
            out.append(f"    {k:<22} ${v:.4f}")
        return "\n".join(out)


def _blend(base: float, cached: float, hit: float) -> float:
    """캐시 히트율을 반영한 실효 단가."""
    return (1.0 - hit) * base + hit * cached


def cost_pipeline(s: Scenario, r: Rates = Rates()) -> Breakdown:
    """STT(gpt-4o-mini-transcribe) → LLM(Sonnet 4.6) → TTS(tts-1)."""
    user_min = s.minutes * s.user_frac
    asst_min = s.minutes * s.asst_frac

    stt = user_min * r.stt_per_min

    # LLM 입력: 턴마다 컨텍스트 재청구. 캐시 히트율 반영.
    ctx_tok = s.turns * s.llm_context_tok
    llm_in_rate = _blend(r.llm_in_per_mtok, r.llm_cache_read_per_mtok, s.cache_hit)
    llm_in = ctx_tok * llm_in_rate / 1e6

    # LLM 출력 ≈ 어시스턴트가 말한 분량의 텍스트.
    llm_out_tok = asst_min * r.text_tok_per_min_speech
    llm_out = llm_out_tok * r.llm_out_per_mtok / 1e6

    tts = asst_min * r.chars_per_min_speech * r.tts_per_mchar / 1e6

    lines = {
        "STT (mini-transcribe)": stt,
        "LLM 입력 (Sonnet)": llm_in,
        "LLM 출력 (Sonnet)": llm_out,
        "TTS (tts-1)": tts,
    }
    total = sum(lines.values())
    return Breakdown("Pipeline  STT→LLM→TTS", total, total / s.minutes, lines)


def cost_realtime(s: Scenario, r: Rates = Rates()) -> Breakdown:
    """gpt-realtime 음성-대-음성.

    핵심: 오디오 입력은 턴마다 *누적 히스토리 전체*가 재청구된다(캐시 없으면 ~T²).
    여기선 단순화해 'turns × 평균 누적 오디오'로 근사한다.
    """
    user_min = s.minutes * s.user_frac
    asst_min = s.minutes * s.asst_frac

    # 한 대화에서 사용자가 만든 총 오디오 입력 토큰.
    base_in_tok = user_min * r.rt_in_tok_per_min
    # 재청구 증폭 계수. base_in_tok = 대화 전체 사용자 오디오를 T턴에 균등분배(턴당 base/T).
    #   linear    : 턴 k에서 그 턴 분량만 청구 → Σ = base. 평균적으로는 누적의 절반,
    #               즉 (T+1)/2 배로 근사(보수적 기본값, 과대추정 방지).
    #   quadratic : 턴 k에서 *그때까지 누적분 전체*(k·base/T) 재전송 →
    #               청구 = (base/T)·Σ_{k=1..T} k = base·(T+1)/2 … 가 아니라
    #               누적이 안 줄어드는 최악(히스토리 trim 없음): 매 턴 전체 base → base·T 배.
    # 캐시 적용분은 $0.40/MTok로 사실상 소멸.
    amplification = float(s.turns) if s.rebill == "quadratic" else (s.turns + 1) / 2.0
    rebilled_tok = base_in_tok * amplification
    in_rate = _blend(r.rt_audio_in_per_mtok, r.rt_audio_cache_in_per_mtok, s.cache_hit)
    audio_in = rebilled_tok * in_rate / 1e6

    # 오디오 출력은 선형, 재청구 없음.
    out_tok = asst_min * r.rt_out_tok_per_min
    audio_out = out_tok * r.rt_audio_out_per_mtok / 1e6

    lines = {
        "오디오 입력 (재청구)": audio_in,
        "오디오 출력": audio_out,
    }
    total = sum(lines.values())
    return Breakdown("Realtime  gpt-realtime S2S", total, total / s.minutes, lines)


def cost_realtime_from_usage(usage: dict, r: Rates = Rates()) -> Breakdown:
    """gpt-realtime의 실제 response.usage(토큰 실측)로 *정확* 비용 계산 — 추정 아님.

    usage 형태(OpenAI Realtime response.done):
      {input_tokens, output_tokens,
       input_token_details:  {audio, text, cached_tokens 또는 cached_tokens_details{audio,text}},
       output_token_details: {audio, text}}
    필드명은 모델/버전에 따라 다를 수 있어 방어적으로 파싱한다. 1턴 실측이므로 재청구 증폭 없음.
    """
    itd = usage.get("input_token_details", {}) or {}
    otd = usage.get("output_token_details", {}) or {}

    in_audio = itd.get("audio_tokens", itd.get("audio", 0)) or 0
    in_text = itd.get("text_tokens", itd.get("text", 0)) or 0

    # 캐시 토큰: 평면(cached_tokens) 또는 중첩(cached_tokens_details{audio,text}) 둘 다 허용.
    cd = itd.get("cached_tokens_details", {}) or {}
    cached_audio = cd.get("audio", 0) or 0
    cached_text = cd.get("text", 0) or 0
    flat_cached = itd.get("cached_tokens", 0) or 0
    if flat_cached and not (cached_audio or cached_text):
        cached_audio = flat_cached  # 분해 불가 시 오디오로 귀속(보수적: 오디오가 더 비쌈)

    # 캐시분은 비캐시 오디오/텍스트에서 차감해 이중계산 방지.
    paid_in_audio = max(0, in_audio - cached_audio)
    paid_in_text = max(0, in_text - cached_text)

    out_audio = otd.get("audio_tokens", otd.get("audio", 0)) or 0
    out_text = otd.get("text_tokens", otd.get("text", 0)) or 0

    lines = {
        "오디오 입력": paid_in_audio * r.rt_audio_in_per_mtok / 1e6,
        "텍스트 입력": paid_in_text * r.rt_text_in_per_mtok / 1e6,
        "캐시 입력": (cached_audio + cached_text) * r.rt_audio_cache_in_per_mtok / 1e6,
        "오디오 출력": out_audio * r.rt_audio_out_per_mtok / 1e6,
        "텍스트 출력": out_text * r.rt_text_out_per_mtok / 1e6,
    }
    total = sum(lines.values())
    # 실측 1턴이라 분당 환산 기준이 없음 → per_min = total(턴 단가). 호출측에서 분으로 나눠 쓰면 됨.
    return Breakdown("Realtime  gpt-realtime (실측 usage)", total, total, lines)


def cost_stt_from_usage(usage: dict, r: Rates = Rates()) -> float:
    """gpt-4o(-mini)-transcribe의 transcript.text.done usage로 정확 STT 비용(USD).

    usage: {input_tokens(오디오), output_tokens(텍스트), ...}. 단가는 mini 기준 가정
    (Rates.stt_*_per_mtok). 없으면 0.
    """
    in_tok = usage.get("input_tokens", 0) or 0
    out_tok = usage.get("output_tokens", 0) or 0
    return (in_tok * r.stt_in_per_mtok + out_tok * r.stt_out_per_mtok) / 1e6


def cost_tts_from_chars(n_chars: int, r: Rates = Rates()) -> float:
    """tts-1 정확 비용(USD) — 입력 문자수 × $/1M자. TTS는 usage 미제공이라 문자수가 정답."""
    return max(0, n_chars) * r.tts_per_mchar / 1e6


def cost_pipeline_measured(stt_usage: dict | None, reply_text: str,
                           r: Rates = Rates()) -> Breakdown:
    """파이프라인 실측 비용. STT=usage(있으면 정확), TTS=문자수(정확), LLM=미측정(0, ⚠).

    moly-server가 토큰 usage를 안 주므로 LLM 라인은 0이며 별도 표기. STT usage가 없으면
    STT도 0(러너에서 추정으로 보완 가능).
    """
    stt = cost_stt_from_usage(stt_usage, r) if stt_usage else 0.0
    tts = cost_tts_from_chars(len(reply_text or ""), r)
    lines = {
        "STT (usage 실측)": stt,
        "LLM (미측정·moly-server)": 0.0,
        "TTS (문자수 실측)": tts,
    }
    total = sum(lines.values())
    return Breakdown("Pipeline  (부분 실측: STT+TTS)", total, total, lines)


def compare(s: Scenario, r: Rates = Rates()) -> dict:
    a = cost_pipeline(s, r)
    b = cost_realtime(s, r)
    ratio = (b.per_min_usd / a.per_min_usd) if a.per_min_usd else float("inf")
    return {"scenario": asdict(s), "pipeline": a, "realtime": b, "realtime_vs_pipeline_x": round(ratio, 2)}


def sweep_csv(path: str, base: Scenario, r: Rates = Rates(),
              turns_grid=(1, 2, 5, 10, 20, 50, 100),
              cache_grid=(0.0, 0.5, 0.9),
              rebill_grid=("linear", "quadratic")) -> int:
    """턴수 × 캐시히트율 × 재청구모델 그리드를 돌려 CSV로 기록. 행 수 반환."""
    fields = [
        "minutes", "user_frac", "turns", "cache_hit", "rebill",
        "pipeline_per_min", "realtime_per_min", "realtime_vs_pipeline_x",
        "pipeline_total", "realtime_total",
    ]
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for rb in rebill_grid:
            for t in turns_grid:
                for ch in cache_grid:
                    s = replace(base, turns=t, cache_hit=ch, rebill=rb)
                    res = compare(s, r)
                    a, b = res["pipeline"], res["realtime"]
                    w.writerow({
                        "minutes": s.minutes, "user_frac": s.user_frac,
                        "turns": t, "cache_hit": ch, "rebill": rb,
                        "pipeline_per_min": round(a.per_min_usd, 5),
                        "realtime_per_min": round(b.per_min_usd, 5),
                        "realtime_vs_pipeline_x": res["realtime_vs_pipeline_x"],
                        "pipeline_total": round(a.total_usd, 5),
                        "realtime_total": round(b.total_usd, 5),
                    })
                    n += 1
    return n


def plot_png(path: str, base: Scenario, r: Rates = Rates(),
             turns_grid=(1, 2, 5, 10, 20, 50, 100)) -> str:
    """cost/min vs 턴수 그래프. 파이프라인(평탄 기준선) + 실시간 4곡선(재청구×캐시).

    matplotlib은 여기서만 지연 임포트 — 코어 도구는 의존성 없이 동작.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # 헤드리스
        import matplotlib.pyplot as plt
    except ImportError as e:
        raise SystemExit("matplotlib 필요: pip install matplotlib") from e

    ts = list(turns_grid)
    fig, ax = plt.subplots(figsize=(8, 5))

    # 파이프라인: 턴에 거의 무관 — 대표선 하나(캐시 90%).
    pipe = [cost_pipeline(replace(base, turns=t, cache_hit=0.9)).per_min_usd for t in ts]
    ax.plot(ts, pipe, "o-", color="#2a7", lw=2, label="Pipeline (cache 90%)")

    styles = {("linear", 0.0): "--", ("linear", 0.9): "-",
              ("quadratic", 0.0): ":", ("quadratic", 0.9): "-."}
    for (rb, ch), ls in styles.items():
        ys = [cost_realtime(replace(base, turns=t, cache_hit=ch, rebill=rb)).per_min_usd for t in ts]
        ax.plot(ts, ys, ls, color="#c44", lw=1.8,
                label=f"Realtime {rb} · cache {ch:.0%}")

    ax.set_xscale("log")
    ax.set_xticks(ts); ax.set_xticklabels([str(t) for t in ts])
    # 라벨은 영문 — 헤드리스 DejaVu Sans에 한글 글리프가 없어 박스로 깨짐.
    ax.set_xlabel("Turns")
    ax.set_ylabel("Cost per minute (USD/min)")
    ax.set_title(f"Voice architecture cost/min — {base.minutes:.0f}-min call, "
                 f"user talk {base.user_frac:.0%}  (rates: 2026-06)")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _main() -> None:
    p = argparse.ArgumentParser(description="음성 아키텍처 분당 비용 추정 (2026-06 단가)")
    p.add_argument("--minutes", type=float, default=10.0)
    p.add_argument("--user-frac", type=float, default=0.5, help="사용자 발화 시간 비율")
    p.add_argument("--turns", type=int, default=20)
    p.add_argument("--context-tok", type=int, default=500, help="턴당 LLM 컨텍스트 토큰")
    p.add_argument("--cache-hit", type=float, default=0.0, help="재청구분 캐시 히트율 0~1")
    p.add_argument("--rebill", choices=["linear", "quadratic"], default="linear",
                   help="실시간 오디오 재청구 모델: linear(보수, 기본) | quadratic(최악치)")
    p.add_argument("--sweep", metavar="CSV",
                   help="턴×캐시×재청구 그리드를 돌려 지정 CSV로 저장하고 종료")
    p.add_argument("--plot", metavar="PNG",
                   help="cost/min vs 턴수 그래프를 PNG로 저장하고 종료 (matplotlib 필요)")
    a = p.parse_args()

    s = Scenario(
        minutes=a.minutes, user_frac=a.user_frac, turns=a.turns,
        llm_context_tok=a.context_tok, cache_hit=a.cache_hit, rebill=a.rebill,
    )

    if a.sweep:
        n = sweep_csv(a.sweep, s)
        print(f"스윕 {n}행 → {a.sweep}  (고정: {a.minutes}분 · 사용자발화 {a.user_frac:.0%} · "
              f"컨텍스트 {a.context_tok}tok/턴)")
        return

    if a.plot:
        out = plot_png(a.plot, s)
        print(f"그래프 → {out}")
        return

    res = compare(s)
    print(f"시나리오: {a.minutes}분 · {a.turns}턴 · 사용자발화 {a.user_frac:.0%} · "
          f"캐시히트 {a.cache_hit:.0%} · 컨텍스트 {a.context_tok}tok/턴 · 재청구 {a.rebill}\n")
    print(res["pipeline"].render())
    print()
    print(res["realtime"].render())
    print(f"\n→ 실시간이 파이프라인의 약 {res['realtime_vs_pipeline_x']}배")


if __name__ == "__main__":
    _main()


# ─────────────────────────────────────────────────────────────────────────────
# SOURCES (검증: 2026-06)
#   STT gpt-4o-mini-transcribe : $1.25/$5.00 per MTok · ~$0.003/분
#       https://developers.openai.com/api/docs/pricing
#   TTS tts-1                  : $15.00 / 1M 문자
#       https://developers.openai.com/api/docs/models/tts-1
#   gpt-realtime (GA 2025-08)  : 오디오 in/out/캐시 $32/$64/$0.40 per MTok
#       https://developers.openai.com/api/docs/models/gpt-realtime
#   Claude Sonnet 4.6          : in/out $3/$15 · 캐시읽기 $0.30 per MTok
#       https://platform.claude.com/docs/en/about-claude/pricing
#   ⚠ 근사/3자: 오디오 토큰↔시간(600/1200 tok/분), 발화량 환산, 분당 대화 비용대($0.18–0.46)
#       https://frankfu.blog/openai/openai-realtime-api-measured-cost-per-minute/
# ─────────────────────────────────────────────────────────────────────────────
