"""WebChat API — SSE streaming endpoint for TornadoAI frontend.

POST /api/chat
  Body: {"session_id": "...", "message": "..."}
  Response: text/event-stream (SSE)
"""

import json
import logging
import uuid

from flask import Blueprint, request, Response, jsonify

from agent.engine import call_claude, build_system_prompt, extract_text, get_tool_blocks
from api.auth import verify_auth_header
from config import MAX_TOOL_TURNS
from db.redis_client import get_redis
from tools.executor import execute_tool

logger = logging.getLogger(__name__)

bp = Blueprint("web_chat", __name__)

_sessions: dict[str, dict] = {}
_redis = get_redis()
_SESSION_TTL = 6 * 3600
_SESSION_PREFIX = "tornado:session:"


def _session_key(sid: str) -> str:
    return f"{_SESSION_PREFIX}{sid}"


def _load_session(sid: str) -> dict | None:
    if _redis:
        raw = _redis.get(_session_key(sid))
        if raw:
            return json.loads(raw)
    return _sessions.get(sid)


def _save_session(sid: str, session: dict):
    if _redis:
        _redis.setex(_session_key(sid), _SESSION_TTL, json.dumps(session, ensure_ascii=False, default=str))
    _sessions[sid] = session


def _normalize_block(block):
    if isinstance(block, dict):
        return block
    if hasattr(block, "type"):
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "tool_use":
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return block


@bp.route("/api/chat/sessions", methods=["POST"])
def create_session():
    sid = str(uuid.uuid4())[:12]
    _save_session(sid, {"history": [], "created_at": None})
    return jsonify({"session_id": sid})


@bp.route("/api/chat", methods=["POST"])
def chat():
    body = request.get_json(silent=True) or {}
    sid = body.get("session_id", "")
    message = body.get("message", "").strip()

    if not sid or not message:
        return jsonify({"error": "session_id and message required"}), 400

    session = _load_session(sid) or {"history": []}
    history = session.get("history", [])

    # Add user message
    history.append({"role": "user", "content": message})

    def generate():
        nonlocal history

        yield f"data: {json.dumps({'type': 'thinking'})}\n\n"

        system = build_system_prompt()

        for turn in range(MAX_TOOL_TURNS):
            response = call_claude(history, system)

            if response.stop_reason == "end_turn":
                text = extract_text(response)
                history.append({
                    "role": "assistant",
                    "content": [_normalize_block(b) for b in response.content],
                })
                yield f"data: {json.dumps({'type': 'text', 'content': text}, ensure_ascii=False)}\n\n"
                break

            # Tool use
            tool_blocks = get_tool_blocks(response)
            if not tool_blocks:
                text = extract_text(response)
                history.append({
                    "role": "assistant",
                    "content": [_normalize_block(b) for b in response.content],
                })
                yield f"data: {json.dumps({'type': 'text', 'content': text}, ensure_ascii=False)}\n\n"
                break

            # Execute tools
            history.append({
                "role": "assistant",
                "content": [_normalize_block(b) for b in response.content],
            })

            tool_results = []
            for tb in tool_blocks:
                yield f"data: {json.dumps({'type': 'tool', 'name': tb.name})}\n\n"
                result = execute_tool(tb.name, tb.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tb.id,
                    "content": result,
                })

            history.append({"role": "user", "content": tool_results})

        # Save session
        session["history"] = history[-16:]  # Keep last 16 messages
        _save_session(sid, session)

        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(generate(), mimetype="text/event-stream")
