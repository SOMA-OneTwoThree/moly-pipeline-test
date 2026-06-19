import 'server-only';
import { getProvider, type ChatMessage, type LLMOptions } from '@/lib/llm';

const DEFAULT_SYSTEM_PROMPT =
  '사용자에게 한국어로 간결하고 자연스럽게, 말하기 좋은 문장으로 답하세요.';

/**
 * 공개 코어: 텍스트 입력 → LLM 응답 delta 스트림.
 * - `ChatMessage[]` 구성은 내부에 격리(소비자는 text만 전달).
 * - system 프롬프트는 env `SYSTEM_PROMPT`, 미설정 시 기본값.
 * - delta는 provider에서 받은 그대로 순서대로 전달(서버는 문장 버퍼링 안 함).
 */
export async function* generateReplyStream(
  text: string,
  opts?: LLMOptions,
): AsyncIterable<string> {
  const systemPrompt = process.env.SYSTEM_PROMPT?.trim() || DEFAULT_SYSTEM_PROMPT;

  const messages: ChatMessage[] = [
    { role: 'system', content: systemPrompt },
    { role: 'user', content: text },
  ];

  yield* getProvider().generateStream(messages, opts);
}
