"""턴 단위 지연 측정 — 모든 시각은 time.perf_counter() 단조시계 기준(초)."""
from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


def now() -> float:
    return time.perf_counter()


def slug(x: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in (x or "na")).strip("-").lower() or "na"


def combo_id(stt_model: str, llm_model: str, tts_model: str) -> str:
    """API 조합 식별 슬러그 — 출력 폴더/구분용. 조합마다 폴더가 갈려 충돌 없음."""
    return f"{slug(stt_model)}__{slug(llm_model)}__{slug(tts_model)}"


@dataclass
class TurnMetrics:
    """한 번의 STT→LLM→TTS 턴에서 찍는 타임스탬프와 파생 지연.

    파일 벤치마크 기준점(t_start)은 'STT 요청 시작'이다. 실시간 마이크에서는
    '발화 종료' 시각으로 대체된다(run_mic에서 주입). end_to_end는 t_start →
    '첫 음성 출력'으로 정의한다 — 사용자 체감 지연.
    """

    # 식별자 (Phase 4 비교 매트릭스용)
    input_label: str = ""
    stt_provider: str = ""
    stt_model: str = ""
    tts_provider: str = ""
    tts_model: str = ""
    llm_model: str = "(moly-server)"

    # 결과 텍스트
    transcript: str = ""
    reply: str = ""

    # 절대 타임스탬프(perf_counter)
    t_start: float = 0.0
    t_audio_end: Optional[float] = None   # (실시간 STT) 발화/오디오 전송 종료 — 꼬리 지연 기준점
    t_stt_first: Optional[float] = None   # 첫 전사 delta
    t_stt_done: Optional[float] = None    # 전사 확정
    t_llm_req: Optional[float] = None     # /api/chat 요청 전송
    t_llm_first: Optional[float] = None   # 첫 LLM delta (TTFT)
    t_llm_done: Optional[float] = None    # done
    t_first_sentence: Optional[float] = None  # 첫 완성 문장
    t_tts_req: Optional[float] = None     # 첫 TTS 요청
    t_tts_first_audio: Optional[float] = None  # 첫 오디오 청크 ★
    t_tts_done: Optional[float] = None    # 마지막 오디오 청크

    # 부가
    run_id: str = ""
    timestamp: str = ""
    input_audio_sec: Optional[float] = None   # 입력 발화 길이
    output_audio_sec: Optional[float] = None  # 응답 음성 길이
    audio_out_path: str = ""
    error: str = ""
    extra: dict = field(default_factory=dict)

    # --- 파생 지연(초). 기준점이 없으면 None ---
    def _d(self, a: Optional[float], b: Optional[float]) -> Optional[float]:
        if a is None or b is None:
            return None
        return round(b - a, 4)

    @property
    def stt_latency(self) -> Optional[float]:
        return self._d(self.t_start, self.t_stt_done)

    @property
    def stt_ttfb(self) -> Optional[float]:
        return self._d(self.t_start, self.t_stt_first)

    @property
    def llm_ttft(self) -> Optional[float]:
        return self._d(self.t_llm_req, self.t_llm_first)

    @property
    def llm_total(self) -> Optional[float]:
        return self._d(self.t_llm_req, self.t_llm_done)

    @property
    def first_sentence_latency(self) -> Optional[float]:
        return self._d(self.t_llm_req, self.t_first_sentence)

    @property
    def tts_first_audio_latency(self) -> Optional[float]:
        return self._d(self.t_tts_req, self.t_tts_first_audio)

    @property
    def end_to_end(self) -> Optional[float]:
        """t_start → 첫 음성 출력. 가장 중요한 체감 지연."""
        return self._d(self.t_start, self.t_tts_first_audio)

    @property
    def stt_tail(self) -> Optional[float]:
        """(실시간) 발화 종료 → 전사 확정. 파일모드 거품을 뺀 진짜 STT 지연."""
        return self._d(self.t_audio_end, self.t_stt_done)

    @property
    def e2e_speech_end(self) -> Optional[float]:
        """(실시간) 발화 종료 → 첫 음성 출력. 실제 대화 체감 지연."""
        return self._d(self.t_audio_end, self.t_tts_first_audio)

    @property
    def turn_total(self) -> Optional[float]:
        return self._d(self.t_start, self.t_tts_done)

    # --- LLM 토큰·응답 길이 (moly-server usage 노출) ---
    @property
    def llm_in_tok(self) -> Optional[int]:
        u = self.extra.get("llm_usage")
        return u.get("input_tokens") if isinstance(u, dict) else None

    @property
    def llm_out_tok(self) -> Optional[int]:
        u = self.extra.get("llm_usage")
        return u.get("output_tokens") if isinstance(u, dict) else None

    @property
    def response_len_chars(self) -> int:
        return len(self.reply or "")

    @property
    def tokens_per_sec(self) -> Optional[float]:
        """출력 토큰 / (첫 토큰→완료). 길어질수록 대기에 직결."""
        out = self.llm_out_tok
        gen = self._d(self.t_llm_first, self.t_llm_done)
        if out and gen and gen > 0:
            return round(out / gen, 1)
        return None

    @property
    def success(self) -> bool:
        return not self.error

    def derived(self) -> dict:
        return {
            "stt_ttfb": self.stt_ttfb,
            "stt_latency": self.stt_latency,
            "llm_ttft": self.llm_ttft,
            "llm_total": self.llm_total,
            "first_sentence_latency": self.first_sentence_latency,
            "tts_first_audio_latency": self.tts_first_audio_latency,
            "end_to_end": self.end_to_end,
            "stt_tail": self.stt_tail,
            "e2e_speech_end": self.e2e_speech_end,
            "turn_total": self.turn_total,
            "tokens_per_sec": self.tokens_per_sec,
        }

    # --- 출력 ---
    def summary_lines(self) -> list[str]:
        d = self.derived()
        def ms(v): return "—" if v is None else f"{v*1000:.0f}ms"
        lines = [
            f"  입력 : {self.input_label}",
            f"  전사 : {self.transcript!r}",
            f"  응답 : {self.reply!r}",
        ]
        if self.t_audio_end is not None:
            # 상식적 체감 지연: 내가 말을 멈춘 순간 → AI 음성이 나오기 시작할 때까지
            lines += [
                f"  ⏱  체감 지연 (말 멈춤 → 첫 응답 음성) :  {ms(d['e2e_speech_end'])}",
                f"        = STT꼬리 {ms(d['stt_tail'])}  +  LLM 첫문장 {ms(d['first_sentence_latency'])}  +  TTS 첫소리 {ms(d['tts_first_audio_latency'])}",
            ]
        else:
            lines += [
                f"  ⏱  체감 지연 (입력 → 첫 응답 음성) :  {ms(d['end_to_end'])}",
                f"        = STT {ms(d['stt_latency'])}  +  LLM 첫문장 {ms(d['first_sentence_latency'])}  +  TTS 첫소리 {ms(d['tts_first_audio_latency'])}",
            ]
        lines.append(
            f"  (상세) LLM TTFT {ms(d['llm_ttft'])} · LLM전체 {ms(d['llm_total'])} · 턴총시간(응답 전체 합성) {ms(d['turn_total'])}"
        )
        return lines

    def to_row(self) -> dict:
        def blank(v):
            return "" if v is None else v
        row = {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "input_label": self.input_label,
            "stt_provider": self.stt_provider,
            "stt_model": self.stt_model,
            "llm_model": self.llm_model,
            "tts_provider": self.tts_provider,
            "tts_model": self.tts_model,
            "success": int(self.success),
            "transcript": self.transcript,
            "reply": self.reply,
            "error": self.error,
        }
        row.update({k: blank(v) for k, v in self.derived().items()})
        # 토큰·응답 길이·오디오 길이
        row["llm_in_tok"] = blank(self.llm_in_tok)
        row["llm_out_tok"] = blank(self.llm_out_tok)
        row["response_len_chars"] = self.response_len_chars
        row["input_audio_sec"] = blank(self.input_audio_sec)
        row["output_audio_sec"] = blank(self.output_audio_sec)
        # 비용 (pipeline.run_turn이 extra에 채움)
        row["cost_usd"] = self.extra.get("cost_usd", "")
        row["cost_stt"] = self.extra.get("cost_stt", "")
        row["cost_llm"] = self.extra.get("cost_llm", "")
        row["cost_tts"] = self.extra.get("cost_tts", "")
        return row


