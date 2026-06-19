'use client';

import { useRef, useState } from 'react';

type Status = 'idle' | 'streaming' | 'done' | 'error';

export default function Home() {
  const [input, setInput] = useState('');
  const [reply, setReply] = useState('');
  const [status, setStatus] = useState<Status>('idle');
  const [errorMsg, setErrorMsg] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  async function send() {
    const text = input.trim();
    if (!text || status === 'streaming') return;

    setReply('');
    setErrorMsg('');
    setStatus('streaming');

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
        signal: controller.signal,
      });

      // 시작 전 오류(JSON): 스트림 시작 안 함.
      if (!res.ok || !res.body) {
        let message = `요청 실패 (HTTP ${res.status})`;
        try {
          const data = (await res.json()) as { error?: string };
          if (data?.error) message = data.error;
        } catch {
          /* ignore */
        }
        setErrorMsg(message);
        setStatus('error');
        return;
      }

      // SSE 소비: getReader + stream 디코더 + \n\n 경계 분할(청크 경계서 프레임 잘림 대비).
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        let sep: number;
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);

          const line = frame.split('\n').find((l) => l.startsWith('data:'));
          if (!line) continue;

          let payload: { delta?: string; done?: boolean; error?: string };
          try {
            payload = JSON.parse(line.slice(5).trim());
          } catch {
            continue; // 파싱 불가 프레임(주석/heartbeat 등) 무시
          }

          if (payload.error !== undefined) {
            setErrorMsg(payload.error);
            setStatus('error');
            return;
          }
          if (payload.done) {
            setStatus('done');
            return;
          }
          if (payload.delta !== undefined) {
            setReply((prev) => prev + payload.delta);
          }
        }
      }

      // \n\n 종료 프레임 없이 스트림이 끝난 경우(비정상)도 done 취급하지 않음.
      setStatus((s) => (s === 'streaming' ? 'done' : s));
    } catch (err) {
      if (controller.signal.aborted) {
        setStatus('idle');
        return;
      }
      setErrorMsg(err instanceof Error ? err.message : '네트워크 오류');
      setStatus('error');
    } finally {
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  return (
    <main
      style={{
        maxWidth: 680,
        margin: '0 auto',
        padding: '2rem 1.25rem',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        lineHeight: 1.6,
      }}
    >
      <h1 style={{ fontSize: '1.4rem', marginBottom: '0.25rem' }}>moly · LLM 응답 파이프라인</h1>
      <p style={{ color: '#666', fontSize: '0.9rem', marginTop: 0 }}>
        텍스트 인풋 → LLM → 스트리밍 텍스트 아웃풋 (STT-LLM-TTS 중 LLM 파트 테스트 UI)
      </p>

      <div style={{ display: 'flex', gap: 8, marginTop: '1.25rem' }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') send();
          }}
          placeholder="메시지를 입력하세요…"
          style={{
            flex: 1,
            padding: '0.6rem 0.75rem',
            fontSize: '1rem',
            border: '1px solid #ccc',
            borderRadius: 8,
          }}
        />
        {status === 'streaming' ? (
          <button onClick={stop} style={btnStyle('#b00')}>
            중지
          </button>
        ) : (
          <button onClick={send} style={btnStyle('#0070f3')} disabled={!input.trim()}>
            전송
          </button>
        )}
      </div>

      <div style={{ marginTop: '0.75rem', fontSize: '0.8rem', color: '#888' }}>
        상태: <code>{status}</code>
      </div>

      {reply && (
        <div
          style={{
            marginTop: '1rem',
            padding: '1rem',
            background: '#f6f8fa',
            color: '#111',
            borderRadius: 8,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {reply}
          {status === 'streaming' && <span style={{ opacity: 0.5 }}>▌</span>}
        </div>
      )}

      {status === 'error' && (
        <div
          style={{
            marginTop: '1rem',
            padding: '0.75rem 1rem',
            background: '#fff0f0',
            color: '#b00',
            border: '1px solid #f3c2c2',
            borderRadius: 8,
            whiteSpace: 'pre-wrap',
          }}
        >
          ⚠️ {errorMsg}
        </div>
      )}
    </main>
  );
}

function btnStyle(bg: string): React.CSSProperties {
  return {
    padding: '0.6rem 1rem',
    fontSize: '1rem',
    color: '#fff',
    background: bg,
    border: 'none',
    borderRadius: 8,
    cursor: 'pointer',
  };
}
