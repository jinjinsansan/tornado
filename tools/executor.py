"""Tool execution — bridges Claude tool calls to WIN5 data."""

import json
import logging
import time
from datetime import datetime, timezone, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import DLOGIC_DATA_API_URL, DLOGIC_PREDICTION_API_URL, WIN5_PRICE
from scrapers.win5 import fetch_win5_races, fetch_win5_carryover, fetch_race_entries
from tools.volatility import calculate_volatility
from tools.ticket_generator import generate_tickets, generate_scenarios

logger = logging.getLogger(__name__)

# Backend API session with retry
_session = requests.Session()
_retry = Retry(total=3, backoff_factor=2, status_forcelist=[502, 503, 504])
_session.mount("http://", HTTPAdapter(max_retries=_retry))

# In-memory cache for current week's WIN5 data
_win5_cache: dict = {}  # {"date": str, "races": [...], "fetched_at": float}
_WIN5_CACHE_TTL = 600  # 10 minutes

JST = timezone(timedelta(hours=9))


def execute_tool(tool_name: str, tool_input: dict, context: dict | None = None) -> str:
    """Execute a tool and return the result as a JSON string."""
    start = time.monotonic()
    logger.info(f"Tool call: {tool_name} with keys={list(tool_input.keys())}")
    try:
        if tool_name == "get_win5_races":
            result = _get_win5_races(tool_input)
        elif tool_name == "get_race_scores":
            result = _get_race_scores(tool_input)
        elif tool_name == "get_volatility":
            result = _get_volatility(tool_input)
        elif tool_name == "generate_tickets":
            result = _generate_tickets(tool_input)
        elif tool_name == "generate_scenarios":
            result = _generate_scenarios(tool_input)
        elif tool_name == "simulate_payout":
            result = _simulate_payout(tool_input)
        elif tool_name == "get_win5_history":
            result = _get_win5_history(tool_input)
        elif tool_name == "get_carryover":
            result = _get_carryover(tool_input)
        else:
            result = json.dumps({"error": f"Unknown tool: {tool_name}"}, ensure_ascii=False)

        elapsed = time.monotonic() - start
        logger.info(f"Tool done: {tool_name} in {elapsed:.2f}s")
        return result
    except Exception as e:
        elapsed = time.monotonic() - start
        logger.exception(f"Tool error: {tool_name} ({elapsed:.2f}s)")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Backend API helpers
# ---------------------------------------------------------------------------

