"""WebChat API — SSE streaming endpoint for TornadoAI frontend.

POST /api/chat
  Body: {"session_id": "...", "message": "..."}
  Response: text/event-stream (SSE)
"""

import json
import logging
import uuid

from flask import Blueprint, request, Response, jsonify

from agent.chat_core import run_agent
from db.redis_client import get_redis

logger = logging.getLogger(__name__)

bp = Blueprint("web_chat", __name__)

_sessions: dict[str, dict] = {}
_SESSION_TTL = 6 * 3600
_SESSION_PREFIX = "tornado:session:"


def _session_key(sid: str) -> str:
    return f"{_SESSION_PREFIX}{sid}"


def _load_session(sid: str) -> dict | None:
    r = get_redis()
    if r:
        raw = r.get(_session_key(sid))
        if raw:
            return json.loads(raw)
    return _sessions.get(sid)


def _save_session(sid: str, session: dict):
    r = get_redis()
    if r:
        r.setex(
            _session_key(sid), _SESSION_TTL,
            json.dumps(session, ensure_ascii=False, default=str),
        )
    _sessions[sid] = session


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

    # Add user message to history
    history.append({"role": "user", "content": message})

    def generate():
        nonlocal history
        final_text = ""
        quick_replies = []
        ticket = None
        scenarios = None

        for chunk in run_agent(user_message=message, history=history):
            chunk_type = chunk.get("type")

            if chunk_type == "thinking":
                yield f"data: {json.dumps({'type': 'thinking'})}\n\n"

            elif chunk_type == "tool":
                yield f"data: {json.dumps({'type': 'tool', 'name': chunk.get('name'), 'label': chunk.get('label')}, ensure_ascii=False)}\n\n"

            elif chunk_type == "text":
                final_text = chunk.get("content", "")
                yield f"data: {json.dumps({'type': 'text', 'content': final_text}, ensure_ascii=False)}\n\n"

            elif chunk_type == "done":
                history = chunk.get("history", history)
                quick_replies = chunk.get("quick_replies", [])
                ticket = chunk.get("ticket")
                scenarios = chunk.get("scenarios")

        # Save session with updated history (keep last 16 messages)
        session["history"] = history[-16:]
        _save_session(sid, session)

        yield f"data: {json.dumps({'type': 'done', 'session_id': sid, 'quick_replies': quick_replies, 'ticket': ticket, 'scenarios': scenarios}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return Response(generate(), mimetype="text/event-stream")
