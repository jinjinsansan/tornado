"""WIN5 target races scraper from netkeiba.com."""

import logging
import re
import json

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

WIN5_URL = "https://race.netkeiba.com/top/win5.html"
RACE_CARD_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
ODDS_API_URL = "https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type=1"


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


def fetch_race_odds(race_id: str) -> dict[int, dict]:
    """Fetch odds data via netkeiba API (when available).

    Returns:
        {horse_number: {"odds": float, "popularity_rank": int}}
    """
    url = ODDS_API_URL.format(race_id=race_id)
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.status_code != 200:
            return {}
        payload = resp.json()
    except Exception:
        return {}

    data = payload.get("data") if isinstance(payload, dict) else ""
    if not data:
        return {}

    # Sometimes this API returns HTML snippet in "data"
    mapping: dict[int, dict] = {}
    try:
        # If JSON string
        if isinstance(data, str) and data.strip().startswith(("{", "[")):
            parsed = json.loads(data)
            # unknown schema; best-effort parse
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if str(k).isdigit() and isinstance(v, dict):
                        hn = int(k)
                        odds = float(v.get("odds", 0) or 0)
                        pop = int(v.get("popular", 0) or v.get("popularity_rank", 0) or 0)
                        if odds > 0:
                            mapping[hn] = {"odds": odds, "popularity_rank": pop}
            return mapping
    except Exception:
        pass

    try:
        soup = BeautifulSoup(data, "lxml")
        # Heuristic: rows often carry umaban in id like tr_13, and odds appear as text within
        for tr in soup.select("tr[id^='tr_']"):
            m = re.match(r"tr_(\\d+)$", tr.get("id", ""))
            if not m:
                continue
            hn = int(m.group(1))
            txt = tr.get_text(" ", strip=True)
            # Find first float-like token (odds)
            m2 = re.search(r"(\\d+\\.\\d+)", txt)
            odds = float(m2.group(1)) if m2 else 0.0
            if odds <= 0:
                continue
            mapping[hn] = {"odds": odds, "popularity_rank": 0}
        return mapping
    except Exception:
        return {}


def fetch_race_entries(race_id: str) -> dict | None:
    """Fetch race entries (horse list) from netkeiba shutuba page.

    Returns an entries dict compatible with the Dlogic data API shape used by this project.
    """
    url = RACE_CARD_URL.format(race_id=race_id)
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.encoding = "euc-jp"
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        logger.warning(f"Race card fetch failed: {race_id}: {e}")
        return None

    entries = []
    horses = []
    horse_numbers = []
    odds_list: list[float] = []

    for tr in soup.select("table.Shutuba_Table tr.HorseList"):
        # netkeiba may not embed umaban in the static HTML; tr id often contains it (e.g. id="tr_13")
        tr_id = tr.get("id", "")
        m = re.match(r"tr_(\d+)$", tr_id)
        if not m:
            continue
        horse_number = int(m.group(1))

        name_a = tr.select_one('td.HorseInfo a[href*="/horse/"]')
        if not name_a:
            continue
        horse_name = name_a.get_text(strip=True)

        waku_td = tr.select_one("td.Waku")
        waku = int(waku_td.get_text(strip=True)) if (waku_td and waku_td.get_text(strip=True).isdigit()) else 0

        # Odds might be blank ('---.-') early in the week.
        odds_td = tr.select_one("td.Txt_R.Popular")
        odds_text = odds_td.get_text(strip=True) if odds_td else ""
        try:
            odds = float(odds_text) if odds_text and odds_text != "---.-" else 0.0
        except Exception:
            odds = 0.0

        pop_td = tr.select_one("td.Popular_Ninki")
        pop_text = pop_td.get_text(strip=True) if pop_td else ""
        popularity_rank = int(pop_text) if pop_text.isdigit() else 0

        entry = {
            "horse_number": horse_number,
            "horse_name": horse_name,
            "odds": odds,
            "popularity_rank": popularity_rank,
            "waku": waku,
        }
        entries.append(entry)

    # Parse race meta
    distance = ""
    field_size = 0

    data01 = soup.select_one(".RaceData01")
    if data01:
        txt = data01.get_text(" ", strip=True)
        m = re.search(r"(芝|ダ|障)\s*(\d+)m", txt)
        if m:
            distance = f"{m.group(1)}{m.group(2)}m"

    data02 = soup.select_one(".RaceData02")
    if data02:
        txt = data02.get_text(" ", strip=True)
        m = re.search(r"(\d+)頭", txt)
        if m:
            field_size = int(m.group(1))

    if not entries:
        return None

    # Try fill odds/popularity via API if missing
    if any((e.get("odds", 0) or 0) <= 0 for e in entries):
        odds_map = fetch_race_odds(race_id)
        if odds_map:
            for e in entries:
                hn = e.get("horse_number")
                if hn in odds_map and ((e.get("odds", 0) or 0) <= 0):
                    e["odds"] = odds_map[hn].get("odds", e.get("odds", 0))
                if hn in odds_map and ((e.get("popularity_rank", 0) or 0) <= 0):
                    e["popularity_rank"] = odds_map[hn].get("popularity_rank", e.get("popularity_rank", 0))

    # Stabilize ordering
    entries.sort(key=lambda x: x.get("horse_number", 0))
    for e in entries:
        horses.append(e["horse_name"])
        horse_numbers.append(e["horse_number"])
        odds_list.append(e.get("odds", 0.0))

    return {
        "race_id": race_id,
        "entries": entries,
        "horses": horses,
        "horse_numbers": horse_numbers,
        "odds": odds_list,
        "distance": distance,
        "field_size": field_size or len(entries),
        "track_condition": "",
        "venue": "",
        "race_number": 0,
        "jockeys": [],
        "posts": [],
    }
