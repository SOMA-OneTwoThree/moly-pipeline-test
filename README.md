# moly-server

STT-LLM-TTS 음성 대화 파이프라인 중 **LLM 파트**(텍스트 인풋 → LLM → 스트리밍 텍스트 아웃풋)의 서버입니다.
STT/TTS 팀은 이 문서의 **연동 계약**만 보면 됩니다.

- 스택: Next.js (App Router) + `@anthropic-ai/sdk`
- LLM은 추상화되어 있어 env로 교체 가능 (현재 provider: `anthropic`)
- 단일턴: 요청은 `{ text }`, 응답은 텍스트 delta **SSE 스트림**

---

## 연동 계약 (STT/TTS가 소비)

### 엔드포인트

```
POST /api/chat
Content-Type: application/json

{ "text": "안녕하세요" }
```

- `text`: 비어 있지 않은 문자열(최대 4000자). STT는 인식된 문자열만 던지면 됩니다.

### 응답: SSE 스트림 (`Content-Type: text/event-stream`)

각 이벤트는 `data: <JSON>\n\n` 한 줄입니다.

| 이벤트 | 의미 |
|---|---|
| `data: {"delta":"<조각>"}` | 응답 텍스트 조각(0개 이상). 순서대로 이어붙이면 전체 응답. |
| `data: {"done":true}` | **정상 종료.** |
| `data: {"error":"<메시지>"}` | **비정상 종료.** `done`을 대체. |

**종료 불변식:** 스트림은 `done` 또는 `error` **중 정확히 하나**로 끝납니다(둘 다/둘 다 아님 없음). 소비자는 둘 중 하나를 "턴 종료"로 처리하세요.

**delta 정의:** 토큰/단어/문장 경계를 **보장하지 않는** 임의 길이 텍스트 조각입니다.
- 도착 순서대로 **이어붙이기만** 하세요(재정렬 금지).
- 문장/단어 분할이 필요하면 **소비자(TTS) 책임**입니다(서버는 문장 버퍼링을 하지 않습니다).
- 한글 등 멀티바이트는 조각 경계에서 잘릴 수 있으니 아래 예제처럼 `TextDecoder({stream:true})`로 누적하세요.

**에러 의미:** `error` 이전에 받은 `delta`는 유효하므로 버리지 마세요. `error` 수신 = 비정상 턴 종료.

**빈 응답:** `delta` 0개 + `done:true` = **성공**(에러 아님).

### 시작 전 오류 (스트림 시작 안 함)

검증/구성 실패는 SSE가 아니라 JSON으로 즉시 반환됩니다.

| 상태 | 본문 | 상황 |
|---|---|---|
| `400` | `{ "error": "..." }` | 잘못된 JSON, 빈 `text`, 4000자 초과 |
| `500` | `{ "error": "..." }` | provider 구성 오류(예: 키 누락) |

### 소비 예제 (fetch + reader + `\n\n` 버퍼 분할)

```ts
const res = await fetch('/api/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ text: '안녕하세요' }),
});

if (!res.ok || !res.body) {
  const { error } = await res.json(); // 시작 전 오류
  throw new Error(error);
}

const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  let sep;
  while ((sep = buffer.indexOf('\n\n')) !== -1) {
    const frame = buffer.slice(0, sep);
    buffer = buffer.slice(sep + 2);
    const line = frame.split('\n').find((l) => l.startsWith('data:'));
    if (!line) continue;
    let evt;
    try { evt = JSON.parse(line.slice(5).trim()); } catch { continue; }

    if (evt.error) { /* 비정상 종료 */ break; }
    if (evt.done)  { /* 정상 종료 */ break; }
    if (evt.delta !== undefined) {
      // evt.delta 를 TTS로 흘려보내거나 누적
    }
  }
}
```

> 브라우저 `EventSource`는 GET 전용이라 쓸 수 없습니다(이 엔드포인트는 POST). 위 fetch+reader 방식을 사용하세요.

---

## 로컬 실행

```bash
npm install
cp .env.example .env   # .env에 본인 ANTHROPIC_API_KEY 주입 (아래 참고)
npm run dev            # http://localhost:3000
```

브라우저로 `http://localhost:3000` 접속 → 입력창에서 스트리밍 응답 확인. 또는:

```bash
curl -N -X POST http://localhost:3000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"안녕"}'
```

### 환경변수 (`.env`)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | LLM provider |
| `LLM_MODEL` | `claude-sonnet-4-6` | anthropic 모델 ID |
| `LLM_MAX_TOKENS` | `1024` | 최대 출력 토큰 |
| `SYSTEM_PROMPT` | (코드 기본값) | 시스템 프롬프트 |
| `ANTHROPIC_API_KEY` | — | **실행에 필수.** `.env`에만, 절대 커밋 금지 |

- 실제 키는 gitignore된 `.env`에만 주입합니다. `.env.example`/소스/로그에 키를 두지 마세요.
- 시크릿에 `NEXT_PUBLIC_` 접두사 금지(클라이언트 번들로 노출).

---

## 범위

- **이번 단계 범위:** 텍스트 인풋 → 스트리밍 텍스트 아웃풋. 멀티턴·세션·인증·CORS·Supabase는 범위 밖.
