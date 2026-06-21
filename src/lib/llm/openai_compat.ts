import 'server-only';
import OpenAI from 'openai';
import type { ChatMessage, LLMOptions, LLMProvider } from './types';

const DEFAULT_MAX_TOKENS = 1024;

/**
 * OpenAI 호환 API 범용 어댑터 — baseURL/key/model만 바꾸면 Groq·OpenAI·DeepSeek·Cerebras·xAI 등
 * 모두 같은 코드로 처리한다. system/user/assistant 역할은 OpenAI chat 포맷 그대로.
 * 종료 직전 usage(prompt_tokens/completion_tokens)를 onUsage로 보고.
 */
export class OpenAICompatibleProvider implements LLMProvider {
  private readonly client: OpenAI;
  private readonly model: string;
  private readonly maxTokens: number;
  private readonly providerName: string;

  constructor(opts: { baseURL: string; apiKey?: string; model: string; providerName: string }) {
    this.client = new OpenAI({ baseURL: opts.baseURL, apiKey: opts.apiKey });
    this.model = opts.model;
    this.providerName = opts.providerName;
    const parsed = Number.parseInt(process.env.LLM_MAX_TOKENS ?? '', 10);
    this.maxTokens = Number.isInteger(parsed) && parsed > 0 ? parsed : DEFAULT_MAX_TOKENS;
  }

  async *generateStream(messages: ChatMessage[], opts?: LLMOptions): AsyncIterable<string> {
    const msgs = messages.map((m) => ({ role: m.role, content: m.content }));
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let usage: any = null;
    try {
      const stream = await this.client.chat.completions.create(
        {
          model: this.model,
          messages: msgs,
          max_tokens: this.maxTokens,
          stream: true,
          stream_options: { include_usage: true },
        },
        { signal: opts?.signal },
      );

      for await (const chunk of stream) {
        if (opts?.signal?.aborted) return;
        const t = chunk.choices[0]?.delta?.content;
        if (t) yield t;
        if (chunk.usage) usage = chunk.usage;
      }

      if (opts?.onUsage && usage) {
        opts.onUsage({
          model: this.model,
          input_tokens: usage.prompt_tokens ?? 0,
          output_tokens: usage.completion_tokens ?? 0,
        });
      }
    } catch (err) {
      if (opts?.signal?.aborted) return;
      const e = err as { message?: string; status?: number };
      console.error(`[${this.providerName}] stream error:`, e.status ?? '-', e.message ?? String(err));
      throw err instanceof Error ? err : new Error(`${this.providerName} stream failed`);
    }
  }
}
