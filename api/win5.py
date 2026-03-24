"""WIN5 REST API — dashboard data endpoints."""

import json
import logging

from flask import Blueprint, request, jsonify

from tools.executor import _get_enriched_races
from tools.ticket_generator import generate_tickets, generate_scenarios
from scrapers.win5 import fetch_win5_carryover

logger = logging.getLogger(__name__)

bp = Blueprint("win5", __name__)


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


@bp.route("/api/win5/carryover", methods=["GET"])
def get_carryover():
    """Get current carryover info."""
    return jsonify(fetch_win5_carryover())
