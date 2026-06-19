import 'server-only';
import type { LLMProvider } from './types';
import { AnthropicProvider } from './anthropic';

export type { ChatMessage, LLMOptions, LLMProvider } from './types';

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
    default:
      throw new Error(`알 수 없는 LLM_PROVIDER: "${name}" (사용 가능: anthropic)`);
  }
}
