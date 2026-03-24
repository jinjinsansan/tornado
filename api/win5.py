"""WIN5 REST API — dashboard data endpoints."""

import json
import logging
from datetime import datetime, timedelta, timezone
import itertools

from flask import Blueprint, request, jsonify

from api.invite import verify_auth
from db.win5_manager import save_ticket, get_user_tickets, get_week_races_with_scores, get_week_result
from tools.executor import _get_enriched_races
from tools.ticket_generator import generate_tickets, generate_scenarios
from scrapers.win5 import fetch_win5_carryover
from db.win5_manager import get_recent_results
from db.supabase_client import get_client

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
    refresh = request.args.get("refresh", "").strip() in ("1", "true", "yes")
    races = _get_enriched_races(refresh=refresh)
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
    races = _get_enriched_races(refresh=bool(body.get("refresh")))
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
    races = _get_enriched_races(refresh=bool(body.get("refresh")))
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


@bp.route("/api/win5/simulate", methods=["POST"])
def simulate():
    """Simulate payout range + 'favorite misses' effect for current selections."""
    body = request.get_json(silent=True) or {}
    tickets = body.get("tickets") or {}
    if not isinstance(tickets, dict) or not tickets:
        return jsonify({"error": "tickets required"}), 400

    races = _get_enriched_races(refresh=bool(body.get("refresh")))
    if not races:
        return jsonify({"error": "WIN5データ取得失敗"}), 500

    results = []
    base_min = 1.0
    base_max = 1.0
    miss_min = 1.0
    miss_max = 1.0

    for r in races:
        key = f"R{r.get('race_order')}"
        selected_nums = tickets.get(key, [])
        horses = r.get("horses", [])

        selected = [h for h in horses if h.get("horse_number") in selected_nums]
        odds = [h.get("odds", 0) for h in selected if h.get("odds", 0) > 0]
        if odds:
            race_base_min = min(odds)
            race_base_max = max(odds)
        else:
            race_base_min = 5.0
            race_base_max = 20.0

        # Favorite: popularity_rank == 1 else minimum odds
        fav = next((h for h in horses if h.get("popularity_rank") == 1), None)
        if not fav:
            odds_all = [h.get("odds", 0) for h in horses if h.get("odds", 0) > 0]
            if odds_all:
                min_odds = min(odds_all)
                fav = next((h for h in horses if h.get("odds", 0) == min_odds), None)

        fav_num = fav.get("horse_number") if fav else None

        # If favorite "misses": winner is not favorite
        selected_wo_fav = [h for h in selected if fav_num is None or h.get("horse_number") != fav_num]
        odds_wo = [h.get("odds", 0) for h in selected_wo_fav if h.get("odds", 0) > 0]
        if odds_wo:
            race_miss_min = min(odds_wo)
            race_miss_max = max(odds_wo)
        else:
            race_miss_min = race_base_min
            race_miss_max = race_base_max

        base_min *= race_base_min
        base_max *= race_base_max
        miss_min *= race_miss_min
        miss_max *= race_miss_max

        results.append({
            "race_order": r.get("race_order"),
            "race_name": r.get("race_name", ""),
            "favorite_horse_number": fav_num,
            "base_odds_range": {"min": race_base_min, "max": race_base_max},
            "favorite_miss_odds_range": {"min": race_miss_min, "max": race_miss_max},
        })

    WIN5_PRICE = 100
    base = {"min": int(base_min * WIN5_PRICE * 0.7), "max": int(base_max * WIN5_PRICE * 0.7)}
    miss = {"min": int(miss_min * WIN5_PRICE * 0.7), "max": int(miss_max * WIN5_PRICE * 0.7)}

    return jsonify({
        "base_estimated_payout": base,
        "favorite_miss_estimated_payout": miss,
        "per_race": results,
    })


@bp.route("/api/win5/carryover", methods=["GET"])
def get_carryover():
    """Get current carryover info."""
    return jsonify(fetch_win5_carryover())


@bp.route("/api/win5/results/recent", methods=["GET"])
def recent_results():
    """Get recent WIN5 results (for simple history/simulator baseline)."""
    try:
        limit = int(request.args.get("limit", "10"))
    except Exception:
        limit = 10
    limit = max(1, min(limit, 52))
    rows = get_recent_results(limit=limit)
    return jsonify({"results": rows, "count": len(rows)})