def _fetch_entries(race_id: str) -> dict | None:
    """Fetch race entries from Dlogic backend data API."""
    # 1) Try Dlogic data API (preferred when available)
    try:
        url = f"{DLOGIC_DATA_API_URL}/api/data/entries/{race_id}?type=jra"
        resp = _session.get(url, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                return None
            return data
    except Exception as e:
        logger.warning(f"Entries fetch failed for {race_id}: {e}")

    # 2) Fallback: scrape race card directly
    return fetch_race_entries(race_id)


def _fetch_predictions(race_id: str, entries: dict) -> dict | None:
    """Fetch 4-engine predictions from Dlogic backend."""
    try:
        payload = {
            "race_id": race_id,
            "horses": entries.get("horses", []),
            "horse_numbers": entries.get("horse_numbers", []),
            "jockeys": entries.get("jockeys", []),
            "posts": entries.get("posts", []),
            "venue": entries.get("venue", ""),
            "race_number": entries.get("race_number", 0),
            "distance": entries.get("distance", ""),
            "track_condition": entries.get("track_condition", "良"),
            "odds": entries.get("odds", None),
        }
        resp = _session.post(
            f"{DLOGIC_PREDICTION_API_URL}/api/v2/predictions/newspaper",
            json=payload, timeout=60,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"Predictions fetch failed for {race_id}: {e}")
    return None


def _build_horse_data(entries: dict, predictions: dict | None) -> list[dict]:
    """Build horse data list with AI scores from entries + predictions."""
    horses_list = entries.get("entries", [])
    if not horses_list:
        # Fallback: build from parallel arrays
        names = entries.get("horses", [])
        numbers = entries.get("horse_numbers", [])
        horses_list = [
            {"horse_number": numbers[i] if i < len(numbers) else i + 1, "horse_name": names[i]}
            for i in range(len(names))
        ]

    # Build rank map from predictions
    rank_map: dict[int, dict] = {}  # horse_number → {engine: rank}
    if predictions:
        for engine, top5 in predictions.items():
            if engine in ("track_adjusted",):
                continue
            if isinstance(top5, list):
                for rank_idx, num in enumerate(top5[:5]):
                    if isinstance(num, int):
                        rank_map.setdefault(num, {})[engine] = rank_idx + 1

    # Calculate AI win probability from engine ranks
    result = []
    n_horses = len(horses_list)

    for h in horses_list:
        num = h.get("horse_number", 0)
        odds = h.get("odds", 0) or 0
        pop = h.get("popularity", 0) or h.get("popularity_rank", 0) or 0

        # AI score: average inverse rank across engines
        ranks = rank_map.get(num, {})
        if ranks:
            # S=1 → score 5, A=2 → 4, B=3 → 3, C=4 → 2, C=5 → 1, unranked → 0.5
            scores = [max(0, 6 - r) for r in ranks.values()]
            ai_score = sum(scores) / len(scores) / 5  # normalized 0-1
        else:
            ai_score = 0.5 / n_horses  # minimal score for unranked

        # Market probability from odds
        market_prob = (1 / odds * 0.8) if odds > 0 else (1 / n_horses)

        # Value score
        value_score = ai_score / market_prob if market_prob > 0 else 1.0

        result.append({
            "horse_number": num,
            "horse_name": h.get("horse_name", ""),
            "ai_win_prob": round(ai_score, 4),
            "market_prob": round(market_prob, 4),
            "odds": odds,
            "popularity_rank": pop,
            "value_score": round(value_score, 3),
            "engine_ranks": ranks,
        })

    return result


def _get_enriched_races() -> list[dict]:
    """Get WIN5 races enriched with entries, predictions, and volatility."""
    global _win5_cache

    # Check cache
    if (_win5_cache
        and _win5_cache.get("races")
        and time.time() - _win5_cache.get("fetched_at", 0) < _WIN5_CACHE_TTL):
        return _win5_cache["races"]

    # Fetch WIN5 target races
    raw_races = fetch_win5_races()
    if not raw_races:
        return []

    enriched = []
    for race in raw_races:
        race_id = race["race_id"]

        # Fetch entries
        entries = _fetch_entries(race_id) or fetch_race_entries(race_id)
        if not entries:
            logger.warning(f"No entries for {race_id}")
            enriched.append({**race, "horses": [], "field_size": 0, "distance": "", "volatility_rank": 3})
            continue

        # Fetch predictions
        predictions = _fetch_predictions(race_id, entries)

        # Build horse data
        horses = _build_horse_data(entries, predictions)

        # Calculate volatility
        vol = calculate_volatility(horses, len(horses))

        enriched.append({
            **race,
            "horses": horses,
            "field_size": len(horses),
            "volatility_rank": vol["volatility_rank"],
            "volatility_detail": vol,
            "distance": entries.get("distance", ""),
            "track_condition": entries.get("track_condition", ""),
        })

    _win5_cache = {"races": enriched, "fetched_at": time.time()}
    return enriched


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _get_win5_races(params: dict) -> str:
    """Get this week's WIN5 target 5 races."""
    races = _get_enriched_races()
    if not races:
        return json.dumps({"error": "WIN5対象レースが取得できなかった"}, ensure_ascii=False)

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
        })

    return json.dumps({"races": result, "count": len(result)}, ensure_ascii=False)


def _get_race_scores(params: dict) -> str:
    """Get all horse scores for WIN5 races."""
    races = _get_enriched_races()
    if not races:
        return json.dumps({"error": "WIN5データが取得できなかった"}, ensure_ascii=False)

    race_order = params.get("race_order")

    result = []
    for r in races:
        if race_order and r.get("race_order") != race_order:
            continue
        result.append({
            "race_order": r.get("race_order"),
            "venue": r.get("venue"),
            "race_number": r.get("race_number"),
            "race_name": r.get("race_name"),
            "volatility_rank": r.get("volatility_rank"),
            "horses": r.get("horses", []),
        })

    return json.dumps({"races": result}, ensure_ascii=False)


