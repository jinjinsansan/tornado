"""WIN5 REST API — dashboard data endpoints."""

import json
import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request, jsonify

from api.invite import verify_auth
from db.win5_manager import save_ticket, get_user_tickets
from tools.executor import _get_enriched_races
from tools.ticket_generator import generate_tickets, generate_scenarios
from scrapers.win5 import fetch_win5_carryover

logger = logging.getLogger(__name__)

bp = Blueprint("win5", __name__)

JST = timezone(timedelta(hours=9))


def _next_sunday_yyyymmdd() -> str:
    now = datetime.now(JST)
    days_until_sunday = (6 - now.weekday()) % 7
    if days_until_sunday == 0 and now.hour >= 18:
        days_until_sunday = 7
    d = now + timedelta(days=days_until_sunday)
    return d.strftime("%Y%m%d")


@bp.route("/api/win5/races", methods=["GET"])
def get_races():
    """Get WIN5 target 5 races with volatility."""
    races = _get_enriched_races()
    result = []
    for r in races:
        result.append({
            "race_order": r.get("race_order"),
            "venue": r.get("venue"),
            "race_number": r.get("race_number"),
            "race_name": r.get("race_name"),
            "field_size": r.get("field_size", len(r.get("horses", []))),
            "distance": r.get("distance", ""),
            "volatility_rank": r.get("volatility_rank", 3),
            "volatility_desc": r.get("volatility_detail", {}).get("description", ""),
            "horses": [
                {
                    "horse_number": h["horse_number"],
                    "horse_name": h["horse_name"],
                    "odds": h.get("odds", 0),
                    "ai_win_prob": h.get("ai_win_prob", 0),
                    "value_score": h.get("value_score", 1),
                    "popularity_rank": h.get("popularity_rank", 0),
                }
                for h in r.get("horses", [])
            ],
        })
    return jsonify({"races": result, "count": len(result)})


@bp.route("/api/win5/tickets", methods=["POST"])
def gen_tickets():
    """Generate optimal ticket combinations."""
    body = request.get_json(silent=True) or {}
    races = _get_enriched_races()
    if not races:
        return jsonify({"error": "WIN5データ取得失敗"}), 500

    result = generate_tickets(
        races,
        budget=body.get("budget", 5000),
        target_payout=body.get("target_payout", 1000000),
        risk_level=body.get("risk_level", "balanced"),
    )
    return jsonify(result)


@bp.route("/api/win5/scenarios", methods=["POST"])
def gen_scenarios():
    """Generate 3 scenarios."""
    body = request.get_json(silent=True) or {}
    races = _get_enriched_races()
    if not races:
        return jsonify({"error": "WIN5データ取得失敗"}), 500

    result = generate_scenarios(races, budget=body.get("budget", 5000))
    return jsonify(result)


@bp.route("/api/win5/tickets/save", methods=["POST"])
def save_my_ticket():
    payload = verify_auth()
    if not payload:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    ticket = body.get("ticket") or {}
    date = body.get("date") or _next_sunday_yyyymmdd()

    if not isinstance(ticket, dict) or not ticket.get("tickets"):
        return jsonify({"error": "ticket required"}), 400

    user_id = payload["uid"]
    saved = save_ticket(user_id=user_id, date=date, ticket_data=ticket)
    return jsonify(saved)


@bp.route("/api/win5/tickets/my", methods=["GET"])
def list_my_tickets():
    payload = verify_auth()
    if not payload:
        return jsonify({"error": "unauthorized"}), 401

    date = request.args.get("date", "").strip()
    user_id = payload["uid"]
    items = get_user_tickets(user_id=user_id, date=date)
    return jsonify({"tickets": items, "count": len(items)})


@bp.route("/api/win5/carryover", methods=["GET"])
def get_carryover():
    """Get current carryover info."""
    return jsonify(fetch_win5_carryover())
