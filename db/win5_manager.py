"""WIN5 data management — save/load races, scores, results, tickets via Supabase."""

import logging
from datetime import datetime, timezone

from db.supabase_client import get_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WIN5 Races
# ---------------------------------------------------------------------------

def save_win5_races(date: str, races: list[dict]) -> int:
    """Save WIN5 target races for a given date. Returns count saved."""
    sb = get_client()
    saved = 0

    for race in races:
        row = {
            "date": date,
            "race_order": race.get("race_order"),
            "race_id": race.get("race_id"),
            "venue": race.get("venue"),
            "race_number": race.get("race_number"),
            "race_name": race.get("race_name", ""),
            "distance": race.get("distance", ""),
            "field_size": race.get("field_size", 0),
            "volatility_rank": race.get("volatility_rank"),
        }
        try:
            sb.table("win5_races") \
                .upsert(row, on_conflict="date,race_order") \
                .execute()
            saved += 1
        except Exception as e:
            logger.error(f"Failed to save race {date} R{race.get('race_order')}: {e}")

    logger.info(f"Saved {saved}/{len(races)} WIN5 races for {date}")
    return saved


def get_win5_races(date: str) -> list[dict]:
    """Get saved WIN5 races for a date."""
    sb = get_client()
    res = sb.table("win5_races") \
        .select("*") \
        .eq("date", date) \
        .order("race_order") \
        .execute()
    return res.data or []


# ---------------------------------------------------------------------------
# Horse Scores
# ---------------------------------------------------------------------------

def save_horse_scores(win5_race_id: str, horses: list[dict]) -> int:
    """Save horse scores for a WIN5 race. Returns count saved."""
    sb = get_client()
    saved = 0

    for h in horses:
        row = {
            "win5_race_id": win5_race_id,
            "horse_number": h.get("horse_number"),
            "horse_name": h.get("horse_name"),
            "ai_win_prob": h.get("ai_win_prob"),
            "market_prob": h.get("market_prob"),
            "value_score": h.get("value_score"),
            "odds": h.get("odds"),
            "popularity_rank": h.get("popularity_rank"),
            "engine_ranks": h.get("engine_ranks"),
            "total_score": h.get("total_score"),
        }
        try:
            sb.table("win5_horse_scores").insert(row).execute()
            saved += 1
        except Exception as e:
            logger.warning(f"Failed to save horse score: {e}")

    return saved


def get_horse_scores(win5_race_id: str) -> list[dict]:
    """Get horse scores for a WIN5 race."""
    sb = get_client()
    res = sb.table("win5_horse_scores") \
        .select("*") \
        .eq("win5_race_id", win5_race_id) \
        .order("horse_number") \
        .execute()
    return res.data or []


# ---------------------------------------------------------------------------
# User Tickets
# ---------------------------------------------------------------------------

def save_ticket(user_id: str, date: str, ticket_data: dict) -> dict:
    """Save a user's ticket pattern."""
    sb = get_client()
    row = {
        "user_id": user_id,
        "date": date,
        "budget": ticket_data.get("investment", 0),
        "target_payout": ticket_data.get("estimated_payout_range", {}).get("max", 0),
        "risk_level": ticket_data.get("risk_level", "balanced"),
        "ticket_data": ticket_data.get("tickets", {}),
        "total_combinations": ticket_data.get("total_combinations", 0),
        "expected_value": ticket_data.get("expected_value", 0),
        "hit_probability": ticket_data.get("hit_probability", 0),
        "scenario_type": ticket_data.get("scenario_type", "custom"),
    }
    res = sb.table("win5_tickets").insert(row).execute()
    logger.info(f"Ticket saved: user={user_id[:8]}... date={date} combos={row['total_combinations']}")
    return res.data[0] if res.data else row


def get_user_tickets(user_id: str, date: str = "") -> list[dict]:
    """Get user's saved tickets, optionally filtered by date."""
    sb = get_client()
    query = sb.table("win5_tickets") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True)
    if date:
        query = query.eq("date", date)
    res = query.limit(20).execute()
    return res.data or []


# ---------------------------------------------------------------------------
# WIN5 Results
# ---------------------------------------------------------------------------

def save_win5_result(date: str, winners: list[dict], payout: int, carryover: int = 0) -> dict:
    """Save WIN5 result for a date."""
    sb = get_client()
    row = {
        "date": date,
        "winners": winners,
        "payout": payout,
        "carryover": carryover,
    }
    res = sb.table("win5_results") \
        .upsert(row, on_conflict="date") \
        .execute()
    logger.info(f"WIN5 result saved: {date} payout={payout}")
    return res.data[0] if res.data else row


def get_win5_result(date: str) -> dict | None:
    """Get WIN5 result for a date."""
    sb = get_client()
    res = sb.table("win5_results") \
        .select("*") \
        .eq("date", date) \
        .limit(1) \
        .execute()
    return res.data[0] if res.data else None


def get_recent_results(limit: int = 10) -> list[dict]:
    """Get recent WIN5 results."""
    sb = get_client()
    res = sb.table("win5_results") \
        .select("*") \
        .order("date", desc=True) \
        .limit(limit) \
        .execute()
    return res.data or []


# ---------------------------------------------------------------------------
# User History
# ---------------------------------------------------------------------------

def save_user_result(user_id: str, date: str, ticket_id: str, is_hit: bool, payout: int = 0):
    """Record a user's WIN5 result for a date."""
    sb = get_client()
    row = {
        "user_id": user_id,
        "date": date,
        "ticket_id": ticket_id,
        "is_hit": is_hit,
        "payout": payout,
    }
    sb.table("win5_user_history") \
        .upsert(row, on_conflict="user_id,date,ticket_id") \
        .execute()


def get_user_history(user_id: str, limit: int = 20) -> list[dict]:
    """Get user's WIN5 history."""
    sb = get_client()
    res = sb.table("win5_user_history") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("date", desc=True) \
        .limit(limit) \
        .execute()
    return res.data or []
