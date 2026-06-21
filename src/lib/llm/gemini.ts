import 'server-only';
import { GoogleGenAI } from '@google/genai';
import type { ChatMessage, LLMOptions, LLMProvider } from './types';

const DEFAULT_MODEL = 'gemini-2.5-flash-lite';
const DEFAULT_MAX_TOKENS = 1024;

/**
 * Google Gemini 어댑터. 키는 GEMINI_API_KEY env(?key= 방식).
 * Anthropic 어댑터와 동일하게 system은 systemInstruction으로 분리, delta 텍스트만 yield하고
 * 종료 직전 usage(promptTokenCount/candidatesTokenCount)를 onUsage로 보고한다.
 */
export class GeminiProvider implements LLMProvider {
  private readonly ai: GoogleGenAI;
  private readonly model: string;
  private readonly maxTokens: number;

  constructor() {
    this.ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });
    this.model = process.env.LLM_MODEL?.trim() || DEFAULT_MODEL;
    const parsed = Number.parseInt(process.env.LLM_MAX_TOKENS ?? '', 10);
    this.maxTokens = Number.isInteger(parsed) && parsed > 0 ? parsed : DEFAULT_MAX_TOKENS;
  }

  async *generateStream(messages: ChatMessage[], opts?: LLMOptions): AsyncIterable<string> {
    const systemText = messages
      .filter((m) => m.role === 'system')
      .map((m) => m.content)
      .join('\n\n')
      .trim();

    const contents = messages
      .filter((m) => m.role !== 'system')
      .map((m) => ({
        role: m.role === 'assistant' ? 'model' : 'user',
        parts: [{ text: m.content }],
      }));

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let lastUsage: any = null;
    try {
      const stream = await this.ai.models.generateContentStream({
        model: this.model,
        contents,
        config: {
          ...(systemText ? { systemInstruction: systemText } : {}),
          maxOutputTokens: this.maxTokens,
        },
      });

      for await (const chunk of stream) {
        if (opts?.signal?.aborted) return; // 취소: 조용히 종료
        const t = chunk.text;
        if (t) yield t;
        if (chunk.usageMetadata) lastUsage = chunk.usageMetadata;
      }

      if (opts?.onUsage && lastUsage) {
        opts.onUsage({
          model: this.model,
          input_tokens: lastUsage.promptTokenCount ?? 0,
          output_tokens: lastUsage.candidatesTokenCount ?? 0,
          cache_read_input_tokens: lastUsage.cachedContentTokenCount ?? undefined,
        });
      }
    } catch (err) {
      if (opts?.signal?.aborted) return;
      const e = err as { message?: string; status?: number };
      console.error('[gemini] stream error:', e.status ?? '-', e.message ?? String(err));
      throw err instanceof Error ? err : new Error('Gemini stream failed');
    }
  }
}
