import 'server-only';
import Anthropic from '@anthropic-ai/sdk';
import type { ChatMessage, LLMOptions, LLMProvider } from './types';

const DEFAULT_MODEL = 'claude-sonnet-4-6';
const DEFAULT_MAX_TOKENS = 1024;

/**
 * 실제 Anthropic Messages API 어댑터.
 * 키는 ANTHROPIC_API_KEY env에서 SDK가 자동 로드한다(코드/로그에 키를 두지 않음).
 */
export class AnthropicProvider implements LLMProvider {
  private readonly client: Anthropic;
  private readonly model: string;
  private readonly maxTokens: number;

  constructor() {
    this.client = new Anthropic();
    this.model = process.env.LLM_MODEL?.trim() || DEFAULT_MODEL;
    const parsed = Number.parseInt(process.env.LLM_MAX_TOKENS ?? '', 10);
    this.maxTokens = Number.isInteger(parsed) && parsed > 0 ? parsed : DEFAULT_MAX_TOKENS;
  }

  async *generateStream(messages: ChatMessage[], opts?: LLMOptions): AsyncIterable<string> {
    // system 역할 → top-level system 파라미터로 분리, 나머지는 messages로.
    const systemText = messages
      .filter((m) => m.role === 'system')
      .map((m) => m.content)
      .join('\n\n')
      .trim();

    const chatMessages = messages
      .filter((m): m is ChatMessage & { role: 'user' | 'assistant' } => m.role !== 'system')
      .map((m) => ({ role: m.role, content: m.content }));

    const stream = this.client.messages.stream(
      {
        model: this.model,
        max_tokens: this.maxTokens,
        ...(systemText ? { system: systemText } : {}),
        messages: chatMessages,
      },
      { signal: opts?.signal },
    );

    try {
      for await (const event of stream) {
        if (opts?.signal?.aborted) {
          stream.abort();
          return; // 취소: 조용히 종료(진행 중 fetch도 abort → 리소스 정리)
        }
        // text_delta만 yield. thinking/thinking_delta/input_json_delta 등 비-text 블록은 무시.
        if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') {
          yield event.delta.text;
        }
      }

      // 스트림 종료 후 단 한 번 terminal 결정(SDK가 이벤트 누적 → 이중소비 아님).
      const final = await stream.finalMessage();

      // refusal → 비정상 종료(에러). delta 0개 가정(Sonnet 4.6).
      // mid-output refusal 모델로 교체 시: 이미 yield된 partial delta를 소비자가 폐기하도록
      // 별도 신호가 필요(현재 계약은 refusal 직전 delta 없음을 전제).
      if (final.stop_reason === 'refusal') {
        throw new Error('LLM refused to respond (stop_reason=refusal)');
      }

      // 정상 종료 직전 토큰 usage 1회 보고(비용·tokens/sec 측정용). 키/내용은 로깅하지 않음.
      if (opts?.onUsage) {
        const u = final.usage;
        opts.onUsage({
          model: this.model,
          input_tokens: u?.input_tokens ?? 0,
          output_tokens: u?.output_tokens ?? 0,
          cache_read_input_tokens: u?.cache_read_input_tokens ?? undefined,
          cache_creation_input_tokens: u?.cache_creation_input_tokens ?? undefined,
        });
      }
      // end_turn / stop_sequence / max_tokens / 빈 응답(text_delta 0개) → 정상 종료 → 코어가 done.
    } catch (err) {
      if (opts?.signal?.aborted) return; // 취소는 정상 종료로 간주(에러 아님)
      // 키/요청 설정/process.env 절대 로깅 금지. message/status만 기록.
      const e = err as { message?: string; status?: number };
      console.error('[anthropic] stream error:', e.status ?? '-', e.message ?? String(err));
      throw err instanceof Error ? err : new Error('Anthropic stream failed');
    }
  }
}
