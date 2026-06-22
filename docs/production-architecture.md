# Molly 프로덕션 아키텍처 (개발 레퍼런스, 2026-06)

3개 레포로 분리. 오늘 검증 스택: **Deepgram Nova-3 + Groq Llama-3.3-70b + ElevenLabs Flash(Jessica) + Mem0**.
체감 지연 목표 ~1초(검증됨).

## 확정 결정
| 항목 | 결정 |
|---|---|
| ① server | **Supabase**(Auth + Postgres) + **Edge Functions(서버리스)** = 제어 플레인 |
| ② LLM | **Python FastAPI**, **컨테이너(Railway/Fly, 항상 켜짐)** |
| ③ STT-TTS | **Python**, **컨테이너(WebSocket)** = 실시간 음성 게이트웨이 |
| LLM | Groq Llama-3.3-70b (provider 추상화) |
| STT/TTS | Deepgram Nova-3 / ElevenLabs Flash v2.5(Jessica) |
| 메모리 | Mem0 managed (LLM 레포가 소유, 캐시 + async add) |
| 히스토리 | `messages[]` (게이트웨이가 세션 중 보관, server가 영속) |

## 전체 구조 + 데이터 흐름
```
                       [모바일 앱]
                          │  WebSocket (오디오 in/out + 이벤트)
                          ▼
 ┌──────────────────────────────────────────────┐
 │ ③ STT-TTS 게이트웨이 (컨테이너, 실시간 오케스트레이터) │
 │   앱 WS 보유 · STT · TTS · 턴테이킹/barge-in       │
 │   세션 대화(messages[]) 메모리 보관               │
 └───┬───────────────┬──────────────────┬─────────┘
     │ STT WS        │ HTTP SSE          │ REST(제어/영속)
     ▼               ▼                   ▼
 [Deepgram]   ┌──────────────┐    ┌──────────────────────┐
 [ElevenLabs] │ ② LLM (FastAPI)│   │ ① server (Supabase Edge)│
              │ Groq+페르소나  │   │ Auth·세션·Postgres 영속  │
              │ +Mem0         │   └──────────────────────┘
              └──────────────┘
```

### 한 턴 시퀀스 (구체)
```
0. (세션시작) 앱 → ① server: 인증 → session_token 발급
1. 앱 → ③ 게이트웨이: WS 연결(session_token). 게이트웨이 → ① server: 토큰검증 + 최근 히스토리·user_id 로드
2. 사용자 발화 → 앱 → 게이트웨이 → Deepgram(STT WS) → interim/final transcript
3. 발화 종료(final): 게이트웨이가 messages에 {user: transcript} 추가
   → ② LLM POST /chat {messages, user_id} (SSE)
4. LLM: Mem0 search(캐시) + 페르소나 + 히스토리 → 응답 delta 스트림
5. 게이트웨이: delta를 문장 단위로 → ElevenLabs(TTS WS) → 오디오 청크 → 앱
   (문장 파이프라이닝: 첫 문장 끝나는 즉시 TTS 시작 — 오늘 하네스 방식)
6. 턴 종료: 게이트웨이 messages에 {assistant: reply} 추가
   → (async) ① server에 턴 보고 → Supabase 저장 / ② LLM은 이미 Mem0 async add
```
지연 예산(검증): STT꼬리 ~175ms + LLM첫문장 ~500ms + TTS첫소리 ~300ms ≈ **~1초** (+ 게이트웨이 로컬 홉 미미)

---

## ① server (Supabase + Edge Functions) — 제어 플레인
**책임**: 인증, 세션 lifecycle, 대화 영속(Postgres), 설정, 토큰 발급
**서버리스인 이유**: 짧은 제어 요청만(실시간 스트림은 ③가 처리)
```
molly-server/
├── supabase/
│   ├── migrations/         # 스키마 SQL
│   └── config.toml
├── functions/              # Edge Functions(Deno/TS)
│   ├── session-start/      # 인증→session_token, 히스토리·user_id 반환
│   ├── session-end/        # 세션 종료 처리
│   ├── turn-save/          # 턴 영속(③가 호출)
│   └── _shared/            # supabase 클라, 인증 미들웨어
└── README.md
```
**Postgres 스키마**
```sql
profiles(    id uuid PK = auth.users.id, display_name, locale, created_at )
conversations( id uuid PK, user_id uuid FK, started_at, ended_at )
messages(    id uuid PK, conversation_id uuid FK, role text, content text, created_at )
-- RLS: 모든 테이블 user_id = auth.uid() 로 격리
```
**env**: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `JWT_SECRET`
**배포**: `supabase functions deploy`

