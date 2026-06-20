"""비용 모델 + metrics CSV 마이그레이션 회귀 테스트.

실행: pytest tests/ -q   (또는  python -m pytest tests/test_cost_model.py)

핵심 보호 대상:
  1. 정확비용 함수의 산술(추정 아님) — 단가 바뀌면 여기서 깨져야 함.
  2. metrics.csv 스키마 마이그레이션 — 구 CSV(cost_usd 없음, ragged 행 포함)에
     append 시 ValueError 없이 컬럼이 추가되고 기존 행이 보존되어야 함.
     (개발 중 None-key ragged 행 버그가 실제로 발생 → 회귀 방지용.)
"""
from __future__ import annotations

import csv
import os
import tempfile

from harness.cost_model import (
    Rates,
    cost_realtime_from_usage,
    cost_stt_from_usage,
    cost_tts_from_chars,
    cost_pipeline_measured,
)
from harness.metrics import TurnMetrics, write_outputs


R = Rates()


# ─── 정확비용 산술 ───────────────────────────────────────────────────────────
def test_stt_cost_from_usage():
    # (1140 오디오입력 × $1.25 + 38 텍스트출력 × $5) / 1e6
    got = cost_stt_from_usage({"input_tokens": 1140, "output_tokens": 38}, R)
    assert abs(got - (1140 * 1.25 + 38 * 5) / 1e6) < 1e-12


def test_stt_cost_empty_is_zero():
    assert cost_stt_from_usage({}, R) == 0.0


def test_tts_cost_from_chars():
    assert abs(cost_tts_from_chars(240, R) - 240 * 15.0 / 1e6) < 1e-12
    assert cost_tts_from_chars(0, R) == 0.0
    assert cost_tts_from_chars(-5, R) == 0.0  # 음수 방어


def test_pipeline_measured_lines_and_total():
    cb = cost_pipeline_measured({"input_tokens": 1140, "output_tokens": 38}, "x" * 240, R)
    assert cb.lines["LLM (미측정·moly-server)"] == 0.0  # moly-server usage 미제공
    assert abs(cb.lines["TTS (문자수 실측)"] - 240 * 15.0 / 1e6) < 1e-12
    assert abs(cb.total_usd - sum(cb.lines.values())) < 1e-12


def test_pipeline_measured_no_stt_usage():
    cb = cost_pipeline_measured(None, "hello", R)
    assert cb.lines["STT (usage 실측)"] == 0.0
    assert cb.total_usd == cost_tts_from_chars(len("hello"), R)


def test_realtime_cost_nested_cache():
    # 캐시 토큰은 비캐시에서 차감되어 이중계산 안 됨.
    usage = {
        "input_token_details": {"audio": 1000, "text": 200,
                                "cached_tokens_details": {"audio": 300, "text": 50}},
        "output_token_details": {"audio": 800, "text": 100},
    }
    cb = cost_realtime_from_usage(usage, R)
    assert abs(cb.lines["오디오 입력"] - (1000 - 300) * 32.0 / 1e6) < 1e-12
    assert abs(cb.lines["오디오 출력"] - 800 * 64.0 / 1e6) < 1e-12
    assert abs(cb.lines["캐시 입력"] - (300 + 50) * 0.40 / 1e6) < 1e-12


def test_realtime_cost_flat_cache_alt_names():
    # 평면 cached_tokens + audio_tokens 별칭도 파싱.
    usage = {"input_token_details": {"audio_tokens": 600, "text_tokens": 100, "cached_tokens": 200},
             "output_token_details": {"audio_tokens": 1200, "text_tokens": 0}}
    cb = cost_realtime_from_usage(usage, R)
    assert abs(cb.lines["오디오 입력"] - (600 - 200) * 32.0 / 1e6) < 1e-12


def test_realtime_cost_empty():
    assert cost_realtime_from_usage({}, R).total_usd == 0.0


# ─── metrics.csv 마이그레이션 회귀 ───────────────────────────────────────────
_LEGACY_HEADER = "input_label,stt_model,transcript,reply,error,stt_latency,end_to_end"


def _make_legacy_csv(path: str) -> None:
    """cost_usd 없는 구 스키마 + ragged 행 두 종류.

    - underflow 행: 필드 모자람(에러 행) → DictReader가 restval로 채움.
    - overflow 행 : 필드 초과(reply에 콤마 등) → DictReader가 None 키에 잉여를 몰아넣음.
      이 None 키가 재작성 시 ValueError를 유발했던 실제 버그 → 반드시 재현해야 함.
    """
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(_LEGACY_HEADER + "\n")
        f.write("a,gpt-4o-mini-transcribe,hi,hello,,0.5,1.2\n")
        f.write("b,gpt-4o-mini-transcribe,,,STT 오류\n")        # underflow
        # overflow: 헤더 7컬럼인데 9개 값 → DictReader가 잉여 2개를 None 키 리스트로.
        f.write("c,gpt-4o-mini-transcribe,hi,he,llo,extra,0.5,1.2,9.9\n")


def test_csv_migration_adds_column_preserves_rows():
    d = tempfile.mkdtemp()
    cp = os.path.join(d, "metrics.csv")
    _make_legacy_csv(cp)

    m = TurnMetrics(input_label="new", transcript="hi", reply="hello")
    m.extra["cost_usd"] = 0.0052
    write_outputs(m, d)  # 마이그레이션 발동(ValueError 나면 안 됨)

    with open(cp, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert "cost_usd" in rows[0]              # 컬럼 추가됨
    assert len(rows) == 4                     # 구 3행(under/over 포함) 보존 + 신규 1
    assert rows[0]["cost_usd"] == ""          # 구 행은 빈칸
    assert rows[-1]["cost_usd"] == "0.0052"   # 신규 행 비용
    assert rows[0]["input_label"] == "a"      # 기존 데이터 무손상
    assert [r["input_label"] for r in rows[:3]] == ["a", "b", "c"]  # 순서/행 보존
    assert None not in rows[-1]               # 잉여 None 키가 새 행에 새지 않음


def test_csv_append_compatible_header_no_rewrite():
    d = tempfile.mkdtemp()
    # 첫 기록이 이미 cost_usd 포함 헤더를 만든다 → 두 번째는 단순 append.
    m1 = TurnMetrics(input_label="x", reply="aa"); m1.extra["cost_usd"] = 0.001
    write_outputs(m1, d)
    m2 = TurnMetrics(input_label="y", reply="bb"); m2.extra["cost_usd"] = 0.002
    write_outputs(m2, d)
    with open(os.path.join(d, "metrics.csv"), newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["input_label"] for r in rows] == ["x", "y"]
    assert [r["cost_usd"] for r in rows] == ["0.001", "0.002"]