def _get_volatility(params: dict) -> str:
    """Get volatility ranks for all 5 races."""
    races = _get_enriched_races()
    if not races:
        return json.dumps({"error": "WIN5データが取得できなかった"}, ensure_ascii=False)

    result = []
    total_score = 0
    for r in races:
        vol = r.get("volatility_detail", {})
        result.append({
            "race_order": r.get("race_order"),
            "venue": r.get("venue"),
            "race_number": r.get("race_number"),
            "race_name": r.get("race_name"),
            "volatility_rank": r.get("volatility_rank", 3),
            "raw_score": vol.get("raw_score", 50),
            "description": vol.get("description", ""),
            "factors": vol.get("factors", {}),
        })
        total_score += r.get("volatility_rank", 3)

    overall = "大荒れ週" if total_score >= 20 else "荒れ模様" if total_score >= 15 else "やや混戦" if total_score >= 12 else "やや堅め" if total_score >= 8 else "堅い週"

    return json.dumps({
        "races": result,
        "overall_volatility": overall,
        "total_volatility_score": total_score,
    }, ensure_ascii=False)


def _generate_tickets(params: dict) -> str:
    """Generate optimal ticket combinations."""
    races = _get_enriched_races()
    if not races:
        return json.dumps({"error": "WIN5データが取得できなかった"}, ensure_ascii=False)

    budget = params.get("budget", 5000)
    target = params.get("target_payout", 1000000)
    risk = params.get("risk_level", "balanced")

    result = generate_tickets(races, budget, target, risk)
    return json.dumps(result, ensure_ascii=False)


def _generate_scenarios(params: dict) -> str:
    """Generate 3 scenarios (main / medium / wild)."""
    races = _get_enriched_races()
    if not races:
        return json.dumps({"error": "WIN5データが取得できなかった"}, ensure_ascii=False)

    budget = params.get("budget", 5000)
    result = generate_scenarios(races, budget)
    return json.dumps(result, ensure_ascii=False)


def _simulate_payout(params: dict) -> str:
    """Simulate payout for given ticket selections."""
    tickets = params.get("tickets", {})
    if not tickets:
        return json.dumps({"error": "買い目が指定されていない"}, ensure_ascii=False)

    races = _get_enriched_races()
    if not races:
        return json.dumps({"error": "WIN5データが取得できなかった"}, ensure_ascii=False)

    # Calculate payout estimate from specified horses
    total_combos = 1
    min_odds = 1.0
    max_odds = 1.0

    for r in races:
        key = f"R{r.get('race_order')}"
        selected_nums = tickets.get(key, [])
        if not selected_nums:
            return json.dumps({"error": f"{key}の馬番が指定されていない"}, ensure_ascii=False)

        total_combos *= len(selected_nums)
        horses = r.get("horses", [])
        selected_odds = [h["odds"] for h in horses if h["horse_number"] in selected_nums and h.get("odds", 0) > 0]

        if selected_odds:
            min_odds *= min(selected_odds)
            max_odds *= max(selected_odds)
        else:
            min_odds *= 5
            max_odds *= 20

    investment = total_combos * WIN5_PRICE

    return json.dumps({
        "total_combinations": total_combos,
        "investment": investment,
        "estimated_payout": {
            "min": int(min_odds * WIN5_PRICE * 0.7),
            "max": int(max_odds * WIN5_PRICE * 0.7),
        },
    }, ensure_ascii=False)


def _get_win5_history(params: dict) -> str:
    """Past WIN5 results from Supabase."""
    from db.win5_manager import get_recent_results
    weeks = params.get("weeks", 10)
    results = get_recent_results(limit=weeks)

    if not results:
        return json.dumps({
            "results": [],
            "message": "過去のWIN5結果データはまだ蓄積されていない。今後毎週自動で記録されていくぜ。",
            "tip": "WIN5の平均配当は約300万円。キャリーオーバー時は1000万超えも。",
        }, ensure_ascii=False)

    formatted = []
    for r in results:
        formatted.append({
            "date": r.get("date"),
            "payout": r.get("payout", 0),
            "carryover": r.get("carryover", 0),
        })

    return json.dumps({
        "results": formatted,
        "count": len(formatted),
    }, ensure_ascii=False)


def _get_carryover(params: dict) -> str:
    """Get current WIN5 carryover."""
    co = fetch_win5_carryover()
    carryover = co.get("carryover", 0)

    if carryover > 0:
        strategy = "キャリーオーバーあり。点数を広げて高配当を狙うのが有効。"
        if carryover >= 100000000:
            strategy = "1億超えのキャリーオーバー！攻めの姿勢で点数を広げるべき。"
    else:
        strategy = "キャリーオーバーなし。通常の予算配分でOK。"

    return json.dumps({
        "carryover": carryover,
        "carryover_display": f"{carryover:,}円" if carryover > 0 else "0円",
        "has_carryover": carryover > 0,
        "strategy": strategy,
    }, ensure_ascii=False)
