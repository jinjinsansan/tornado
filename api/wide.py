import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from api.invite import verify_auth
from scrapers.race_list import fetch_race_list, pick_default_race_date
from scrapers.wide_odds import fetch_wide_odds_pairs
from tools.executor import _fetch_entries, _fetch_predictions, _build_horse_data

logger = logging.getLogger(__name__)

bp = Blueprint("wide", __name__)

JST = timezone(timedelta(hours=9))
WIN5_PRICE = 100


def _today_yyyymmdd() -> str:
    now = datetime.now(JST)
    return now.strftime("%Y%m%d")


def _place_prob_from_win(ai_win_prob: float) -> float:
    # Crude heuristic: place probability roughly scales with win prob, capped.
    try:
        p = float(ai_win_prob or 0)
    except Exception:
        p = 0.0
    return max(0.01, min(0.9, p * 3.0))


@bp.route("/api/wide/races", methods=["GET"])
def list_races():
    payload = verify_auth()
    if not payload:
        return jsonify({"error": "unauthorized"}), 401

    # UI is "auto" (no calendar). Keep date param as an override for internal debugging.
    date = (request.args.get("date") or "").strip()
    if not date:
        date = pick_default_race_date(datetime.now(JST))

    # Prefetch is assumed available from the previous day 10:30 JST.
    try:
        d = datetime.strptime(date, "%Y%m%d").replace(tzinfo=JST)
        ready_at = (d - timedelta(days=1)).replace(hour=10, minute=30, second=0, microsecond=0)
        ready = datetime.now(JST) >= ready_at
    except Exception:
        ready_at = None
        ready = True

    races = fetch_race_list(date)
    return jsonify({
        "date": date,
        "ready": bool(ready),
        "ready_at": ready_at.isoformat() if ready_at else None,
        "races": races,
        "count": len(races),
    })


@bp.route("/api/wide/generate", methods=["POST"])
def generate_wide():
    payload = verify_auth()
    if not payload:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    race_id = str(body.get("race_id") or "").strip()
    budget = int(body.get("budget") or 0)
    target_payout = int(body.get("target_payout") or 0)

    if not race_id.isdigit():
        return jsonify({"error": "race_id required"}), 400
    if budget < WIN5_PRICE:
        return jsonify({"error": "budget must be >= 100"}), 400
    if target_payout <= 0:
        return jsonify({"error": "target_payout required"}), 400

    entries = _fetch_entries(race_id)
    if not entries:
        return jsonify({"error": "レース情報の取得に失敗しました"}), 500

    preds = _fetch_predictions(race_id, entries)
    horses = _build_horse_data(entries, preds)
    horses = [h for h in horses if h.get("horse_number") and h.get("horse_name")]
    if len(horses) < 6:
        return jsonify({"error": "出走馬データが不足しています"}), 500

    horse_by_no = {int(h["horse_number"]): h for h in horses if str(h.get("horse_number")).isdigit()}

    wide_pairs = fetch_wide_odds_pairs(race_id)
    if not wide_pairs:
        return jsonify({"error": "ワイドオッズがまだ取得できません（公開前の可能性があります）"}), 400

    target_mult = target_payout / float(budget)  # e.g. 5.0

    scored = []
    for (a, b), odds in wide_pairs.items():
        ha = horse_by_no.get(a)
        hb = horse_by_no.get(b)
        if not ha or not hb:
            continue

        lo = float(odds.get("min") or 0)
        hi = float(odds.get("max") or lo)
        if lo <= 0:
            continue
        if hi <= 0:
            hi = lo
        mult_mid = ((lo + hi) / 2.0)  # wide odds multiplier (per 100)

        # Hit probability heuristic from AI win probs
        pa = _place_prob_from_win(ha.get("ai_win_prob", 0))
        pb = _place_prob_from_win(hb.get("ai_win_prob", 0))
        hit_p = max(0.0001, min(0.95, pa * pb * 0.9))

        closeness = 1.0 / (1.0 + abs(mult_mid - target_mult))
        score = hit_p * closeness

        payout_min = int(round(lo * (budget / 100)))
        payout_max = int(round(hi * (budget / 100)))

        scored.append({
            "pair": [
                {"horse_number": a, "horse_name": ha.get("horse_name", "")},
                {"horse_number": b, "horse_name": hb.get("horse_name", "")},
            ],
            "wide_odds": {"min": round(lo, 1), "max": round(hi, 1)},  # x per 100yen
            "hit_probability_est": round(hit_p, 4),
            "target_multiplier": round(target_mult, 3),
            "multiplier_mid": round(mult_mid, 3),
            "expected_payout_range": {"min": payout_min, "max": payout_max},
            "score": round(score, 6),
        })

    scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    top = scored[0] if scored else None
    if not top:
        return jsonify({"error": "ワイド候補の作成に失敗しました"}), 500

    return jsonify({
        "race_id": race_id,
        "budget": budget,
        "target_payout": target_payout,
        "target_multiplier": round(target_mult, 3),
        "recommended": top,
        "alternatives": scored[1:11],
        "count": len(scored),
        "note": "オッズ公開前は生成できません。公開後に再実行してください。",
    })
