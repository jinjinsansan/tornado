"""User management for TornadoAI via Supabase."""

import logging
from datetime import datetime, timezone

from db.supabase_client import get_client

logger = logging.getLogger(__name__)


def get_or_create_user(line_user_id: str, display_name: str = "") -> dict:
    """Get or create a user by LINE user ID. Returns user dict."""
    sb = get_client()

    res = sb.table("users") \
        .select("*") \
        .eq("line_user_id", line_user_id) \
        .limit(1) \
        .execute()

    if res.data:
        user = res.data[0]
        # Update last active
        sb.table("users") \
            .update({
                "last_active_at": datetime.now(timezone.utc).isoformat(),
                "display_name": display_name or user.get("display_name", ""),
            }) \
            .eq("id", user["id"]) \
            .execute()
        return user

    # Create new user
    insert_res = sb.table("users").insert({
        "line_user_id": line_user_id,
        "display_name": display_name,
        "plan": "free",
    }).execute()

    logger.info(f"New user created: {line_user_id} ({display_name})")
    return insert_res.data[0] if insert_res.data else {}


def get_user_by_id(user_id: str) -> dict | None:
    """Get user by UUID."""
    sb = get_client()
    res = sb.table("users") \
        .select("*") \
        .eq("id", user_id) \
        .limit(1) \
        .execute()
    return res.data[0] if res.data else None


def get_user_plan(user_id: str) -> str:
    """Get user's current plan. Returns 'free' if not found."""
    user = get_user_by_id(user_id)
    if not user:
        return "free"

    plan = user.get("plan", "free")
    expires = user.get("plan_expires_at")

    # Check if plan has expired
    if expires and plan != "free":
        try:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if exp_dt < datetime.now(timezone.utc):
                # Plan expired — downgrade to free
                sb = get_client()
                sb.table("users").update({"plan": "free"}).eq("id", user_id).execute()
                return "free"
        except Exception:
            pass

    return plan
