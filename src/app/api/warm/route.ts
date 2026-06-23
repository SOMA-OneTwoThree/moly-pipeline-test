export const runtime = 'nodejs';

import { getCachedMemories } from '@/lib/memory/mem0';

/**
 * POST /api/warm { user_id } — Mem0 캐시를 미리 채운다(prefetch).
 * 세션 시작 시 호출하면 첫 턴의 search 미스(~0.7s)를 제거 → 첫 응답 지연↓.
 * fail-safe: 실패해도 ok 반환(대화엔 영향 없음).
 */
export async function POST(request: Request): Promise<Response> {
  let userId: string | undefined;
  try {
    const body = (await request.json()) as { user_id?: unknown };
    userId =
      typeof body?.user_id === 'string' && body.user_id.trim().length > 0
        ? body.user_id.trim()
        : undefined;
  } catch {
    // 무시 — 아래에서 ok 반환
  }

  if (userId) {
    // 캐시 워밍(결과는 버림 — RAM 캐시만 채우면 됨).
    await getCachedMemories(userId, 'user profile and preferences');
  }

  return new Response(JSON.stringify({ ok: true }), {
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}
