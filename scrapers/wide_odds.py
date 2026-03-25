"""Wide odds scraper (best-effort).

Note:
  netkeiba odds data availability depends on timing (often empty earlier in the week).
"""

import json
import logging
import re
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

WIDE_API_URL = "https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={race_id}&type=4"


def _to_int(x: Any) -> int | None:
    try:
        if isinstance(x, bool):
            return None
        if isinstance(x, int):
            return x
        s = str(x).strip()
        if not s:
            return None
        if s.isdigit():
            return int(s)
        return None
    except Exception:
        return None


def _to_float(x: Any) -> float | None:
    try:
        if isinstance(x, bool):
            return None
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip()
        if not s:
            return None
        s = s.replace(",", "")
        return float(s)
    except Exception:
        return None


def _visit_pairs(obj: Any, out: dict[tuple[int, int], dict]) -> None:
    """Recursively extract [a,b,odds...] like structures."""
    if isinstance(obj, dict):
        for v in obj.values():
            _visit_pairs(v, out)
        return

    if isinstance(obj, list):
        # direct row: [a,b,odds] or [a,b,min,max]
        if len(obj) >= 3:
            a = _to_int(obj[0])
            b = _to_int(obj[1])
            o1 = _to_float(obj[2])
            if a and b and o1 and o1 > 0:
                lo = o1
                hi = _to_float(obj[3]) if len(obj) >= 4 else None
                if hi and hi > 0 and hi < lo:
                    lo, hi = hi, lo
                key = (min(a, b), max(a, b))
                out[key] = {
                    "min": round(lo, 1),
                    "max": round(hi, 1) if hi else round(lo, 1),
                }
                return

        for it in obj:
            _visit_pairs(it, out)
        return


def _extract_from_html(html: str) -> dict[tuple[int, int], dict]:
    soup = BeautifulSoup(html, "lxml")
    out: dict[tuple[int, int], dict] = {}

    # Try: matrix tables — collect header numbers then parse cells
    for table in soup.find_all("table"):
        # Heuristic: wide matrix has many numeric cells
        ths = [th.get_text(" ", strip=True) for th in table.find_all("th")]
        nums = [int(t) for t in ths if t.isdigit()]
        if len(nums) < 6:
            continue

        # Build column headers (first row)
        rows = table.find_all("tr")
        if not rows:
            continue
        header = rows[0]
        col_nums = []
        for th in header.find_all(["th", "td"]):
            t = th.get_text(" ", strip=True)
            if t.isdigit():
                col_nums.append(int(t))
        if len(col_nums) < 6:
            continue

        for tr in rows[1:]:
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            row_no = None
            # row header
            t0 = cells[0].get_text(" ", strip=True)
            if t0.isdigit():
                row_no = int(t0)
            if not row_no:
                continue
            for idx, td in enumerate(cells[1:], start=0):
                if idx >= len(col_nums):
                    break
                col_no = col_nums[idx]
                if col_no <= row_no:
                    continue
                t = td.get_text(" ", strip=True)
                if not t:
                    continue
                m = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", t)
                if m:
                    lo = float(m.group(1))
                    hi = float(m.group(2))
                    out[(row_no, col_no)] = {"min": round(min(lo, hi), 1), "max": round(max(lo, hi), 1)}
                    continue
                m2 = re.search(r"(\d+(?:\.\d+)?)", t)
                if m2:
                    o = float(m2.group(1))
                    if o > 0:
                        out[(row_no, col_no)] = {"min": round(o, 1), "max": round(o, 1)}

        if out:
            return out

    return out


def fetch_wide_odds_pairs(race_id: str) -> dict[tuple[int, int], dict]:
    """Fetch wide odds per pair.

    Returns:
      {(a,b): {"min": float, "max": float}}  where a<b
    """
    url = WIDE_API_URL.format(race_id=race_id)
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return {}
        payload = resp.json()
    except Exception as e:
        logger.debug(f"wide odds api failed: {race_id}: {e}")
        return {}

    data = payload.get("data") if isinstance(payload, dict) else None
    if not data:
        return {}

    # Data sometimes comes as JSON string or HTML snippet.
    if isinstance(data, str):
        s = data.strip()
        if not s:
            return {}
        if s.startswith(("{", "[")):
            try:
                data = json.loads(s)
            except Exception:
                return {}
        else:
            return _extract_from_html(s)

    # Dict/list data: best-effort recursion
    out: dict[tuple[int, int], dict] = {}
    _visit_pairs(data, out)
    return out
