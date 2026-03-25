"""Invite code authentication API."""

import hashlib
import hmac
import json
import logging
import os
import time
import secrets
import base64
from datetime import datetime, timezone

import requests
from flask import Blueprint, request, jsonify

from config import LINE_LOGIN_CHANNEL_ID, LINE_LOGIN_CHANNEL_SECRET, LINE_LOGIN_REDIRECT_URI
from db.supabase_client import get_client

logger = logging.getLogger(__name__)

bp = Blueprint("invite", __name__)

WEB_AUTH_SECRET = os.getenv("WEB_AUTH_SECRET", "")
if not WEB_AUTH_SECRET:
    WEB_AUTH_SECRET = os.urandom(32).hex()

TOKEN_EXPIRY = 30 * 24 * 3600  # 30 days
LINE_STATE_EXPIRY = 10 * 60  # 10 minutes
ACTIVATION_MAX_ATTEMPTS = 5


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


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _pin_hash(pin: str) -> str:
    # 4桁PINは総当たりされやすいので、DBにはHMACハッシュのみ保存
    return hmac.new(WEB_AUTH_SECRET.encode(), pin.encode("utf-8"), hashlib.sha256).hexdigest()


def _sign_state(payload: dict) -> str:
    data = json.dumps(payload, separators=(",", ":"))
    sig = hmac.new(WEB_AUTH_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data.encode().hex()}.{sig}"


def _verify_state(token: str) -> dict | None:
    return _verify_token(token)


def _pkce_generate() -> tuple[str, str]:
    # RFC7636: verifier 43-128 chars (base64url)
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8").rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).decode("utf-8").rstrip("=")
    return verifier, challenge


