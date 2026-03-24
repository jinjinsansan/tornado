#!/usr/bin/env python3
"""Fetch WIN5 results after races finish.

Run via cron on Sunday 20:00 JST.

Usage:
    python -m scripts.fetch_results              # This week's Sunday
    python -m scripts.fetch_results --date 20260329
"""

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPTS_DIR, "..")
sys.path.insert(0, PROJECT_DIR)

env_path = os.path.join(PROJECT_DIR, ".env.local")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

import requests
from bs4 import BeautifulSoup

from db.win5_manager import save_win5_result, get_win5_races

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
WIN5_RESULT_URL = "https://race.netkeiba.com/top/win5.html"


def _today_str() -> str:
    return datetime.now(JST).strftime("%Y%m%d")


def fetch_win5_result(date: str = "") -> dict | None:
    """Fetch WIN5 result from netkeiba."""
    url = WIN5_RESULT_URL
    if date:
        url += f"?kaisai_date={date}"

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.encoding = "euc-jp"
        text = resp.text

        # Find payout
        payout = 0
        payout_match = re.search(r"払戻金[^\d]*?([\d,]+)\s*円", text)
        if payout_match:
            payout = int(payout_match.group(1).replace(",", ""))

        # Find carryover
        carryover = 0
        co_match = re.search(r"キャリーオーバー[^\d]*?([\d,]+)\s*円", text)
        if co_match:
            carryover = int(co_match.group(1).replace(",", ""))

        if payout == 0 and carryover == 0:
            logger.info("No result data found (race may not have finished yet)")
            return None

        return {
            "payout": payout,
            "carryover": carryover,
        }

    except Exception as e:
        logger.error(f"Result fetch failed: {e}")
        return None


def run(date: str):
    logger.info(f"=== WIN5 Result Fetch: {date} ===")

    result = fetch_win5_result(date)
    if not result:
        logger.warning("No result available yet")
        return False

    logger.info(f"  Payout: {result['payout']:,}円")
    logger.info(f"  Carryover: {result['carryover']:,}円")

    # Save to Supabase
    save_win5_result(
        date=date,
        winners=[],  # TODO: parse individual race winners
        payout=result["payout"],
        carryover=result["carryover"],
    )
    logger.info("  Saved to Supabase")

    logger.info("=== Done ===")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WIN5 Result Fetcher")
    parser.add_argument("--date", default="", help="Target date YYYYMMDD")
    args = parser.parse_args()

    target_date = args.date or _today_str()
    success = run(target_date)
    sys.exit(0 if success else 1)
