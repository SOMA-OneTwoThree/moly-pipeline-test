# moly-server

- 스택: Next.js
- LLM은 추상화되어 있어 env로 교체 가능 (`mock` ↔ `anthropic`)
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

---

## 로컬 실행

```bash
npm install
cp .env.example .env   # 그리고 .env 값 채우기
npm run dev            # http://localhost:3000
```

브라우저로 `http://localhost:3000` 접속 → 입력창에서 스트리밍 응답 확인. 또는:

```bash
curl -N -X POST http://localhost:3000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"안녕"}'
```