def write_outputs(m: TurnMetrics, out_dir: str) -> str:
    """턴 메트릭을 단일 metrics.csv에 누적(append). csv 경로 반환.

    행은 조합 컬럼(stt_model/llm_model/tts_model)으로 구분된다. 음성 파일은
    pipeline이 runs/audio/{combo}/ 아래 조합별로 분리 저장(충돌 없음).
    """
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "metrics.csv")
    row = m.to_row()
    _append_csv_row(csv_path, list(row.keys()), row)
    return csv_path


def _read_header(path: str) -> Optional[list[str]]:
    try:
        with open(path, newline="", encoding="utf-8") as f:
            first = f.readline()
        if not first.strip():
            return None
        return next(csv.reader([first]))
    except (OSError, StopIteration):
        return None


def _append_csv_row(csv_path: str, fields: list[str], row: dict) -> None:
    """행을 append. 단, 기존 헤더에 새 컬럼(예: cost_usd)이 없으면 스키마를 마이그레이션.

    DictWriter는 fieldnames에 없는 키가 row에 있으면 ValueError를 던지므로,
    헤더 불일치 시 기존 데이터를 새 스키마로 다시 써서 깨짐을 방지한다.
    """
    existing = _read_header(csv_path)
    if existing is None:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerow(row)
        return

    missing = [c for c in fields if c not in existing]
    if not missing:
        # 헤더 호환 — 그대로 append. extrasaction='ignore'로 여분 키도 안전.
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=existing, extrasaction="ignore").writerow(row)
        return

    # 새 컬럼 추가 → 전체 재작성(기존 행은 빈칸으로 채움).
    new_fields = existing + missing
    with open(csv_path, newline="", encoding="utf-8") as f:
        # 구 CSV에 헤더보다 짧/긴 ragged 행이 있을 수 있음(에러 행 등).
        # restkey=None 로 흘러든 여분 값은 재작성 시 버린다.
        old_rows = list(csv.DictReader(f, restval=""))
    base = {c: "" for c in new_fields}
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=new_fields, extrasaction="ignore")
        w.writeheader()
        for r in old_rows:
            r.pop(None, None)  # DictReader가 만든 여분 컬럼 키 제거
            w.writerow({**base, **r})
        w.writerow({**base, **{k: v for k, v in row.items() if k in new_fields}})
