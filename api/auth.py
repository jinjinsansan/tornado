"""LINE Login OAuth — token creation/verification for TornadoAI."""

import hashlib
import hmac
import json
import logging
import os
import time

import requests
from flask import Blueprint, request, jsonify

from config import LINE_LOGIN_CHANNEL_ID, LINE_LOGIN_CHANNEL_SECRET
from db.supabase_client import get_client

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

WEB_AUTH_SECRET = os.getenv("WEB_AUTH_SECRET", "")
if not WEB_AUTH_SECRET:
    logger.warning("WEB_AUTH_SECRET not set — using random key (tokens won't persist across restarts)")
    WEB_AUTH_SECRET = os.urandom(32).hex()

TOKEN_EXPIRY = 7 * 24 * 3600


def _create_token(profile_id: str, line_user_id: str, display_name: str) -> str:
    payload = {
        "pid": profile_id,
        "lid": line_user_id,
        "name": display_name,
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


def verify_auth_header() -> dict | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return _verify_token(auth[7:])


@bp.route("/api/chatauth/line", methods=["POST"])
def auth_line():
    """Exchange LINE auth code for JWT token."""
    body = request.get_json(silent=True) or {}
    code = body.get("code", "")
    redirect_uri = body.get("redirect_uri", "")

    if not code:
        return jsonify({"error": "code required"}), 400

    # Exchange code for access token
    token_resp = requests.post("https://api.line.me/oauth2/v2.1/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": LINE_LOGIN_CHANNEL_ID,
        "client_secret": LINE_LOGIN_CHANNEL_SECRET,
    })

    if token_resp.status_code != 200:
        return jsonify({"error": "token exchange failed"}), 400

    access_token = token_resp.json().get("access_token", "")

    # Get user profile
    profile_resp = requests.get("https://api.line.me/v2/profile", headers={
        "Authorization": f"Bearer {access_token}",
    })

    if profile_resp.status_code != 200:
        return jsonify({"error": "profile fetch failed"}), 400

    profile = profile_resp.json()
    line_user_id = profile.get("userId", "")
    display_name = profile.get("displayName", "")

    # Upsert user in Supabase
    sb = get_client()
    res = sb.table("users").select("id").eq("line_user_id", line_user_id).limit(1).execute()

    if res.data:
        user_id = res.data[0]["id"]
        sb.table("users").update({
            "display_name": display_name,
            "last_active_at": "now()",
        }).eq("id", user_id).execute()
    else:
        insert_res = sb.table("users").insert({
            "line_user_id": line_user_id,
            "display_name": display_name,
        }).execute()
        user_id = insert_res.data[0]["id"]

    token = _create_token(user_id, line_user_id, display_name)

    return jsonify({
        "token": token,
        "user": {
            "id": user_id,
            "display_name": display_name,
        },
    })


@bp.route("/api/chatauth/me", methods=["GET"])
def auth_me():
    payload = verify_auth_header()
    if not payload:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({
        "id": payload["pid"],
        "display_name": payload["name"],
    })