@bp.route("/api/win5/backtest", methods=["GET"])
def backtest():
    """Backtest current generator against past weeks (requires Supabase data)."""
    try:
        weeks = int(request.args.get("weeks", "52"))
    except Exception:
        weeks = 52
    weeks = max(1, min(weeks, 52))

    try:
        budget = int(request.args.get("budget", "5000"))
    except Exception:
        budget = 5000
    try:
        target_payout = int(request.args.get("target_payout", "5000000"))
    except Exception:
        target_payout = 5000000
    risk_level = request.args.get("risk_level", "balanced")

    # Dates are derived from win5_results rows (latest first)
    results = get_recent_results(limit=weeks)
    if not results:
        return jsonify({"results": [], "count": 0, "message": "過去結果データがありません"}), 200

    rows = []
    total_invest = 0
    total_return = 0
    hit_count = 0

    for res in results:
        date = res.get("date")
        if not date:
            continue
        week_races = get_week_races_with_scores(date)
        week_result = get_week_result(date) or {}
        winners = week_result.get("winners") or []

        if len(week_races) != 5 or not winners:
            continue

        ticket = generate_tickets(
            week_races,
            budget=budget,
            target_payout=target_payout,
            risk_level=risk_level,
        )

        # Determine hit
        win_map = {w.get("race_order"): w.get("horse_number") for w in winners if isinstance(w, dict)}
        is_hit = True
        for r in week_races:
            ro = r.get("race_order")
            wn = win_map.get(ro)
            sel = ticket.get("tickets", {}).get(f"R{ro}", [])
            if wn is None or wn not in sel:
                is_hit = False
                break

        invest = int(ticket.get("investment", 0) or 0)
        ret = int(res.get("payout", 0) or 0) if is_hit else 0

        total_invest += invest
        total_return += ret
        hit_count += 1 if is_hit else 0

        rows.append({
            "date": date,
            "investment": invest,
            "return": ret,
            "profit": ret - invest,
            "is_hit": is_hit,
            "payout": int(res.get("payout", 0) or 0),
            "carryover": int(res.get("carryover", 0) or 0),
            "tickets": ticket.get("tickets", {}),
            "total_combinations": ticket.get("total_combinations", 0),
        })

    return jsonify({
        "results": rows,
        "count": len(rows),
        "summary": {
            "weeks_considered": len(rows),
            "hits": hit_count,
            "hit_rate": (hit_count / len(rows)) if rows else 0,
            "total_investment": total_invest,
            "total_return": total_return,
            "total_profit": total_return - total_invest,
        }
    })


@bp.route("/api/win5/overlap", methods=["POST"])
def overlap():
    """Aggregate overlap ratio for the caller's selected horses (crowdedness).

    Returns overlap only for horses included in request tickets to avoid leaking full distributions.
    """
    payload = verify_auth()
    if not payload:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    tickets = body.get("tickets") or {}
    date = body.get("date") or _next_sunday_yyyymmdd()

    if not isinstance(tickets, dict) or not tickets:
        return jsonify({"error": "tickets required"}), 400

    # Selected horses to measure
    selected: dict[str, set[int]] = {}
    for k, v in tickets.items():
        if not isinstance(k, str) or not k.startswith("R"):
            continue
        if isinstance(v, list):
            nums = {int(n) for n in v if isinstance(n, int) or (isinstance(n, str) and str(n).isdigit())}
            if nums:
                selected[k] = nums

    if not selected:
        return jsonify({"error": "no selections"}), 400

    sb = get_client()
    res = sb.table("win5_tickets").select("ticket_data").eq("date", date).limit(1000).execute()
    rows = res.data or []
    total = len(rows)
    if total == 0:
        return jsonify({"date": date, "total_tickets": 0, "overlap": {}})

    counts: dict[str, dict[int, int]] = {rk: {n: 0 for n in nums} for rk, nums in selected.items()}
    for row in rows:
        td = row.get("ticket_data") if isinstance(row, dict) else None
        if not isinstance(td, dict):
            continue
        for rk, nums in selected.items():
            picked = td.get(rk, [])
            if not isinstance(picked, list):
                continue
            picked_set = set()
            for p in picked:
                if isinstance(p, int):
                    picked_set.add(p)
                elif isinstance(p, str) and p.isdigit():
                    picked_set.add(int(p))
            for n in nums:
                if n in picked_set:
                    counts[rk][n] += 1

    # Format with ratios
    overlap_out = {}
    for rk, m in counts.items():
        overlap_out[rk] = [
            {"horse_number": n, "count": c, "ratio": (c / total) if total else 0}
            for n, c in sorted(m.items(), key=lambda x: x[1], reverse=True)
        ]

    return jsonify({"date": date, "total_tickets": total, "overlap": overlap_out})


