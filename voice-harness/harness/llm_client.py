"""moly-server /api/chat SSE 소비자.

계약(README/route.ts):
  POST { text } → text/event-stream
  data: {"delta":"..."}  (0개 이상)
  data: {"done":true}    정상 종료
  data: {"error":"..."}  비정상 종료(done 대체) — 직전 delta는 유효
  스트림은 done 또는 error 중 정확히 하나로 끝난다.
멀티바이트는 청크 경계에서 잘릴 수 있으므로 httpx의 텍스트 디코딩 + '\n\n' 프레이밍으로 누적.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

import httpx


class LLMStreamError(RuntimeError):
    """SSE error 이벤트 또는 시작 전 HTTP 오류."""


@dataclass
class Delta:
    text: str


def stream_chat(
    url: str,
    text: str,
    timeout: float = 60.0,
    on_usage: Optional[Callable[[dict], None]] = None,
    user_id: Optional[str] = None,
) -> Iterator[str]:
    """text를 보내고 delta 문자열을 순서대로 yield.

    user_id가 있으면 함께 보내 서버가 Mem0 기억을 조회/주입한다(영속성).
    정상 종료: 제너레이터 자연 종료(done 수신). done 이벤트에 usage가 있으면 on_usage(usage) 호출.
    비정상 종료: LLMStreamError raise(error 수신 — 단, 직전까지 yield된 delta는 유효).
    """
    body: dict = {"text": text}
    if user_id:
        body["user_id"] = user_id
    buf = ""
    with httpx.Client(timeout=timeout) as client:
        with client.stream(
            "POST",
            url,
            json=body,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            # 시작 전 오류: 스트림 헤더가 JSON 에러(text/event-stream 아님)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code != 200 or "text/event-stream" not in ctype:
                body = resp.read().decode("utf-8", "replace")
                msg = _extract_error(body) or f"HTTP {resp.status_code}"
                raise LLMStreamError(msg)

            for chunk in resp.iter_text():
                buf += chunk
                while True:
                    sep = buf.find("\n\n")
                    if sep == -1:
                        break
                    frame = buf[:sep]
                    buf = buf[sep + 2 :]
                    payload = _parse_frame(frame)
                    if payload is None:
                        continue
                    if "error" in payload:
                        raise LLMStreamError(str(payload["error"]))
                    if payload.get("done"):
                        if on_usage and isinstance(payload.get("usage"), dict):
                            on_usage(payload["usage"])
                        return
                    if "delta" in payload and payload["delta"]:
                        yield payload["delta"]


def _parse_frame(frame: str) -> Optional[dict]:
    line = next((l for l in frame.split("\n") if l.startswith("data:")), None)
    if not line:
        return None
    try:
        return json.loads(line[5:].strip())
    except json.JSONDecodeError:
        return None


def _extract_error(body: str) -> Optional[str]:
    try:
        return json.loads(body).get("error")
    except (json.JSONDecodeError, AttributeError):
        return None
