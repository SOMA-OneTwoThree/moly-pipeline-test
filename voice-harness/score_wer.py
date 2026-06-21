"""WER/CER 스코어러 — STT가 한국식 영어를 얼마나 정확히 받아쓰는지.

samples/ 의 오디오 + 같은 이름 .txt(정답 전사)를 매칭해 현재 STT(.env)로 전사 후
WER(단어 오류율)·CER(글자 오류율)을 계산한다. STT_MODEL을 바꿔가며 돌리면 runs/wer.csv에
누적되어 모델별 비교가 된다.

사용:
  python score_wer.py
  STT_MODEL=gpt-4o-transcribe python score_wer.py
"""
from __future__ import annotations

import csv
import glob
import os
import re
import sys

import jiwer

from harness.config import Config
from harness.stt import get_stt_provider


# 축약형 → 풀어쓰기. "i'm" vs "i am"처럼 의미 같은 변형이 WER을 부풀리는 것 방지.
_CONTRACTIONS = {
    "i'm": "i am", "you're": "you are", "he's": "he is", "she's": "she is",
    "it's": "it is", "we're": "we are", "they're": "they are", "i've": "i have",
    "you've": "you have", "we've": "we have", "i'd": "i would", "i'll": "i will",
    "don't": "do not", "doesn't": "does not", "didn't": "did not", "can't": "cannot",
    "won't": "will not", "isn't": "is not", "aren't": "are not", "wasn't": "was not",
    "weren't": "were not", "haven't": "have not", "hasn't": "has not", "hadn't": "had not",
    "wouldn't": "would not", "couldn't": "could not", "shouldn't": "should not",
    "let's": "let us", "that's": "that is", "what's": "what is", "how's": "how is",
    "there's": "there is", "here's": "here is", "where's": "where is",
}


def _norm(s: str) -> str:
    """WER 정규화 — 소문자 → 축약형 풀기 → 구두점 제거 → 공백 정리."""
    s = s.lower()
    for c, full in _CONTRACTIONS.items():
        s = re.sub(rf"\b{re.escape(c)}\b", full, s)
    s = re.sub(r"[^\w\s']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _transcribe(stt, path: str, lang: str) -> str:
    text = ""
    for ev in stt.transcribe_stream(path, language=lang):
        if getattr(ev, "audio_end", False):
            continue
        if ev.final:
            text = ev.text
    return text


def main() -> int:
    cfg = Config.load()
    try:
        stt = get_stt_provider(cfg)
    except RuntimeError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2

    pairs = []
    for audio in sorted(glob.glob("samples/*")):
        if audio.endswith(".txt"):
            continue
        ref = os.path.splitext(audio)[0] + ".txt"
        if os.path.exists(ref):
            pairs.append((audio, ref))
    if not pairs:
        print("정답 전사(.txt) 매칭되는 샘플이 없습니다. samples/<name>.txt 를 만드세요.", file=sys.stderr)
        return 1

    print(f"STT={stt.name}:{stt.model}  샘플 {len(pairs)}개")
    print("=" * 66)
    refs, hyps, rows = [], [], []
    for audio, ref_path in pairs:
        with open(ref_path, encoding="utf-8") as f:
            ref = f.read().strip()
        hyp = _transcribe(stt, audio, cfg.stt_language)
        rn, hn = _norm(ref), _norm(hyp)
        wer = jiwer.wer(rn, hn)
        cer = jiwer.cer(rn, hn)
        refs.append(rn)
        hyps.append(hn)
        print(f"\n▸ {os.path.basename(audio)}")
        print(f"  정답: {ref!r}")
        print(f"  전사: {hyp!r}")
        print(f"  WER {wer * 100:.1f}%   CER {cer * 100:.1f}%")
        rows.append({"stt_model": stt.model, "sample": os.path.basename(audio),
                     "wer": round(wer, 4), "cer": round(cer, 4)})

    agg_wer = jiwer.wer(refs, hyps)
    agg_cer = jiwer.cer(refs, hyps)
    print("\n" + "=" * 66)
    print(f"전체  WER {agg_wer * 100:.1f}%   ·   CER {agg_cer * 100:.1f}%   (STT={stt.model})")

    os.makedirs(cfg.out_dir, exist_ok=True)
    out = os.path.join(cfg.out_dir, "wer.csv")
    write_header = not os.path.exists(out)
    with open(out, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["stt_model", "sample", "wer", "cer"])
        if write_header:
            w.writeheader()
        w.writerows(rows)
    print(f"📄 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
