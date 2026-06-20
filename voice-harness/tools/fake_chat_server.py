"""moly-server /api/chat 계약을 흉내내는 가짜 서버(stdlib only).

Anthropic 키 없이 STT→LLM→TTS 배선/문장분할/지표를 오프라인 검증하기 위함.
실제 LLM 품질·지연 측정에는 진짜 moly-server를 쓴다.

실행:  python tools/fake_chat_server.py  # http://localhost:3000/api/chat
"""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_REPLY = "안녕! 만나서 반가워. 오늘은 뭐 하고 지냈어? 나는 네 얘기가 늘 궁금해."


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 조용히
        pass

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            text = body.get("text")
        except json.JSONDecodeError:
            self._json_error(400, "잘못된 JSON 본문입니다.")
            return
        if not isinstance(text, str) or not text.strip():
            self._json_error(400, "`text`는 비어 있지 않은 문자열이어야 합니다.")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        # delta를 토큰처럼 쪼개 약간의 지연과 함께 전송
        for i in range(0, len(_REPLY), 4):
            frame = json.dumps({"delta": _REPLY[i : i + 4]}, ensure_ascii=False)
            self.wfile.write(f"data: {frame}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.03)
        self.wfile.write(b'data: {"done":true}\n\n')
        self.wfile.flush()

    def _json_error(self, status: int, message: str):
        payload = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 3000), Handler)
    print("fake_chat_server → http://localhost:3000/api/chat  (Ctrl+C 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
