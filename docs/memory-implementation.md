# 대화 영속성 구현 (Mem0) — 정리 (2026-06)

> Molly에 "사용자를 기억하는 친구" 영속성을 추가. 실시간 음성 지연(~1초)을 유지하면서 구현.
> 조사 배경은 [memory-research.md](./memory-research.md) 참고.

## 두 층 메모리
```
[작업기억] messages 히스토리   → 지금 대화 흐름 (방금 한 말)
[장기기억] Mem0 (캐시)         → 세션 넘는 사실 (지난주 한 말)
[시스템]   서버 소유 페르소나   → Molly 일관성(+ Mem0 기억 주입)
        ↓ system(+기억) + history + 현재발화 → Groq LLM
```

## 계약 (OpenAI/Anthropic messages 컨벤션)
```jsonc
POST /api/chat
{
  "messages": [                              // 대화 히스토리(최근 N턴, 무상태)
    {"role":"user","content":"..."},
    {"role":"assistant","content":"..."},
    {"role":"user","content":"이번 발화"}     // 마지막 = 현재 턴
  ],
  "user_id": "brownie"                        // 선택 — 있으면 Mem0 장기기억
}
// 하위호환: { "text": "...", "user_id"? } — 단일 user 턴으로 처리
```
- system은 **서버 소유**(페르소나+기억), 클라는 user/assistant 턴만 전송
- 슬라이딩 윈도우 최근 20개(`MAX_HISTORY_MESSAGES`)

## 동작 흐름
```
1. getCachedMemories(user_id, 발화) → 캐시 우선(RAM ~0ms), 미스면 1회 Mem0 search   (service.ts)
2. systemPrompt += "# What you remember about this person\n- ..."                    (service.ts)
3. messages = [{system+기억}, ...history, {user: 현재발화}] → Groq LLM
4. after(): addMemory(user_id, 발화, 응답) + refreshMemoryCache(...)  ← async, 지연 0  (route.ts)
       → Mem0 클라우드가 사실 추출/갱신(ADD/UPDATE/DELETE)
```

## 핵심 설계 결정
- **search 캐시(stale-while-revalidate)**: 매 턴 Mem0 search는 한국→US ~0.7s라 E2E를 2배(1.05→2.14s)로 늘림.
  → 인프로세스 캐시로 첫 턴만 네트워크, 이후 RAM 즉시. after()에서 백그라운드 갱신. **체감 ~1초 복구.**
- **add는 async**: 응답 flush 후 `after()`에서 적재 → 사용자 지연 0.
- **SDK 미사용**: mem0ai JS가 구버전 `@anthropic-ai/sdk` peer 충돌 → REST 직접 호출.
- **fail-safe**: 키 없음/네트워크 오류 시 빈 결과로 삼켜 대화를 절대 막지 않음.

## 추출 정책 (일시 vs 지속) — Mem0 프로젝트 설정
"배고픔" 같은 일시상태가 영구 저장되는 문제 → Mem0 custom_instructions(짧게):
```
Extract only durable facts about the user that stay true over time: name, job,
location, relationships, pets, stable preferences and dislikes, recurring habits,
and goals. Do not store momentary or temporary states such as being hungry, tired,
busy, sleepy, or the mood of the moment.
```
- ⚠️ 대시보드 "Generate/Reconfigure"는 3000토큰 거대 프롬프트를 만들어 **추출을 깨뜨림**(무료플랜 추출모델 과부하).
  → **짧은 instruction을 직접** 넣을 것. Reconfigure 금지.

## 측정 결과 (Deepgram + Groq 70b + ElevenLabs, 메모리 ON)
| 지표 | p50 | 비고 |
|---|---|---|
| 체감 E2E | **1071ms** | 캐시 히트 턴 ~0.85–1.1초 |
| LLM 첫문장 | 554ms | Groq |
| TTS 첫소리 | 305ms | |
| 성공률 | 6/6 | |
| 1턴 비용 | $0.0027 | |

- 추출: 깨끗한 지속 사실만(designer / cat Coco / spicy Korean / afraid of dogs) — 일시상태 필터됨
- 회상: "you're afraid of dogs, so that could be uncomfortable" — 능동 활용 확인

## 운영 주의
- **`.env` 변경 시 서버 재시작 필수** (Next는 .env를 시작 시 1회만 로드 — 코드만 hot-reload)
- **무료플랜 burst 스로틀**: 한꺼번에 여러 add 넣으면 일부 누락. 실서비스(턴당 1건)는 무관
- 키는 gitignored `.env`의 `MEM0_API_KEY`

## 남은 과제
- **세션시작 prefetch** — 첫 턴 캐시미스(~0.7s)도 인사/연결 중에 미리 채워 숨김
- 캐시를 Redis로(다중 서버), Mem0 self-host(아시아 리전) — 프로덕션
- 사용자 기억 조회/삭제 UI(프라이버시 제어)
