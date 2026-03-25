"""JRA race list scraper (all races for a given date)."""

import logging
import re

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

RACE_LIST_SUB_URL = "https://race.netkeiba.com/top/race_list_sub.html"

_VENUES_RE = re.compile(r"(中山|阪神|中京|東京|京都|新潟|福島|小倉|札幌|函館)")


def fetch_race_list(date: str) -> list[dict]:
    """Fetch all JRA races for a given date.

    Args:
        date: YYYYMMDD

    Returns:
        [
            {
                "race_id": "...",
                "venue": "中山",
                "race_number": 10,
                "race_name": "...",
                "start_time": "14:15",
                "distance": "芝2000m",
            },
            ...
        ]
    """
    if not date or not re.fullmatch(r"\d{8}", date):
        return []

    try:
        resp = requests.get(
            RACE_LIST_SUB_URL,
            params={"kaisai_date": date},
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        logger.warning(f"race_list_sub fetch failed: {date}: {e}")
        return []

    result: list[dict] = []
    seen = set()

    for dl in soup.select("dl.RaceList_DataList"):
        title = dl.select_one("dt.RaceList_DataHeader .RaceList_DataTitle")
        title_text = title.get_text(" ", strip=True) if title else ""
        m_venue = _VENUES_RE.search(title_text)
        venue = m_venue.group(1) if m_venue else ""

        for li in dl.select("dd.RaceList_Data li.RaceList_DataItem"):
            a = li.select_one('a[href*="shutuba.html?race_id="]')
            if not a:
                continue
            href = a.get("href", "")
            m = re.search(r"race_id=(\d+)", href)
            if not m:
                continue
            race_id = m.group(1)
            if race_id in seen:
                continue
            seen.add(race_id)

            num_txt = li.select_one(".Race_Num span")
            num_text = num_txt.get_text(" ", strip=True) if num_txt else a.get_text(" ", strip=True)
            m_num = re.search(r"(\d+)R", num_text)
            race_number = int(m_num.group(1)) if m_num else 0

            name_el = li.select_one(".RaceList_ItemTitle .ItemTitle")
            race_name = name_el.get_text(" ", strip=True) if name_el else ""

            time_el = li.select_one(".RaceList_Itemtime")
            start_time = time_el.get_text(" ", strip=True) if time_el else ""

            dist_el = li.select_one(".RaceList_ItemLong")
            distance = dist_el.get_text(" ", strip=True) if dist_el else ""

            result.append({
                "race_id": race_id,
                "venue": venue,
                "race_number": race_number,
                "race_name": race_name,
                "start_time": start_time,
                "distance": distance,
            })

    # Stable ordering: venue blocks appear in page order; within each block, race_number order
    result.sort(key=lambda x: (x.get("venue", ""), x.get("race_number", 0), x.get("race_id", "")))
    return result
