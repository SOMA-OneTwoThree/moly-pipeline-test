export const runtime = 'nodejs';

import { generateReplyStream } from '@/lib/chat/service';

/** 입력 상한(문자 수). 초과 시 스트림 시작 전 400. */
const MAX_INPUT_LENGTH = 4000;

function jsonError(message: string, status: number): Response {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
}

function sse(data: unknown): Uint8Array {
  return new TextEncoder().encode(`data: ${JSON.stringify(data)}\n\n`);
}

/**
 * POST /api/chat — 단일턴 텍스트 인풋 → SSE 텍스트 delta 스트림.
 * 계약: `data: {"delta":"..."}` (0개 이상) → `data: {"done":true}`,
 *       도중 오류는 `data: {"error":"..."}`로 종료(done 대체). 둘 중 정확히 하나로 끝난다.
 */
export async function POST(request: Request): Promise<Response> {
  // 1) 입력 파싱/검증 — 스트림 시작 전(여기서 실패하면 JSON 에러 응답).
  let text: unknown;
  try {
    const body = (await request.json()) as { text?: unknown };
    text = body?.text;
  } catch {
    return jsonError('잘못된 JSON 본문입니다.', 400);
  }

  if (typeof text !== 'string' || text.trim().length === 0) {
    return jsonError('`text`는 비어 있지 않은 문자열이어야 합니다.', 400);
  }
  if (text.length > MAX_INPUT_LENGTH) {
    return jsonError(`입력이 너무 깁니다(최대 ${MAX_INPUT_LENGTH}자).`, 400);
  }

  const signal = request.signal;
  const iterator = generateReplyStream(text, { signal })[Symbol.asyncIterator]();

  // 2) 첫 결과를 미리 당긴다(prime). async generator는 지연 평가라
  //    provider 구성 오류(알 수 없는 provider/키 누락)는 첫 next()에서야 throw된다.
  //    여기서 잡으면 헤더 전송 전이므로 "시작 전 오류 → 500 JSON"으로 확정할 수 있다.
  //    (첫 토큰 전 네트워크/API 오류도 동일하게 500. 첫 delta 이후 오류만 SSE error.)
  let first: IteratorResult<string>;
  try {
    first = await iterator.next();
  } catch (err) {
    await iterator.return?.(); // 항상 generator 정리(진행 중 fetch도 abort → 리소스 정리)
    if (signal.aborted) return new Response(null, { status: 499 }); // 토큰 전 클라 취소
    return jsonError(err instanceof Error ? err.message : '내부 오류', 500);
  }

  // 3) SSE 스트림 — pull 기반(자연 backpressure, 전체 응답을 메모리에 모으지 않음).
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      if (signal.aborted) {
        controller.close();
        return;
      }
      if (first.done) {
        // 빈 응답(delta 0개) → done:true = 성공.
        controller.enqueue(sse({ done: true }));
        controller.close();
      } else {
        controller.enqueue(sse({ delta: first.value }));
      }
    },
    async pull(controller) {
      try {
        if (signal.aborted) {
          controller.close();
          return;
        }
        const { value, done } = await iterator.next();
        if (done) {
          controller.enqueue(sse({ done: true }));
          controller.close();
          return;
        }
        controller.enqueue(sse({ delta: value }));
      } catch (err) {
        if (signal.aborted) {
          // 클라이언트 취소: 추가 이벤트 없이 종료.
          controller.close();
          return;
        }
        const message = err instanceof Error ? err.message : 'LLM 스트림 오류';
        controller.enqueue(sse({ error: message }));
        controller.close();
      }
    },
    async cancel() {
      // 소비자/런타임이 스트림을 취소 → generator 정리.
      await iterator.return?.();
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      // 리버스 프록시(nginx 등) 버퍼링 방지 — delta가 즉시 도착하도록.
      'X-Accel-Buffering': 'no',
    },
  });
}
