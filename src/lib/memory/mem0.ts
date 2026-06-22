import 'server-only';

/**
 * Mem0 managed(클라우드) 메모리 레이어 — REST 직접 호출(SDK 미사용, 의존성 충돌 회피).
 * - search: 현재 발화에 관련된 기억을 가져와 시스템 프롬프트에 주입(읽기, 동기).
 * - add: 대화 턴을 보내면 Mem0가 백그라운드로 사실 추출/갱신(쓰기, async·non-blocking).
 * 모든 호출은 fail-safe — 키 없음/네트워크 오류 시 빈 결과로 삼켜 대화를 절대 막지 않는다.
 */

const BASE = 'https://api.mem0.ai/v1';
const TIMEOUT_MS = 4000;

function authHeaders(): Record<string, string> | null {
  const key = process.env.MEM0_API_KEY?.trim();
  if (!key) return null;
  return { Authorization: `Token ${key}`, 'Content-Type': 'application/json' };
}

/** 사용자의 관련 기억을 검색해 문자열 배열로 반환. 실패/미설정 시 빈 배열. */
export async function searchMemories(userId: string, query: string, limit = 5): Promise<string[]> {
  const headers = authHeaders();
  if (!headers) return [];
  try {
    const res = await fetch(`${BASE}/memories/search/`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ query, user_id: userId, limit }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!res.ok) {
      console.error('[mem0] search HTTP', res.status);
      return [];
    }
    const data: unknown = await res.json();
    // 응답은 배열 또는 {results:[...]} 형태 — 각 항목의 memory 필드를 추출.
    const items = Array.isArray(data)
      ? data
      : ((data as { results?: unknown[] })?.results ?? []);
    return items
      .map((r) => (r as { memory?: string })?.memory)
      .filter((m): m is string => typeof m === 'string' && m.length > 0);
  } catch (e) {
    console.error('[mem0] search 실패:', (e as Error).message);
    return [];
  }
}

// ── 인프로세스 캐시 (user_id → 기억). 네트워크 ~0.7s를 RAM ~0ms로 대체. ──
// 서버 재시작 시 비워짐 → 다음 첫 턴에 재충전(허용). 다중 인스턴스면 Redis로 승격.
const memoryCache = new Map<string, string[]>();

/**
 * 캐시 우선 조회 — 캐시에 있으면 즉시 반환(네트워크 X), 없으면(첫 턴) 1회만 블로킹 search 후 캐시.
 * service가 매 턴 이걸 써서 동기 search 0.7s를 피한다. 갱신은 refreshMemoryCache(백그라운드)가 담당.
 */
export async function getCachedMemories(userId: string, query: string): Promise<string[]> {
  const cached = memoryCache.get(userId);
  if (cached) return cached; // 캐시 히트 → 즉시
  const memories = await searchMemories(userId, query); // 첫 턴만 네트워크
  memoryCache.set(userId, memories);
  return memories;
}

/**
 * 백그라운드 캐시 갱신(stale-while-revalidate) — 턴을 막지 않고 최신 검색으로 캐시를 업데이트.
 * route의 after()에서 addMemory와 함께 호출 → 다음 턴이 최신 기억을 즉시 사용.
 */
export async function refreshMemoryCache(userId: string, query: string): Promise<void> {
  const memories = await searchMemories(userId, query);
  memoryCache.set(userId, memories);
}

/**
 * 대화 턴(user+assistant)을 Mem0에 적재 → 백그라운드 추출/갱신(ADD·UPDATE·DELETE·NOOP).
 * 호출 측은 await하지 말고 응답 flush 후(after) 실행할 것(non-blocking).
 */
export async function addMemory(
  userId: string,
  userText: string,
  assistantText: string,
): Promise<void> {
  const headers = authHeaders();
  if (!headers) return;
  try {
    const res = await fetch(`${BASE}/memories/`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        messages: [
          { role: 'user', content: userText },
          { role: 'assistant', content: assistantText },
        ],
        user_id: userId,
      }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!res.ok) console.error('[mem0] add HTTP', res.status);
  } catch (e) {
    console.error('[mem0] add 실패:', (e as Error).message);
  }
}
