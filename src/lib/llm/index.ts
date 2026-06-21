import 'server-only';
import type { LLMProvider } from './types';
import { AnthropicProvider } from './anthropic';
import { GeminiProvider } from './gemini';
import { OpenAICompatibleProvider } from './openai_compat';

export type { ChatMessage, LLMOptions, LLMProvider, TokenUsage } from './types';

/**
 * env `LLM_PROVIDER`로 provider를 선택한다(기본 `anthropic`).
 * LLM 추상화는 유지 — 새 provider를 추가하려면 여기 분기만 늘리면 된다.
 * 알 수 없는 값/키 누락은 즉시 throw(fail-fast)하여 잘못된 구성을 일찍 드러낸다.
 */
export function getProvider(): LLMProvider {
  const name = (process.env.LLM_PROVIDER ?? 'anthropic').trim().toLowerCase();

  switch (name) {
    case 'anthropic':
      if (!process.env.ANTHROPIC_API_KEY) {
        throw new Error(
          'LLM_PROVIDER=anthropic 인데 ANTHROPIC_API_KEY가 없습니다. .env에 키를 주입하세요.',
        );
      }
      return new AnthropicProvider();
    case 'gemini':
      if (!process.env.GEMINI_API_KEY) {
        throw new Error('LLM_PROVIDER=gemini 인데 GEMINI_API_KEY가 없습니다. .env에 키를 주입하세요.');
      }
      return new GeminiProvider();
    case 'groq':
      if (!process.env.GROQ_API_KEY) {
        throw new Error('LLM_PROVIDER=groq 인데 GROQ_API_KEY가 없습니다.');
      }
      return new OpenAICompatibleProvider({
        baseURL: 'https://api.groq.com/openai/v1',
        apiKey: process.env.GROQ_API_KEY,
        model: process.env.LLM_MODEL?.trim() || 'llama-3.3-70b-versatile',
        providerName: 'groq',
      });
    case 'openai':
      if (!process.env.OPENAI_API_KEY) {
        throw new Error('LLM_PROVIDER=openai 인데 OPENAI_API_KEY가 없습니다.');
      }
      return new OpenAICompatibleProvider({
        baseURL: 'https://api.openai.com/v1',
        apiKey: process.env.OPENAI_API_KEY,
        model: process.env.LLM_MODEL?.trim() || 'gpt-4o-mini',
        providerName: 'openai',
      });
    case 'deepseek':
      if (!process.env.DEEPSEEK_API_KEY) {
        throw new Error('LLM_PROVIDER=deepseek 인데 DEEPSEEK_API_KEY가 없습니다.');
      }
      return new OpenAICompatibleProvider({
        baseURL: 'https://api.deepseek.com',
        apiKey: process.env.DEEPSEEK_API_KEY,
        model: process.env.LLM_MODEL?.trim() || 'deepseek-chat',
        providerName: 'deepseek',
      });
    default:
      throw new Error(
        `알 수 없는 LLM_PROVIDER: "${name}" (사용 가능: anthropic, gemini, groq, openai, deepseek)`,
      );
  }
}
