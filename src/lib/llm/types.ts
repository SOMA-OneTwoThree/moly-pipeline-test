import 'server-only';

/**
 * 내부용 메시지 표현. provider 경계 밖으로 노출하지 않는다.
 * 공개 코어(generateReplyStream)는 text 입력만 받는다.
 */
export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface LLMOptions {
  /** 취소 신호. yield 사이에서 확인하여 중단 시 즉시 정리한다. */
  signal?: AbortSignal;
}

export interface LLMProvider {
  /**
   * 메시지에 대한 응답을 텍스트 delta 조각으로 스트리밍한다.
   * - 조각은 토큰/단어/문장 경계를 보장하지 않는 임의 길이 문자열.
   * - 정상 종료: generator가 자연 종료 → 코어가 `done`.
   * - 비정상 종료: throw → 코어/라우트가 `error`.
   * - 취소(opts.signal): 조용히 종료(추가 yield/throw 없음).
   */
  generateStream(messages: ChatMessage[], opts?: LLMOptions): AsyncIterable<string>;
}
