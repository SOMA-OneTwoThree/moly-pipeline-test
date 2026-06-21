# voice-harness · 몰리 음성 대화 API 비교 측정 도구

음성 대화(STT→LLM→TTS)의 **지연·품질·비용**을 측정하고, 여러 API를 갈아끼우며 비교해 우리 서비스에
맞는 조합을 선정하는 하네스. 두 아키텍처를 같은 입력으로 비교한다.

```
A. 파이프라인   [오디오] → STT → POST /api/chat(moly-server LLM) → TTS → [음성]
B. 음성→음성    [오디오] → gpt-realtime (한 모델이 듣고·생각하고·말함) → [음성]
```

## 설계 — provider 추상화
`moly-server`가 LLM을 `LLM_PROVIDER` env로 추상화한 것과 똑같이 STT/TTS도 인터페이스 뒤에 둔다.
`.env` 한 줄로 API가 교체되고, 결과는 단일 `runs/metrics.csv`에 **조합 컬럼**으로 누적된다.
합성 음성은 `runs/audio/{조합}/{샘플}.wav`로 **조합별 분리** 저장(충돌 없이 A/B 청취).

## 교체 가능한 provider

| 단계 | env | 옵션 | 비고 |
|---|---|---|---|
| STT | `STT_PROVIDER` | `deepgram` · `assemblyai` · `openai_realtime` · `openai`(배치) · `mock` | 전부 스트리밍 WebSocket(꼬리지연 측정) |
| LLM | moly-server `LLM_PROVIDER` | `anthropic`(Haiku/Sonnet) · `gemini`(Flash-Lite) | 모델은 `LLM_MODEL`, 변경 시 서버 재시작 |
| TTS | `TTS_PROVIDER` | `elevenlabs`(Flash v2.5) · `openai`(tts-1/mini-tts) · `mock` | `ELEVENLABS_VOICE_ID`로 보이스 선택 |

## 도구

| 명령 | 용도 | 서버 필요 |
|---|---|---|
| `python run_file.py samples/*.m4a --repeat N` | 파이프라인 STT→LLM→TTS 측정 → metrics.csv | ✅ moly-server |
| `python run_stt.py samples/x.m4a` | STT 단독 지연 측정 | ✕ |
| `python score_wer.py` | STT WER/CER 채점(samples/*.txt 정답 필요) → wer.csv | ✕ |
| `python aggregate.py` | 조합별 **p50/p90/p99** 지연·비용·성공률 집계 | ✕ |
| `python run_realtime.py samples/x.m4a` | 음성→음성(gpt-realtime) | ✕ |
| `python -m harness.cost_model` | 분당 비용 추정 | ✕ |

## 측정 지표 (`runs/metrics.csv`)
체감 지연 = **말 멈춤 → 첫 응답 음성** = `STT 꼬리 + LLM 첫 문장 + TTS 첫 소리`.
그 외: TTFT, tokens/sec, 응답 토큰/문자 수, 입력/출력 오디오 길이, **실측 비용(STT·LLM·TTS)**,
**Success(성공 여부)**, run_id/timestamp. 음성 대화는 일관성이 중요해 **p50/p90/p99**로 본다.

## 설치 · 실행

```bash
cd molly/moly-server/voice-harness
pip install -r requirements.txt
cp .env.example .env            # 키 주입(OPENAI/DEEPGRAM/ASSEMBLYAI/ELEVENLABS)

# moly-server(LLM 박스) — 별도 터미널
cd .. && cp .env.example .env   # ANTHROPIC_API_KEY 또는 GEMINI_API_KEY + LLM_PROVIDER/LLM_MODEL
npm run dev

# 측정 (조합은 .env 또는 env 오버라이드)
cd voice-harness
STT_PROVIDER=deepgram TTS_PROVIDER=elevenlabs python run_file.py samples/test1.m4a --repeat 3
python aggregate.py
afplay runs/audio/<조합폴더>/test1.wav
```

## 현재 권장 조합 (2026-06, n=2 golden set 기준)

| 단계 | 선정 | 꼬리/첫소리 | 근거 |
|---|---|---|---|
| STT | **Deepgram Nova-3** | ~170–200ms | WER 3.1%(최저)·지연 최저·안정 |
| LLM | Haiku 4.5 (또는 Gemini Flash-Lite) | 첫문장 ~0.8s | Gemini는 ~10배 저렴 |
| TTS | **ElevenLabs Flash v2.5** | 첫소리 ~280ms | OpenAI 대비 ~3배 빠름 |

→ **체감 지연 ~1.3초** (초기 OpenAI 조합 ~3.5초 대비 2.7배 단축, 성공률 100%).
STT 비교: Deepgram WER 3.1% < OpenAI 3.8% < AssemblyAI 5.8%. ⚠️ 표본 작음·영어 전용(코드스위칭 미포함).

## 새 provider 추가
- STT: `harness/stt/<name>_stt.py`에 `transcribe_stream()` → `harness/stt/__init__.py` 분기
- TTS: `harness/tts/<name>_tts.py`에 `synthesize_stream()` → `harness/tts/__init__.py` 분기
- LLM: moly-server `src/lib/llm/<name>.ts` + `index.ts` 분기 (usage `onUsage`로 보고)

## 구조
```
voice-harness/
├── run_file.py · run_stt.py · run_realtime.py · score_wer.py · aggregate.py
├── harness/
│   ├── config.py · llm_client.py(SSE,usage) · sentence_splitter.py
│   ├── pipeline.py · metrics.py · cost_model.py · realtime_s2s.py
│   ├── stt/ (deepgram · assemblyai · openai_realtime · openai · mock · 팩토리)
│   └── tts/ (elevenlabs · openai · mock · 팩토리)
├── tools/fake_chat_server.py        # 오프라인용 가짜 moly-server
└── samples/                         # 오디오 + .txt(WER 정답 전사)
```

## 남은 것
- 코드스위칭(한국어 섞인) golden set 확충 → STT 재검증
- STT/TTS 비용 단가 정확화(Deepgram usage·ElevenLabs 크레딧) · 실시간 마이크