## ② LLM (Python FastAPI, 컨테이너) — Molly의 뇌
**책임**: `{messages, user_id}` → 스트리밍 응답 (페르소나 + Mem0 + 히스토리)
**인터페이스**: `POST /chat` → SSE (`data:{delta}` × N → `data:{done, usage}`) — 오늘 계약 그대로
```
molly-llm/
├── app/
│   ├── main.py             # FastAPI, /chat SSE 엔드포인트, /health
│   ├── llm/                # provider 추상화
│   │   ├── base.py
│   │   ├── openai_compat.py  # Groq/OpenAI/DeepSeek (base_url 교체)
│   │   └── factory.py        # LLM_PROVIDER 분기
│   ├── chat/
│   │   ├── service.py        # 페르소나 + 프롬프트 조립 + 기억 주입
│   │   └── prompts.py        # 페르소나(alien-Molly) 버전 관리
│   └── memory/
│       └── mem0.py           # search 캐시(getCached/refresh) + async add
├── tests/
├── Dockerfile
├── requirements.txt          # fastapi, uvicorn, openai, mem0ai, httpx
└── .env.example
```
**핵심 구현 노트**
- SSE: `StreamingResponse` 또는 `sse-starlette`. Groq=`openai`(base_url=groq). 오늘 벤치에서 검증.
- Mem0: `getCachedMemories`(캐시 우선) + `refreshMemoryCache`(백그라운드). **컨테이너=인스턴스 RAM이라 캐시 그대로 작동(Redis 불필요)**.
- async add: 응답 스트림 종료 후 `BackgroundTasks`로 `mem0.add` + 캐시 갱신.
- 페르소나·system은 서버 소유. 클라(게이트웨이)는 user/assistant 턴만 전송. 슬라이딩 윈도우 최근 20.
**env**: `GROQ_API_KEY`, `MEM0_API_KEY`, `LLM_PROVIDER=groq`, `LLM_MODEL=llama-3.3-70b-versatile`, `SYSTEM_PROMPT`(빈값=기본)
**배포**: Dockerfile → Railway/Fly (항상 켜짐, min 1 인스턴스 — 콜드스타트 금지)

## ③ STT-TTS (Python, 컨테이너) — 실시간 음성 게이트웨이
**책임**: 앱 WS 보유, STT·TTS 스트리밍, 턴테이킹/barge-in, 세션 대화 보관, LLM/server 호출
**인터페이스**: 앱 ↔ WebSocket (오디오 in/out + 이벤트: transcript, turn_start/end, barge-in)
```
molly-speech/
├── app/
│   ├── main.py             # FastAPI + WebSocket 엔드포인트(/ws), /health
│   ├── gateway/
│   │   ├── session.py        # 세션 상태(messages[], user_id), server 연동
│   │   ├── orchestrator.py   # 턴 루프: STT→LLM→TTS 조율, 문장 파이프라이닝
│   │   └── turn_taking.py     # VAD/끝점, barge-in(사용자 끼어들면 TTS 중단)
│   ├── stt/
│   │   └── providers/        # deepgram.py(Nova-3, WS) [+ assemblyai 대체]
│   ├── tts/
│   │   └── providers/        # elevenlabs.py(Flash, WS) [+ openai 대체]
│   └── shared/               # PCM16 포맷, VAD, 오디오 유틸 (하네스 코드 재사용)
├── Dockerfile
├── requirements.txt          # fastapi, uvicorn, websockets, httpx, numpy
└── .env.example
```
**핵심 구현 노트**
- 실시간 영속 WS → **서버리스 불가, 컨테이너 필수**.
- STT: Deepgram WS, interim_results + endpointing. 발화 종료(final) 시점이 턴 트리거.
- TTS: ElevenLabs WS, 문장 단위 스트리밍(첫 문장 즉시 시작 = 지연↓).
- barge-in: 사용자가 말 시작하면 진행 중 TTS 중단 + 새 턴.
- 세션 대화는 게이트웨이 메모리에 보관(server에서 초기 로드, 턴마다 async 영속).
**env**: `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID=cgSgspJ2msm6clMCkdW9`, `LLM_URL`, `SERVER_URL`
**배포**: Dockerfile → Railway/Fly (②와 같은 플랫폼)

---

## 레포 간 계약 (요약)
| 호출 | 프로토콜 | 계약 |
|---|---|---|
| 앱 ↔ ③ | WebSocket | 오디오 바이너리 + JSON 이벤트 |
| ③ → ② | HTTP SSE | `POST /chat {messages[], user_id}` → delta/done |
| ③ → ① | REST | `session-start`(히스토리·user_id), `turn-save` |
| 앱 → ① | REST | 인증, session_token |
| ②→Mem0, ③→Deepgram/ElevenLabs | 외부 | 각 provider |

## 공통 규칙
- **시크릿**: 레포별 `.env`(gitignore). 절대 커밋 X. (오늘 키: GROQ/MEM0/DEEPGRAM/ELEVENLABS)
- **인증**: ① 발급 토큰으로 ②③ 보호(내부 호출은 서비스 토큰)
- **관찰성**: 오늘 지연 분해 지표(STT꼬리/LLM첫문장/TTS첫소리/체감E2E) 계속 수집
- **provider 추상화** 유지: 각 단계 env 한 줄로 교체 가능(Groq↔Gemini, Deepgram↔AssemblyAI, ElevenLabs↔OpenAI)
- **로컬 개발**: 각 레포 `docker compose` 또는 uvicorn; 게이트웨이가 LLM_URL=localhost로 ② 호출

## 단계(Phase)
1. **스캐폴드**: 3 레포 + Dockerfile + /health + Supabase 스키마/Auth
2. **②LLM 포팅**: 오늘 Node 코어 → FastAPI(페르소나+Mem0+SSE). 단독 테스트(curl)
3. **③게이트웨이**: STT→LLM→TTS 텍스트 경로부터, 그 다음 오디오 WS + 문장 파이프라이닝
4. **①server**: Auth + 세션/메시지 영속 + 토큰
5. **통합**: 앱 ↔ ③ ↔ ②/① 풀 루프, 지연 측정(목표 ~1초 유지)
6. **다듬기**: barge-in, 세션시작 prefetch, Redis(다중 인스턴스 시)

## 미결(내일 확정)
- 앱↔③ 오디오 포맷/샘플레이트(PCM16 16k 권장)
- 게이트웨이가 세션 보관 vs server가 매턴 제공(권장: 게이트웨이 보관)
- 컨테이너 플랫폼 Railway vs Fly (팀 친숙도)
