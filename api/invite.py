"""Invite code authentication API."""

import hashlib
import hmac
import json
import logging
import os
import time

from flask import Blueprint, request, jsonify

from db.supabase_client import get_client

logger = logging.getLogger(__name__)

bp = Blueprint("invite", __name__)

WEB_AUTH_SECRET = os.getenv("WEB_AUTH_SECRET", "")
if not WEB_AUTH_SECRET:
    WEB_AUTH_SECRET = os.urandom(32).hex()

TOKEN_EXPIRY = 30 * 24 * 3600  # 30 days


def _create_token(user_id: str) -> str:
    payload = {
        "uid": user_id,
        "exp": int(time.time()) + TOKEN_EXPIRY,
    }
    data = json.dumps(payload, separators=(",", ":"))
    sig = hmac.new(WEB_AUTH_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data.encode().hex()}.{sig}"


def _verify_token(token: str) -> dict | None:
    try:
        data_hex, sig = token.split(".", 1)
        data = bytes.fromhex(data_hex).decode()
        expected = hmac.new(WEB_AUTH_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(data)
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def verify_auth() -> dict | None:
    """Verify Bearer token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return _verify_token(auth[7:])


@bp.route("/api/auth/invite", methods=["POST"])
def redeem_invite():
    """Redeem an invite code and get auth token."""
    body = request.get_json(silent=True) or {}
    code = body.get("code", "").strip().upper()

    if not code:
        return jsonify({"error": "招待コードを入力してください"}), 400

    sb = get_client()

    # Find the code
    res = sb.table("invite_codes") \
        .select("id, code, used_by, used_at") \
        .eq("code", code) \
        .limit(1) \
        .execute()

    if not res.data:
        return jsonify({"error": "無効な招待コードです"}), 400

    invite = res.data[0]

    # Already used — but return token for the existing user (re-login)
    if invite.get("used_by"):
        user_id = invite["used_by"]
        user_res = sb.table("users").select("id, display_name").eq("id", user_id).limit(1).execute()
        if user_res.data:
            token = _create_token(user_id)
            return jsonify({
                "token": token,
                "user": {"id": user_id, "display_name": user_res.data[0].get("display_name", "")},
                "message": "おかえりなさい",
            })
        return jsonify({"error": "アカウントエラー。サポートにお問い合わせください"}), 400

    # Create new user
    user_res = sb.table("users").insert({
        "display_name": f"会員{code[-4:]}",
        "plan": "premium",
    }).execute()

    if not user_res.data:
        return jsonify({"error": "アカウント作成に失敗しました"}), 500

    user_id = user_res.data[0]["id"]

    # Mark code as used
    sb.table("invite_codes").update({
        "used_by": user_id,
        "used_at": "now()",
    }).eq("id", invite["id"]).execute()

    token = _create_token(user_id)

    logger.info(f"Invite redeemed: code={code} user={user_id}")

    return jsonify({
        "token": token,
        "user": {"id": user_id, "display_name": f"会員{code[-4:]}"},
        "message": "ようこそトルネードAIへ",
    })


@bp.route("/api/auth/me", methods=["GET"])
def auth_me():
    """Verify token and return user info."""
    payload = verify_auth()
    if not payload:
        return jsonify({"error": "unauthorized"}), 401

    sb = get_client()
    user_res = sb.table("users").select("id, display_name, plan").eq("id", payload["uid"]).limit(1).execute()
    if not user_res.data:
        return jsonify({"error": "user not found"}), 404

    user = user_res.data[0]
    return jsonify({
        "id": user["id"],
        "display_name": user["display_name"],
        "plan": user["plan"],
    })
