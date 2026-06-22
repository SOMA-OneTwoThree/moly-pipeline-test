import 'server-only';
import { Letta } from '@letta-ai/letta-client';
import type { LLMOptions } from './types';

/**
 * Letta(stateful agent) 어댑터 — 일반 LLM provider 와 달리 페르소나·대화이력·메모리를
 * 서버 측 agent 가 소유한다. 따라서 system 프롬프트나 히스토리를 조립하지 않고,
 * 현재 user 발화 1개만 agent 에 보내고 응답 텍스트 delta 만 받아 흘려보낸다.
 * agent 가 `archival_memory_*`/core memory 를 갱신하며 맥락이 누적된다(영속성).
 */

let client: Letta | null = null;
function getClient(): Letta {
  if (!client) {
    const baseURL = process.env.LETTA_BASE_URL?.trim() || 'http://localhost:8283';
    // self-host 는 인증이 없으면 토큰을 검사하지 않는다. SECURE 모드면 LETTA_API_KEY 주입.
    client = new Letta({ baseURL, apiKey: process.env.LETTA_API_KEY?.trim() || 'letta-local' });
  }
  return client;
}

/**
 * text 입력 → Letta agent 응답 delta 스트림.
 * - agentId: opts.agentId(요청별) → env LETTA_AGENT_ID(기본 공유 agent) 순.
 * - `assistant_message` 청크의 텍스트만 yield(reasoning/tool/ping 은 무시).
 * - 종료 직전 `usage_statistics` 를 TokenUsage 로 매핑해 onUsage 1회 보고.
 * - 취소(opts.signal): 조용히 종료.
 */
export async function* streamLettaReply(text: string, opts?: LLMOptions): AsyncIterable<string> {
  const agentId = opts?.agentId?.trim() || process.env.LETTA_AGENT_ID?.trim();
  if (!agentId) {
    throw new Error(
      'LLM_PROVIDER=letta 인데 agent id가 없습니다. 요청의 sessionId/agentId 또는 env LETTA_AGENT_ID를 지정하세요.',
    );
  }

  try {
    const stream = await getClient().agents.messages.create(
      agentId,
      {
        messages: [{ role: 'user', content: text }],
        streaming: true,
        stream_tokens: true,
      },
      { signal: opts?.signal },
    );

    for await (const chunk of stream) {
      if (opts?.signal?.aborted) return;

      if (chunk.message_type === 'assistant_message') {
        const c = chunk.content;
        const t =
          typeof c === 'string'
            ? c
            : Array.isArray(c)
              ? c.map((p) => ('text' in p ? p.text : '')).join('')
              : '';
        if (t) yield t;
      } else if (chunk.message_type === 'usage_statistics' && opts?.onUsage) {
        opts.onUsage({
          model: process.env.LETTA_MODEL?.trim() || 'letta-agent',
          input_tokens: chunk.prompt_tokens ?? 0,
          output_tokens: chunk.completion_tokens ?? 0,
          cache_read_input_tokens: chunk.cached_input_tokens ?? undefined,
          cache_creation_input_tokens: chunk.cache_write_tokens ?? undefined,
        });
      }
    }
  } catch (err) {
    if (opts?.signal?.aborted) return;
    const e = err as { message?: string; statusCode?: number };
    console.error('[letta] stream error:', e.statusCode ?? '-', e.message ?? String(err));
    throw err instanceof Error ? err : new Error('letta stream failed');
  }
}
