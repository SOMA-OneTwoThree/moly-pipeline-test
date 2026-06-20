# voice-harness · 몰리 음성 대화 지연·비용 측정 도구

음성 대화(STT→LLM→TTS)의 **지연·품질·비용**을 측정하고, API를 갈아끼우며 비교하는 하네스.
두 가지 아키텍처를 같은 입력으로 측정해 비교한다.

```
A. 파이프라인   [오디오] → STT → POST /api/chat(moly-server LLM) → TTS → [음성]
B. 음성→음성    [오디오] → gpt-realtime (한 모델이 듣고·생각하고·말함) → [음성]
```

> 측정 결과 요약은 **`voice-pipeline-summary.xlsx`** 참고 (측정시간 / 체감지연 / 비용 / 현재구성).

---

## 구현 방법 (요약)

### 공통 — provider 추상화
`moly-server`가 LLM을 `LLM_PROVIDER` env로 추상화한 것과 똑같이, STT/TTS도 인터페이스 뒤에 둔다.
`.env`의 `STT_PROVIDER`/`TTS_PROVIDER`/`STT_MODEL`/`TTS_MODEL` 한 줄만 바꾸면 API가 교체되고,
결과가 `runs/metrics.csv`에 누적되어 비교표가 된다. 모든 시각은 `time.perf_counter()` 단조시계로 찍는다.

### A. 파이프라인 (`run_file.py`)
1. **STT** — 오디오 파일을 **OpenAI Realtime API(WebSocket) 스트리밍 전사**로 처리. ffmpeg로 PCM16
   24kHz 디코딩 → 실시간 페이스로 흘려보냄(말 속도 모사) → 서버 VAD가 `speech_stopped`로 발화 종료를
   감지 → 그 시점을 기준으로 **꼬리 지연**(말 멈춤→전사 확정)을 측정. 무음은 주입하지 않고 파일 자체
   끝 무음에 의존. 문장 사이 멈춤으로 전사가 조각나면 이어붙인다.
2. **LLM** — 전사 텍스트를 `POST /api/chat`로 보내 SSE delta 스트림 수신(moly-server, 계약 그대로).
3. **문장 파이프라이닝** — delta를 문장 단위로 쪼개, **첫 문장이 끝나는 즉시 TTS를 시작**(별도 스레드).
   LLM이 다음 문장을 생성하는 동안 TTS가 앞 문장을 합성 → 체감 지연을 낮춘다.
4. **TTS** — OpenAI(`tts-1`/`gpt-4o-mini-tts`) 청크 스트리밍. PCM을 wav로 저장하고 시작/끝 페이드로
   onset 클릭(팝) 제거.

### B. 음성→음성 (`run_realtime.py`)
OpenAI Realtime 모델(`gpt-realtime`) 하나가 오디오를 직접 듣고 → 응답을 생성 → 오디오로 말한다.
파일을 실시간 페이스로 전송(끝 무음 trim) → 수동 `commit` + `response.create`로 한 턴·한 응답 →
**발화 종료 → 첫 응답 오디오** 지연 측정. moly-server·TTS를 거치지 않는 최저지연 비교군.

### 측정 지표 (직관적 표기)
체감 지연 = **말 멈춤 → 첫 응답 음성** = `STT 꼬리 + LLM 첫 문장 + TTS 첫 소리` (합이 맞게 분해 표시).

---

## 도구

| 명령 | 용도 | 서버 필요 |
|---|---|---|
| `python run_file.py samples/x.m4a` | 파이프라인 STT→LLM→TTS 측정 | ✅ moly-server |
| `python run_stt.py samples/x.m4a` | STT 단독 측정 (LLM·TTS 없이) | ✕ |
| `python run_realtime.py samples/x.m4a` | 음성→음성 (gpt-realtime) | ✕ |
| `python -m harness.cost_model` | 두 구조 분당 비용 추정/실측 | ✕ |

---

## 설치 · 실행

```bash
cd molly/voice-harness
pip install -r requirements.txt
cp .env.example .env            # OPENAI_API_KEY 주입 (STT·TTS·s2s 공용)

# moly-server (파이프라인용 LLM 박스) — 별도 터미널
cd ../moly-server && cp .env.example .env   # ANTHROPIC_API_KEY + LLM_MODEL 주입
npm run dev                                  # http://localhost:3000

# 측정
cd ../voice-harness
python run_file.py samples/x.m4a     &&  afplay runs/reply-samples_x_m4a.wav
python run_realtime.py samples/x.m4a &&  afplay runs/s2s-reply-x.wav
```

> 입력 언어: `.env`의 `STT_LANGUAGE`(en/ko/빈값=자동). 응답 언어는 moly-server의 `SYSTEM_PROMPT`.
> LLM 모델은 moly-server의 `LLM_MODEL`(예: `claude-haiku-4-5`)이며 변경 시 서버 재시작 필요.

---

## 현재 구성 (2026-06)

| 구성 | 값 |
|---|---|
| STT | `openai_realtime` / `gpt-4o-mini-transcribe` (언어 en) |
| LLM | moly-server / `claude-haiku-4-5` |
| TTS | `gpt-4o-mini-tts` (voice coral, speed 1.0) |
| 비교군 | `gpt-realtime` 음성→음성 (voice marin) |

### 측정 요약 (입력 "How are you? I'm fine.")

| 구조 | 체감 지연 | 1턴 비용 |
|---|---|---|
| 파이프라인 (Haiku + mini-tts) | **~2.6–3.1초** | ~$0.0015 (TTS 실측) |
| 음성→음성 (gpt-realtime) | **~0.58초** | ~$0.0073 |

---

## 새 provider 추가

- **STT**: `harness/stt/<name>_stt.py`에 `transcribe_stream()` 구현 → `harness/stt/__init__.py` 팩토리에 분기
- **TTS**: `harness/tts/<name>_tts.py`에 `synthesize_stream()` 구현 → `harness/tts/__init__.py` 팩토리에 분기
- 예정: ElevenLabs TTS(키+모델만 주면 추가)

## 구조

```
voice-harness/
├── run_file.py / run_stt.py / run_realtime.py   # CLI 3종
├── voice-pipeline-summary.xlsx                  # 측정 결과 정리
├── harness/
│   ├── config.py · llm_client.py(SSE) · sentence_splitter.py
│   ├── pipeline.py(오케스트레이션) · metrics.py(지연·CSV/JSON) · cost_model.py(비용)
│   ├── realtime_s2s.py                          # 음성→음성
│   ├── stt/  (base · openai_stt · openai_realtime_stt · mock · 팩토리)
│   └── tts/  (base · openai_tts · mock · 팩토리)
├── tools/fake_chat_server.py                    # 오프라인용 가짜 moly-server
└── samples/                                     # 입력 오디오
```

## 로드맵
- ✅ 파일 입력 파이프라인 · 스트리밍 STT · 문장 파이프라이닝 · 음성→음성 · 비용 모델
- ⏳ 실시간 마이크(push-to-talk) · ElevenLabs 등 2차 provider 비교