def _build_line_auth_url(state: str, code_challenge: str) -> str:
    if not LINE_LOGIN_CHANNEL_ID:
        raise RuntimeError("LINE_LOGIN_CHANNEL_ID not set")
    if not LINE_LOGIN_CHANNEL_SECRET:
        raise RuntimeError("LINE_LOGIN_CHANNEL_SECRET not set")

    # LINE Login v2.1 authorize
    # https://developers.line.biz/en/docs/line-login/integrate-line-login/
    from urllib.parse import urlencode
    q = urlencode({
        "response_type": "code",
        "client_id": LINE_LOGIN_CHANNEL_ID,
        "redirect_uri": LINE_LOGIN_REDIRECT_URI,
        "scope": "openid profile",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    return f"https://access.line.me/oauth2/v2.1/authorize?{q}"


@bp.route("/api/auth/activate", methods=["POST"])
def activate_link():
    """Verify activation link token + PIN, then return LINE auth URL for linking."""
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    pin = (body.get("pin") or "").strip()

    if not token:
        return jsonify({"error": "token required"}), 400
    if not (pin.isdigit() and len(pin) == 4):
        return jsonify({"error": "PINは4桁の数字で入力してください"}), 400

    sb = get_client()
    token_hash = _sha256_hex(token)
    res = sb.table("activation_links") \
        .select("id, pin_hash, status, attempts, locked_at, used_by, used_at, expires_at") \
        .eq("token_hash", token_hash) \
        .limit(1) \
        .execute()

    if not res.data:
        return jsonify({"error": "無効なアクティベーションURLです"}), 400

    row = res.data[0]
    if row.get("used_by") or row.get("used_at") or row.get("status") == "used":
        return jsonify({"error": "このアクティベーションは既に使用済みです"}), 400
    if row.get("locked_at") or row.get("status") == "locked":
        return jsonify({"error": "PIN試行回数が上限に達しました。サポートにご連絡ください"}), 400

    # Expiry (Supabase returns ISO string)
    try:
        expires_at = row.get("expires_at")
        exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00")) if isinstance(expires_at, str) else None
        if exp_dt and exp_dt < datetime.now(timezone.utc):
            return jsonify({"error": "このアクティベーションURLは期限切れです"}), 400
    except Exception:
        pass

    if not hmac.compare_digest(row.get("pin_hash", ""), _pin_hash(pin)):
        attempts = int(row.get("attempts") or 0) + 1
        updates = {"attempts": attempts}
        remaining = max(0, ACTIVATION_MAX_ATTEMPTS - attempts)
        if attempts >= ACTIVATION_MAX_ATTEMPTS:
            updates["locked_at"] = "now()"
            updates["status"] = "locked"
        sb.table("activation_links").update(updates).eq("id", row["id"]).execute()
        if remaining == 0:
            return jsonify({"error": "PIN試行回数が上限に達しました。サポートにご連絡ください"}), 400
        return jsonify({"error": f"PINが違います（残り{remaining}回）", "remaining": remaining}), 400

    # Build LINE auth URL with signed state (includes code_verifier)
    verifier, challenge = _pkce_generate()
    state = _sign_state({
        "mode": "activate",
        "aid": row["id"],
        "cv": verifier,
        "exp": int(time.time()) + LINE_STATE_EXPIRY,
    })

    try:
        url = _build_line_auth_url(state=state, code_challenge=challenge)
    except Exception as e:
        logger.exception("LINE auth url build failed")
        return jsonify({"error": str(e)}), 500

    return jsonify({"url": url})


@bp.route("/api/auth/line/start", methods=["GET"])
def line_start():
    """Start LINE login for returning users."""
    verifier, challenge = _pkce_generate()
    state = _sign_state({
        "mode": "login",
        "cv": verifier,
        "exp": int(time.time()) + LINE_STATE_EXPIRY,
    })
    try:
        url = _build_line_auth_url(state=state, code_challenge=challenge)
    except Exception as e:
        logger.exception("LINE auth url build failed")
        return jsonify({"error": str(e)}), 500
    return jsonify({"url": url})


@bp.route("/api/auth/line/exchange", methods=["POST"])
def line_exchange():
    """Exchange LINE auth code/state for TornadoAI token (login or activation)."""
    body = request.get_json(silent=True) or {}
    code = (body.get("code") or "").strip()
    state_token = (body.get("state") or "").strip()

    if not code or not state_token:
        return jsonify({"error": "code/state required"}), 400

    state = _verify_state(state_token)
    if not state:
        return jsonify({"error": "invalid state"}), 400

    mode = state.get("mode")
    code_verifier = state.get("cv")
    activation_id = state.get("aid")
    if mode not in ("login", "activate") or not code_verifier:
        return jsonify({"error": "invalid state payload"}), 400

    # Exchange code -> tokens (PKCE)
    tok = requests.post("https://api.line.me/oauth2/v2.1/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": LINE_LOGIN_REDIRECT_URI,
        "client_id": LINE_LOGIN_CHANNEL_ID,
        "client_secret": LINE_LOGIN_CHANNEL_SECRET,
        "code_verifier": code_verifier,
    }, timeout=15)

    if tok.status_code != 200:
        return jsonify({"error": "LINE token exchange failed"}), 400

    tok_json = tok.json()
    id_token = tok_json.get("id_token", "")
    if not id_token:
        return jsonify({"error": "LINE id_token missing"}), 400

    # Verify id_token using LINE endpoint (avoids extra JWT libs)
    ver = requests.post("https://api.line.me/oauth2/v2.1/verify", data={
        "id_token": id_token,
        "client_id": LINE_LOGIN_CHANNEL_ID,
    }, timeout=15)

    if ver.status_code != 200:
        return jsonify({"error": "LINE id_token verify failed"}), 400

    claims = ver.json()
    line_user_id = claims.get("sub", "")
    display_name = claims.get("name", "") or "会員"
    if not line_user_id:
        return jsonify({"error": "LINE user id missing"}), 400

    sb = get_client()

    # Returning user login
    if mode == "login":
        res = sb.table("users").select("id, display_name, plan, role").eq("line_user_id", line_user_id).limit(1).execute()
        if not res.data:
            return jsonify({"error": "未アクティベーションです。購入者メールのURLから初回登録してください"}), 403

        user = res.data[0]
        sb.table("users").update({
            "display_name": display_name,
            "last_active_at": "now()",
        }).eq("id", user["id"]).execute()

        token = _create_token(user["id"])
        return jsonify({
            "token": token,
            "user": {
                "id": user["id"],
                "display_name": user.get("display_name") or display_name,
                "plan": user.get("plan", ""),
                "role": user.get("role", "member"),
            },
        })

    # Activation: bind activation link -> new premium user
    if not activation_id:
        return jsonify({"error": "activation id missing"}), 400

    act_res = sb.table("activation_links") \
        .select("id, status, locked_at, used_by, used_at, expires_at, metadata") \
        .eq("id", activation_id) \
        .limit(1) \
        .execute()

    if not act_res.data:
        return jsonify({"error": "activation not found"}), 400

    act = act_res.data[0]
    if act.get("locked_at") or act.get("status") == "locked":
        return jsonify({"error": "PIN試行回数が上限に達しました。サポートにご連絡ください"}), 400

    try:
        expires_at = act.get("expires_at")
        exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00")) if isinstance(expires_at, str) else None
        if exp_dt and exp_dt < datetime.now(timezone.utc):
            return jsonify({"error": "このアクティベーションURLは期限切れです"}), 400
    except Exception:
        pass

    # Idempotent: already used by same LINE user -> allow login
    if act.get("used_by") or act.get("used_at") or act.get("status") == "used":
        user_res = sb.table("users").select("id, line_user_id, display_name, plan").eq("id", act.get("used_by")).limit(1).execute()
        if user_res.data and user_res.data[0].get("line_user_id") == line_user_id:
            token = _create_token(user_res.data[0]["id"])
            return jsonify({
                "token": token,
                "user": {"id": user_res.data[0]["id"], "display_name": user_res.data[0].get("display_name", ""), "plan": user_res.data[0].get("plan", "")},
            })
        return jsonify({"error": "このアクティベーションは既に使用済みです"}), 400

    # Ensure LINE not already linked
    existing = sb.table("users").select("id").eq("line_user_id", line_user_id).limit(1).execute()
    if existing.data:
        return jsonify({"error": "このLINEアカウントは既に登録済みです（別アクティベーションは使用できません）"}), 400

    meta = act.get("metadata") if isinstance(act.get("metadata"), dict) else {}
    role = meta.get("role") if isinstance(meta, dict) else None
    if not isinstance(role, str) or not role:
        role = "member"

    user_ins = sb.table("users").insert({
        "line_user_id": line_user_id,
        "display_name": display_name,
        "role": role,
        "plan": "premium",
        "last_active_at": "now()",
    }).execute()

    if not user_ins.data:
        return jsonify({"error": "アカウント作成に失敗しました"}), 500

    user_id = user_ins.data[0]["id"]

    new_meta = {}
    if isinstance(meta, dict):
        new_meta.update(meta)
    new_meta["line_user_id"] = line_user_id

    sb.table("activation_links").update({
        "used_by": user_id,
        "used_at": "now()",
        "status": "used",
        "metadata": new_meta,
    }).eq("id", activation_id).execute()

    token = _create_token(user_id)
    return jsonify({
        "token": token,
        "user": {"id": user_id, "display_name": display_name, "plan": "premium", "role": role},
    })


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
    user_res = sb.table("users").select("id, display_name, plan, role").eq("id", payload["uid"]).limit(1).execute()
    if not user_res.data:
        return jsonify({"error": "user not found"}), 404

    user = user_res.data[0]
    return jsonify({
        "id": user["id"],
        "display_name": user["display_name"],
        "plan": user["plan"],
        "role": user.get("role", "member"),
    })
