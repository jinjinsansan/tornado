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
from scrapers.race_list import fetch_race_list, pick_default_race_date
from scrapers.wide_odds import fetch_wide_odds_pairs
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

def _next_sunday_yyyymmdd() -> str:
    now = datetime.now(JST)
    days_until_sunday = (6 - now.weekday()) % 7
    if days_until_sunday == 0 and now.hour >= 18:
        days_until_sunday = 7
    d = now + timedelta(days=days_until_sunday)
    return d.strftime("%Y%m%d")


def _get_cached_races_from_supabase(date: str) -> list[dict]:
    try:
        from db.win5_manager import get_win5_races as db_get_win5_races, get_horse_scores
    except Exception:
        return []

    try:
        races = db_get_win5_races(date)
    except Exception:
        return []

    if len(races) != 5:
        return []

    enriched = []
    for r in races:
        horses = []
        try:
            horses = get_horse_scores(r["id"])
        except Exception:
            horses = []

        if not horses:
            return []

        vol = calculate_volatility(horses, r.get("field_size", len(horses)) or len(horses)) if horses else {
            "volatility_rank": r.get("volatility_rank", 3),
            "description": "",
            "raw_score": 50,
            "factors": {},
        }

        enriched.append({
            "race_order": r.get("race_order"),
            "race_id": r.get("race_id"),
            "venue": r.get("venue"),
            "race_number": r.get("race_number"),
            "race_name": r.get("race_name", ""),
            "distance": r.get("distance", ""),
            "field_size": r.get("field_size", len(horses)) or len(horses),
            "horses": horses,
            "volatility_rank": r.get("volatility_rank") or vol.get("volatility_rank", 3),
            "volatility_detail": vol,
        })

    enriched.sort(key=lambda x: x.get("race_order", 0))
    return enriched


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
        elif tool_name == "get_wide_races":
            result = _get_wide_races(tool_input)
        elif tool_name == "generate_wide":
            result = _generate_wide(tool_input)
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


def _fetch_entries_dlogic_only(race_id: str) -> dict | None:
    """Use prefetched entries only (no scraping fallback)."""
    try:
        url = f"{DLOGIC_DATA_API_URL}/api/data/entries/{race_id}?type=jra"
        resp = _session.get(url, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            return None
        return data
    except Exception:
        return None


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
        waku = h.get("waku", 0) or h.get("post", 0) or 0

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
            "waku": int(waku) if isinstance(waku, (int, float, str)) and str(waku).isdigit() else 0,
            "ai_win_prob": round(ai_score, 4),
            "market_prob": round(market_prob, 4),
            "odds": odds,
            "popularity_rank": pop,
            "value_score": round(value_score, 3),
            "engine_ranks": ranks,
        })

    return result


def _get_enriched_races(refresh: bool = False) -> list[dict]:
    """Get WIN5 races enriched with entries, predictions, and volatility."""
    global _win5_cache

    # Check cache
    if (not refresh and _win5_cache
        and _win5_cache.get("races")
        and time.time() - _win5_cache.get("fetched_at", 0) < _WIN5_CACHE_TTL):
        return _win5_cache["races"]

    # Prefer Supabase cache (plan-aligned)
    if not refresh:
        cached = _get_cached_races_from_supabase(_next_sunday_yyyymmdd())
        if cached:
            _win5_cache = {"races": cached, "fetched_at": time.time()}
            return cached

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


# ---------------------------------------------------------------------------
# Wide mode tools (single-race)
# ---------------------------------------------------------------------------

def _get_wide_races(params: dict) -> str:
    date = pick_default_race_date(datetime.now(JST))
    races = fetch_race_list(date)

    ready = False
    for r in races[:8]:
        rid = str(r.get("race_id") or "")
        if not rid.isdigit():
            continue
        if _fetch_entries_dlogic_only(rid):
            ready = True
            break

    out = []
    for r in races:
        out.append({
            "venue": r.get("venue", ""),
            "race_number": r.get("race_number", 0),
            "race_name": r.get("race_name", ""),
            "start_time": r.get("start_time", ""),
            "distance": r.get("distance", ""),
        })

    return json.dumps({"date": date, "ready": ready, "races": out, "count": len(out)}, ensure_ascii=False)


def _place_prob_from_win(ai_win_prob: float) -> float:
    try:
        p = float(ai_win_prob or 0)
    except Exception:
        p = 0.0
    return max(0.01, min(0.9, p * 3.0))


def _generate_wide(params: dict) -> str:
    venue = str(params.get("venue") or "").strip()
    race_number = int(params.get("race_number") or 0)
    budget = int(params.get("budget") or 0)
    target_payout = int(params.get("target_payout") or 0)

    if not venue or race_number <= 0:
        return json.dumps({"error": "会場とレース番号が必要です（例: 中山11R）"}, ensure_ascii=False)
    if budget < 100:
        return json.dumps({"error": "予算は100円以上で指定してください"}, ensure_ascii=False)
    if target_payout <= 0:
        return json.dumps({"error": "欲しい払戻額（円）を指定してください"}, ensure_ascii=False)

    date = pick_default_race_date(datetime.now(JST))
    races = fetch_race_list(date)
    target = next((r for r in races if r.get("venue") == venue and int(r.get("race_number") or 0) == race_number), None)
    if not target:
        return json.dumps({"error": f"{venue}{race_number}R が見つかりませんでした。『ワイド レース一覧』で確認してください。"}, ensure_ascii=False)

    race_id = str(target.get("race_id") or "")
    entries = _fetch_entries_dlogic_only(race_id)
    if not entries:
        return json.dumps({"error": "データ準備中です（前日10:30以降に利用可能）"}, ensure_ascii=False)

    preds = _fetch_predictions(race_id, entries)
    horses = _build_horse_data(entries, preds)
    horses = [h for h in horses if h.get("horse_number") and h.get("horse_name")]
    if len(horses) < 6:
        return json.dumps({"error": "出走馬データが不足しています"}, ensure_ascii=False)

    horse_by_no = {int(h["horse_number"]): h for h in horses if str(h.get("horse_number")).isdigit()}
    wide_pairs = fetch_wide_odds_pairs(race_id)
    if not wide_pairs:
        return json.dumps({"error": "ワイドオッズがまだ取得できません（公開前の可能性があります）"}, ensure_ascii=False)

    target_mult = target_payout / float(budget)
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
        mult_mid = ((lo + hi) / 2.0)

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
            "wide_odds": {"min": round(lo, 1), "max": round(hi, 1)},
            "hit_probability_est": round(hit_p, 4),
            "target_multiplier": round(target_mult, 3),
            "multiplier_mid": round(mult_mid, 3),
            "expected_payout_range": {"min": payout_min, "max": payout_max},
            "score": round(score, 6),
        })

    scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    if not scored:
        return json.dumps({"error": "ワイド候補を作れませんでした"}, ensure_ascii=False)

    return json.dumps({
        "date": date,
        "venue": venue,
        "race_number": race_number,
        "race_name": target.get("race_name", ""),
        "budget": budget,
        "target_payout": target_payout,
        "target_multiplier": round(target_mult, 3),
        "recommended": scored[0],
        "alternatives": scored[1:11],
        "count": len(scored),
    }, ensure_ascii=False)
