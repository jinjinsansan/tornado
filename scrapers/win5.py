"""WIN5 target races scraper from netkeiba.com."""

import logging
import re

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

WIN5_URL = "https://race.netkeiba.com/top/win5.html"


def fetch_win5_races(date: str = "") -> list[dict]:
    """Fetch WIN5 target races from netkeiba.

    Args:
        date: YYYYMMDD format. Empty = this week's Sunday (default on netkeiba).

    Returns:
        List of 5 race dicts, ordered R1-R5:
        [
            {
                "race_order": 1,
                "race_id": "202606030210",
                "venue": "中山",
                "race_number": 10,
                "race_name": "...",
            },
            ...
        ]
    """
    url = WIN5_URL
    if date:
        url += f"?kaisai_date={date}"

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.encoding = "euc-jp"
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        logger.error(f"WIN5 page fetch failed: {e}")
        return []

    # Find race links (shutuba.html?race_id=XXXX)
    races = []
    seen_ids = set()

    for a in soup.select('a[href*="shutuba.html?race_id="]'):
        href = a.get("href", "")
        m = re.search(r"race_id=(\d+)", href)
        if not m:
            continue

        race_id = m.group(1)
        if race_id in seen_ids:
            continue
        seen_ids.add(race_id)

        text = a.get_text(strip=True)

        # Extract venue and race number from text (e.g. "中山10R千葉S")
        venue_match = re.search(
            r"(中山|阪神|中京|東京|京都|新潟|福島|小倉|札幌|函館)", text
        )
        num_match = re.search(r"(\d+)R", text)

        venue = venue_match.group(1) if venue_match else ""
        race_number = int(num_match.group(1)) if num_match else 0

        # Race name: remove venue+R prefix
        race_name = re.sub(r"^.*?\d+R", "", text).strip()

        races.append({
            "race_id": race_id,
            "venue": venue,
            "race_number": race_number,
            "race_name": race_name,
        })

    # Should be exactly 5 races
    if len(races) != 5:
        logger.warning(f"Expected 5 WIN5 races, got {len(races)}")

    # Add race_order (1-5)
    for i, race in enumerate(races):
        race["race_order"] = i + 1

    # Try to get carryover info
    carryover = 0
    co_match = re.search(r"キャリーオーバー[^\d]*?([\d,]+)\s*円", resp.text)
    if co_match:
        carryover = int(co_match.group(1).replace(",", ""))

    # Get WIN5 date from page
    win5_date = date
    if not win5_date:
        date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", resp.text)
        if date_match:
            win5_date = f"{date_match.group(1)}{int(date_match.group(2)):02d}{int(date_match.group(3)):02d}"

    logger.info(f"WIN5 races: {len(races)} races, date={win5_date}, carryover={carryover}")

    return races


def fetch_win5_carryover() -> dict:
    """Fetch current WIN5 carryover amount."""
    try:
        resp = requests.get(WIN5_URL, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.encoding = "euc-jp"

        carryover = 0
        co_match = re.search(r"キャリーオーバー[^\d]*?([\d,]+)\s*円", resp.text)
        if co_match:
            carryover = int(co_match.group(1).replace(",", ""))

        return {"carryover": carryover, "has_carryover": carryover > 0}
    except Exception as e:
        logger.error(f"Carryover fetch failed: {e}")
        return {"carryover": 0, "has_carryover": False}
