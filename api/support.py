import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from flask import Blueprint, jsonify, request

from api.invite import verify_auth
from db.redis_client import get_redis

logger = logging.getLogger(__name__)

bp = Blueprint("support", __name__)

JST = timezone(timedelta(hours=9))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # group/channel id
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

_mem_seq = 0
_mem_tickets: dict[int, dict] = {}
_mem_replies_by_user: dict[str, list[dict]] = {}


def _now_iso() -> str:
    return datetime.now(JST).isoformat()


def _redis_ticket_key(tid: int) -> str:
    return f"support:ticket:{tid}"


def _redis_replies_key(uid: str) -> str:
    return f"support:replies:{uid}"


def _next_ticket_id() -> int:
    global _mem_seq
    r = get_redis()
    if r:
        try:
            return int(r.incr("support:seq"))
        except Exception:
            pass
    _mem_seq += 1
    return _mem_seq


def _save_ticket(t: dict):
    tid = int(t["id"])
    r = get_redis()
    if r:
        try:
            r.hset(_redis_ticket_key(tid), mapping={k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v) for k, v in t.items()})
            r.expire(_redis_ticket_key(tid), 60 * 60 * 24 * 14)  # 14 days
            return
        except Exception:
            pass
    _mem_tickets[tid] = t


def _load_ticket(tid: int) -> dict | None:
    r = get_redis()
    if r:
        try:
            raw = r.hgetall(_redis_ticket_key(tid))
            if not raw:
                return None
            t = {}
            for k, v in raw.items():
                try:
                    t[k] = json.loads(v)
                except Exception:
                    t[k] = v
            if "id" in t:
                t["id"] = int(t["id"])
            return t
        except Exception:
            return None
    return _mem_tickets.get(tid)


def _enqueue_reply(uid: str, payload: dict):
    r = get_redis()
    if r:
        try:
            r.rpush(_redis_replies_key(uid), json.dumps(payload, ensure_ascii=False))
            r.expire(_redis_replies_key(uid), 60 * 60 * 24 * 14)
            return
        except Exception:
            pass
    _mem_replies_by_user.setdefault(uid, []).append(payload)


def _drain_replies(uid: str) -> list[dict]:
    r = get_redis()
    if r:
        try:
            key = _redis_replies_key(uid)
            pipe = r.pipeline()
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            items, _ = pipe.execute()
            out = []
            for s in items or []:
                try:
                    out.append(json.loads(s))
                except Exception:
                    continue
            return out
        except Exception:
            return []
    out = _mem_replies_by_user.get(uid, [])
    _mem_replies_by_user[uid] = []
    return out


def _telegram_send(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=20)
        return resp.status_code == 200
    except Exception:
        return False


@bp.route("/api/support/tickets", methods=["POST"])
def create_ticket():
    payload = verify_auth()
    if not payload:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    message = str(body.get("message") or "").strip()
    page = str(body.get("page") or "").strip()

    if not message:
        return jsonify({"error": "message required"}), 400
    if len(message) > 2000:
        return jsonify({"error": "message too long"}), 400

    uid = payload.get("uid", "")
    tid = _next_ticket_id()

    ticket = {
        "id": tid,
        "user_id": uid,
        "status": "open",
        "message": message,
        "page": page,
        "created_at": _now_iso(),
    }
    _save_ticket(ticket)

    # Notify Telegram
    uid_short = uid[:8] + "..." if isinstance(uid, str) and len(uid) > 8 else uid
    tg_text = (
        f"📩 <b>新規お問い合わせ</b>\n"
        f"ticket: <b>#{tid}</b>\n"
        f"user: <code>{uid_short}</code>\n"
        f"page: <code>{page or '-'}</code>\n"
        f"\n<b>内容</b>\n{message}\n"
        f"\n<b>返信コマンド</b>\n/resolve {tid} 返信内容..."
    )
    sent = _telegram_send(tg_text)

    return jsonify({"ticket_id": tid, "sent": bool(sent)})


@bp.route("/api/support/replies", methods=["GET"])
def get_replies():
    payload = verify_auth()
    if not payload:
        return jsonify({"error": "unauthorized"}), 401
    uid = payload.get("uid", "")
    items = _drain_replies(uid)
    return jsonify({"replies": items, "count": len(items)})


@bp.route("/api/telegram/webhook", methods=["POST"])
def telegram_webhook():
    # Verify secret token (recommended)
    if TELEGRAM_WEBHOOK_SECRET:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if got != TELEGRAM_WEBHOOK_SECRET:
            return jsonify({"error": "forbidden"}), 403

    update = request.get_json(silent=True) or {}
    msg = (update.get("message") or {}).get("text") or ""
    msg = str(msg).strip()
    if not msg:
        return jsonify({"ok": True})

    if msg.startswith("/resolve"):
        # /resolve 12 reply text...
        parts = msg.split(maxsplit=2)
        if len(parts) < 3:
            return jsonify({"ok": True, "error": "usage: /resolve <id> <text>"})
        try:
            tid = int(parts[1])
        except Exception:
            return jsonify({"ok": True, "error": "invalid id"})

        reply_text = parts[2].strip()
        t = _load_ticket(tid)
        if not t:
            return jsonify({"ok": True, "error": "ticket not found"})

        uid = str(t.get("user_id") or "")
        _enqueue_reply(uid, {
            "ticket_id": tid,
            "text": reply_text,
            "resolved_at": _now_iso(),
        })

        t["status"] = "resolved"
        t["resolved_at"] = _now_iso()
        _save_ticket(t)

        return jsonify({"ok": True})

    return jsonify({"ok": True})
