"""WIN5 volatility (波乱度) calculator.

Assigns each race a volatility rank 1-5:
  1 = 堅い (low upset chance)
  5 = 大荒れ (high upset chance)

Factors:
  - Field size (more horses = more volatile)
  - Top AI scores gap (small gap = competitive = volatile)
  - Favorite reliability (low AI rank for 1st fav = volatile)
  - Odds concentration (low entropy = volatile)
"""

import logging
import math

logger = logging.getLogger(__name__)


def calculate_volatility(horses: list[dict], field_size: int = 0) -> dict:
    """Calculate volatility for a single race.

    Args:
        horses: List of horse dicts with keys:
            - ai_win_prob: float (AI-estimated win probability)
            - market_prob: float (market-implied probability from odds)
            - odds: float
            - popularity_rank: int
        field_size: Number of horses (defaults to len(horses))

    Returns:
        {
            "volatility_rank": 1-5,
            "factors": {
                "field_size_score": float,
                "competitiveness_score": float,
                "favorite_reliability_score": float,
                "odds_entropy_score": float,
            },
            "raw_score": float (0-100),
            "description": str,
        }
    """
    if not horses:
        return {"volatility_rank": 3, "raw_score": 50, "factors": {}, "description": "データなし"}

    n = field_size or len(horses)

    # Factor 1: Field size (0-25 points)
    # 8 horses = 5pt, 12 = 15pt, 16+ = 25pt
    field_score = min(25, max(0, (n - 6) * 3.5))

    # Factor 2: Competitiveness - gap between top 3 AI scores (0-30 points)
    ai_probs = sorted([h.get("ai_win_prob", 0) for h in horses], reverse=True)
    if len(ai_probs) >= 3 and ai_probs[0] > 0:
        top3_gap = ai_probs[0] - ai_probs[2]
        # Small gap = competitive = high volatility
        competitiveness = max(0, 30 - top3_gap * 300)
    else:
        competitiveness = 15  # neutral

    # Factor 3: Favorite reliability (0-25 points)
    # If the betting favorite has low AI support, it's volatile
    fav = None
    for h in horses:
        if h.get("popularity_rank") == 1:
            fav = h
            break

    if fav and fav.get("ai_win_prob", 0) > 0:
        fav_ai = fav["ai_win_prob"]
        fav_market = fav.get("market_prob", 0)
        if fav_market > 0:
            # AI thinks favorite is overvalued → volatile
            gap = fav_market - fav_ai
            fav_reliability = min(25, max(0, gap * 200))
        else:
            fav_reliability = 12
    else:
        fav_reliability = 12

    # Factor 4: Odds entropy (0-20 points)
    # High entropy = many horses with similar odds = volatile
    odds_list = [h.get("odds", 0) for h in horses if h.get("odds", 0) > 0]
    if odds_list:
        total_inv = sum(1 / o for o in odds_list)
        probs = [(1 / o) / total_inv for o in odds_list]
        entropy = -sum(p * math.log2(p) for p in probs if p > 0)
        max_entropy = math.log2(len(odds_list)) if len(odds_list) > 1 else 1
        normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0
        entropy_score = normalized_entropy * 20
    else:
        entropy_score = 10

    # Total raw score (0-100)
    raw_score = field_score + competitiveness + fav_reliability + entropy_score

    # Convert to rank 1-5
    if raw_score >= 75:
        rank = 5
        desc = "大荒れ警戒"
    elif raw_score >= 60:
        rank = 4
        desc = "荒れ模様"
    elif raw_score >= 45:
        rank = 3
        desc = "やや混戦"
    elif raw_score >= 30:
        rank = 2
        desc = "やや堅め"
    else:
        rank = 1
        desc = "堅い"

    return {
        "volatility_rank": rank,
        "raw_score": round(raw_score, 1),
        "factors": {
            "field_size_score": round(field_score, 1),
            "competitiveness_score": round(competitiveness, 1),
            "favorite_reliability_score": round(fav_reliability, 1),
            "odds_entropy_score": round(entropy_score, 1),
        },
        "description": desc,
    }