@bp.route("/api/win5/profile", methods=["GET"])
def profile():
    """Simple personalization summary from user's saved tickets."""
    payload = verify_auth()
    if not payload:
        return jsonify({"error": "unauthorized"}), 401

    user_id = payload["uid"]
    items = get_user_tickets(user_id=user_id, date="")
    if not items:
        return jsonify({"count": 0, "message": "まだ保存データがありません"})

    combos = [int(i.get("total_combinations", 0) or 0) for i in items]
    invest = [int(i.get("budget", 0) or 0) for i in items]
    avg_combos = sum(combos) / len(combos) if combos else 0
    avg_invest = sum(invest) / len(invest) if invest else 0

    # Average selections per race
    per_race_counts = {f"R{i}": [] for i in range(1, 6)}
    for it in items:
        td = it.get("ticket_data") or {}
        if not isinstance(td, dict):
            continue
        for rk in per_race_counts.keys():
            v = td.get(rk, [])
            per_race_counts[rk].append(len(v) if isinstance(v, list) else 0)

    avg_per_race = {rk: (sum(v) / len(v) if v else 0) for rk, v in per_race_counts.items()}

    # Heuristic style
    if avg_combos >= 200:
        style = "広げる派（高配当重視）"
        tip = "点数が膨らみやすいので、堅いレースは1頭に絞ると投資効率が上がります。"
    elif avg_combos >= 60:
        style = "バランス派"
        tip = "荒れるレースだけ狙って広げ、堅いレースは削るとさらに期待値が安定します。"
    else:
        style = "絞る派（的中率重視）"
        tip = "荒れレースを1つだけ広げる“1点集中”を作ると爆発ルートが出やすいです。"

    return jsonify({
        "count": len(items),
        "avg_total_combinations": avg_combos,
        "avg_investment": avg_invest,
        "avg_selected_per_race": avg_per_race,
        "style": style,
        "tip": tip,
    })


@bp.route("/api/win5/explosion/heatmap", methods=["POST"])
def explosion_heatmap():
    """Compute explosion (high-payout) potential heatmap for selected horses.

    Security: returns only for the horses included in request tickets.
    """
    payload = verify_auth()
    if not payload:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    tickets = body.get("tickets") or {}
    if not isinstance(tickets, dict) or not tickets:
        return jsonify({"error": "tickets required"}), 400

    max_per_race = body.get("max_per_race", 6)
    try:
        max_per_race = int(max_per_race)
    except Exception:
        max_per_race = 6
    max_per_race = max(1, min(max_per_race, 10))

    races = _get_enriched_races(refresh=bool(body.get("refresh")))
    if not races:
        return jsonify({"error": "WIN5データ取得失敗"}), 500

    # Build candidates per race (only selected horses)
    candidates = []
    for r in races:
        ro = r.get("race_order")
        key = f"R{ro}"
        selected_nums = tickets.get(key, [])
        if not isinstance(selected_nums, list) or len(selected_nums) == 0:
            return jsonify({"error": f"{key} empty"}), 400

        horses = r.get("horses", [])
        selected = [h for h in horses if h.get("horse_number") in selected_nums]
        # Sort for explosion potential by odds desc (fallback odds = 20), then value_score desc
        selected.sort(
            key=lambda h: ((h.get("odds", 0) or 0) if (h.get("odds", 0) or 0) > 0 else 20.0, (h.get("value_score", 0) or 0)),
            reverse=True,
        )
        selected = selected[:max_per_race]
        candidates.append({
            "race_order": ro,
            "race_name": r.get("race_name", ""),
            "horses": selected,
        })

    if len(candidates) != 5:
        return jsonify({"error": "Expected 5 races"}), 400

    # Enumerate combos (bounded by max_per_race)
    horse_lists = [c["horses"] for c in candidates]
    total_combos = 1
    for lst in horse_lists:
        total_combos *= len(lst)
    if total_combos > 20000:
        return jsonify({"error": "too many combinations"}), 400

    WIN5_PRICE = 100
    fallback_odds = 20.0

    max_for_horse: dict[tuple[int, int], int] = {}  # (race_order, horse_number) -> max payout
    count_for_horse: dict[tuple[int, int], int] = {}
    global_max = 0

    for combo in itertools.product(*horse_lists):
        odds_prod = 1.0
        for h in combo:
            o = float(h.get("odds", 0) or 0)
            odds_prod *= o if o > 0 else fallback_odds
        payout = int(odds_prod * WIN5_PRICE * 0.7)
        if payout > global_max:
            global_max = payout
        for h in combo:
            key = (int(h.get("race_order", 0) or 0), int(h.get("horse_number", 0) or 0))
            prev = max_for_horse.get(key, 0)
            if payout > prev:
                max_for_horse[key] = payout
            count_for_horse[key] = count_for_horse.get(key, 0) + 1

    # Build response
    out_races = []
    for c in candidates:
        ro = int(c["race_order"])
        items = []
        for h in c["horses"]:
            hn = int(h.get("horse_number", 0) or 0)
            k = (ro, hn)
            max_payout = max_for_horse.get(k, 0)
            items.append({
                "horse_number": hn,
                "horse_name": h.get("horse_name", ""),
                "odds": float(h.get("odds", 0) or 0),
                "value_score": float(h.get("value_score", 0) or 0),
                "max_route_payout": max_payout,
                "max_route_ratio": (max_payout / global_max) if global_max else 0,
                "routes_count": count_for_horse.get(k, 0),
            })
        items.sort(key=lambda x: x["max_route_payout"], reverse=True)
        out_races.append({
            "race_order": ro,
            "race_name": c.get("race_name", ""),
            "items": items,
        })

    out_races.sort(key=lambda x: x["race_order"])
    return jsonify({
        "max_per_race": max_per_race,
        "total_combinations": total_combos,
        "global_max_route_payout": global_max,
        "races": out_races,
    })
