#!/usr/bin/env python3
"""Weekly WIN5 data update — fetch races, entries, predictions, save to Supabase.

Run via cron on Wednesday 12:00 JST (after JRA publishes WIN5 target races).

Usage:
    python -m scripts.weekly_update              # This week's Sunday
    python -m scripts.weekly_update --date 20260329   # Specific date
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

# Project root
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPTS_DIR, "..")
sys.path.insert(0, PROJECT_DIR)

# Load .env.local
env_path = os.path.join(PROJECT_DIR, ".env.local")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

from scrapers.win5 import fetch_win5_races, fetch_win5_carryover
from tools.executor import _fetch_entries, _fetch_predictions, _build_horse_data
from tools.volatility import calculate_volatility
from db.win5_manager import save_win5_races, save_horse_scores, get_win5_races

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


def _next_sunday() -> str:
    """Get next Sunday's date as YYYYMMDD."""
    now = datetime.now(JST)
    days_until_sunday = (6 - now.weekday()) % 7
    if days_until_sunday == 0 and now.hour >= 18:
        days_until_sunday = 7  # If it's Sunday evening, target next week
    sunday = now + timedelta(days=days_until_sunday)
    return sunday.strftime("%Y%m%d")


def run(date: str):
    logger.info(f"=== WIN5 Weekly Update: {date} ===")

    # Step 1: Fetch WIN5 target races
    logger.info("Step 1: Fetching WIN5 target races...")
    races = fetch_win5_races(date)
    if not races:
        logger.error("No WIN5 races found. Aborting.")
        return False

    logger.info(f"  Found {len(races)} races:")
    for r in races:
        logger.info(f"    R{r['race_order']}: {r['venue']}{r['race_number']}R {r['race_name']} (id={r['race_id']})")

    # Step 2: Fetch entries + predictions for each race
    logger.info("Step 2: Fetching entries & predictions...")
    enriched = []
    for race in races:
        race_id = race["race_id"]

        entries = _fetch_entries(race_id)
        if not entries:
            logger.warning(f"  No entries for {race_id} — may not be published yet")
            enriched.append({**race, "horses": [], "field_size": 0, "volatility_rank": 3})
            continue

        predictions = _fetch_predictions(race_id, entries)
        horses = _build_horse_data(entries, predictions)
        vol = calculate_volatility(horses, len(horses))

        enriched.append({
            **race,
            "horses": horses,
            "field_size": len(horses),
            "distance": entries.get("distance", ""),
            "volatility_rank": vol["volatility_rank"],
        })

        logger.info(f"  R{race['race_order']}: {len(horses)} horses, volatility={vol['volatility_rank']} ({vol['description']})")
        time.sleep(1)  # Be nice to API

    # Step 3: Save to Supabase
    logger.info("Step 3: Saving to Supabase...")
    saved_races = save_win5_races(date, enriched)
    logger.info(f"  Saved {saved_races} races")

    # Save horse scores
    db_races = get_win5_races(date)
    for db_race in db_races:
        matching = [r for r in enriched if r.get("race_order") == db_race["race_order"]]
        if matching and matching[0].get("horses"):
            count = save_horse_scores(db_race["id"], matching[0]["horses"])
            logger.info(f"  R{db_race['race_order']}: saved {count} horse scores")

    # Step 4: Carryover
    logger.info("Step 4: Checking carryover...")
    co = fetch_win5_carryover()
    logger.info(f"  Carryover: {co.get('carryover', 0):,}円")

    logger.info("=== Update complete ===")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WIN5 Weekly Data Update")
    parser.add_argument("--date", default="", help="Target date YYYYMMDD (default: next Sunday)")
    args = parser.parse_args()

    target_date = args.date or _next_sunday()
    success = run(target_date)
    sys.exit(0 if success else 1)
