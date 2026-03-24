"""WIN5 ticket generator — the core of TornadoAI.

Generates optimal ticket combinations based on:
  - Budget (total yen to spend, each ticket = 100 yen)
  - Target payout (desired return)
  - Risk level (conservative / balanced / aggressive)
  - AI scores + market odds + volatility per race
"""

import itertools
import logging
import math

logger = logging.getLogger(__name__)

WIN5_PRICE = 100  # 1 ticket = 100 yen


def generate_tickets(
    races: list[dict],
    budget: int = 5000,
    target_payout: int = 1000000,
    risk_level: str = "balanced",
) -> dict:
    """Generate optimal WIN5 ticket combinations.

    Args:
        races: 5 race dicts, each containing:
            - race_order: int (1-5)
            - venue: str
            - race_number: int
            - race_name: str
            - volatility_rank: int (1-5)
            - horses: list[dict] with:
                - horse_number: int
                - horse_name: str
                - ai_win_prob: float
                - market_prob: float
                - odds: float
                - value_score: float (ai_win_prob / market_prob)
        budget: Total budget in yen
        target_payout: Desired payout in yen
        risk_level: "conservative" / "balanced" / "aggressive"

    Returns:
        {
            "tickets": {"R1": [1, 3], "R2": [5], ...},
            "total_combinations": int,
            "investment": int,
            "estimated_payout_range": {"min": int, "max": int},
            "hit_probability": float,
            "expected_value": float,
            "selections": [
                {
                    "race_order": 1,
                    "venue": "中山",
                    "race_number": 10,
                    "race_name": "...",
                    "volatility_rank": 3,
                    "selected_horses": [
                        {"horse_number": 1, "horse_name": "...", "odds": 3.5, "ai_win_prob": 0.25},
                        ...
                    ],
                    "num_selected": 2,
                },
                ...
            ],
        }
    """
    if len(races) != 5:
        return {"error": f"Expected 5 races, got {len(races)}"}

    max_tickets = budget // WIN5_PRICE

    # Determine how many horses to pick per race based on volatility + risk
    pick_counts = _allocate_picks(races, max_tickets, risk_level)

    # Select best horses for each race
    selections = []
    for i, race in enumerate(races):
        horses = race.get("horses", [])
        n_picks = pick_counts[i]
        selected = _select_horses(horses, n_picks, risk_level)

        selections.append({
            "race_order": race.get("race_order", i + 1),
            "venue": race.get("venue", ""),
            "race_number": race.get("race_number", 0),
            "race_name": race.get("race_name", ""),
            "volatility_rank": race.get("volatility_rank", 3),
            "selected_horses": selected,
            "num_selected": len(selected),
        })

    # Build ticket map
    tickets = {}
    for sel in selections:
        key = f"R{sel['race_order']}"
        tickets[key] = [h["horse_number"] for h in sel["selected_horses"]]

    # Calculate combinations
    total_combos = 1
    for sel in selections:
        total_combos *= sel["num_selected"]

    investment = total_combos * WIN5_PRICE

    # Estimate payout range
    payout_range = _estimate_payout_range(selections)

    # Hit probability = product of probabilities of selecting at least the winner
    hit_prob = _estimate_hit_probability(selections)

    # Expected value
    ev = (hit_prob * (payout_range["min"] + payout_range["max"]) / 2) / investment if investment > 0 else 0

    return {
        "tickets": tickets,
        "total_combinations": total_combos,
        "investment": investment,
        "estimated_payout_range": payout_range,
        "hit_probability": round(hit_prob, 6),
        "expected_value": round(ev, 2),
        "selections": selections,
    }


def generate_scenarios(
    races: list[dict],
    budget: int = 5000,
) -> dict:
    """Generate 3 scenarios: main (conservative), medium, wild (aggressive).

    Returns:
        {
            "main": {ticket data},
            "medium": {ticket data},
            "wild": {ticket data},
        }
    """
    return {
        "main": generate_tickets(races, budget, target_payout=500000, risk_level="conservative"),
        "medium": generate_tickets(races, budget, target_payout=2000000, risk_level="balanced"),
        "wild": generate_tickets(races, budget, target_payout=10000000, risk_level="aggressive"),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _allocate_picks(races: list[dict], max_tickets: int, risk_level: str) -> list[int]:
    """Decide how many horses to pick per race.

    Volatile races get more picks, stable races get fewer.
    Total product of picks must not exceed max_tickets.
    """
    # Base picks per volatility rank
    base_map = {
        "conservative": {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
        "balanced":     {1: 1, 2: 2, 3: 2, 4: 3, 5: 4},
        "aggressive":   {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
    }
    base = base_map.get(risk_level, base_map["balanced"])

    picks = []
    for race in races:
        v = race.get("volatility_rank", 3)
        field_size = len(race.get("horses", []))
        p = min(base.get(v, 2), max(1, field_size))
        picks.append(p)

    # Reduce if total combinations exceed budget
    while _product(picks) > max_tickets and max(picks) > 1:
        # Find race with most picks and reduce by 1
        max_idx = picks.index(max(picks))
        picks[max_idx] -= 1

    # If still too many, force minimum
    while _product(picks) > max_tickets:
        for i in range(len(picks)):
            if picks[i] > 1:
                picks[i] = 1
                break

    return picks


def _select_horses(horses: list[dict], n: int, risk_level: str) -> list[dict]:
    """Select the best N horses from a race.

    Conservative: prioritize high AI probability
    Balanced: mix AI probability and value score
    Aggressive: prioritize value score (undervalued by market)
    """
    if not horses or n <= 0:
        return []

    # Score each horse
    scored = []
    for h in horses:
        ai_prob = h.get("ai_win_prob", 0)
        value = h.get("value_score", 1.0)

        if risk_level == "conservative":
            score = ai_prob * 0.8 + min(value, 3) * 0.05
        elif risk_level == "aggressive":
            score = ai_prob * 0.3 + min(value, 5) * 0.2
        else:  # balanced
            score = ai_prob * 0.5 + min(value, 4) * 0.15

        scored.append((score, h))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [h for _, h in scored[:n]]


def _estimate_payout_range(selections: list[dict]) -> dict:
    """Estimate min/max payout for the ticket combination."""
    min_odds_product = 1.0
    max_odds_product = 1.0

    for sel in selections:
        horses = sel.get("selected_horses", [])
        if not horses:
            continue
        odds_list = [h.get("odds", 5.0) for h in horses]
        min_odds_product *= min(odds_list)
        max_odds_product *= max(odds_list)

    # WIN5 payout is roughly the product of individual race odds × 100
    # (simplified — actual WIN5 is pool-based)
    min_payout = int(min_odds_product * WIN5_PRICE * 0.7)  # 70% pool return
    max_payout = int(max_odds_product * WIN5_PRICE * 0.7)

    return {"min": min_payout, "max": max_payout}


def _estimate_hit_probability(selections: list[dict]) -> float:
    """Estimate probability that at least one combination hits."""
    prob = 1.0
    for sel in selections:
        horses = sel.get("selected_horses", [])
        if not horses:
            prob *= 0.0
            continue
        # P(at least one selected horse wins this race)
        race_prob = sum(h.get("ai_win_prob", 0.05) for h in horses)
        prob *= min(race_prob, 0.95)  # cap at 95%
    return prob


def _product(nums: list[int]) -> int:
    result = 1
    for n in nums:
        result *= n
    return result
