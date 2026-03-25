#!/usr/bin/env python3
"""Hourly monitor: checks public pages/API and sends a Telegram status report."""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPTS_DIR, "..")
sys.path.insert(0, PROJECT_DIR)

# Load .env.local (systemd EnvironmentFile is preferred; this is a fallback)
env_path = os.path.join(PROJECT_DIR, ".env.local")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), val)

JST = timezone(timedelta(hours=9))


def _check(name: str, url: str, ok_statuses: set[int], expect_text: str | None = None, expect_json_key: str | None = None):
    t0 = time.time()
    try:
        r = requests.get(url, timeout=15, allow_redirects=True)
        ms = int((time.time() - t0) * 1000)
        status = r.status_code
        ok = status in ok_statuses

        note = ""
        if ok and expect_text is not None:
            ok = expect_text in (r.text or "")
            note = f"text~{expect_text}" if expect_text else ""
        if ok and expect_json_key is not None:
            try:
                data = r.json()
                ok = expect_json_key in data
                note = f"json~{expect_json_key}"
            except Exception:
                ok = False

        return {
            "name": name,
            "url": url,
            "ok": bool(ok),
            "status": status,
            "ms": ms,
            "note": note,
        }
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return {
            "name": name,
            "url": url,
            "ok": False,
            "status": None,
            "ms": ms,
            "error": f"{type(e).__name__}: {e}",
        }


def _telegram_send(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        return resp.status_code == 200
    except Exception:
        return False


def main():
    checks = [
        ("TOP", "https://www.tornadeai.com/", {200}),
        ("CHAT", "https://www.tornadeai.com/chat", {200}),
        ("API_HEALTH", "https://api.tornadeai.com/health", {200}, "OK", None),
        ("WIN5_RESULTS", "https://api.tornadeai.com/api/win5/results/recent?limit=1", {200}, None, "results"),
        # Wide is auth-protected; 401 is still a valid "service alive" response.
        ("WIDE_RACES", "https://api.tornadeai.com/api/wide/races", {200, 401, 403}),
    ]

    results = []
    overall_ok = True
    for item in checks:
        name, url, ok_statuses = item[0], item[1], item[2]
        expect_text = item[3] if len(item) >= 4 else None
        expect_json_key = item[4] if len(item) >= 5 else None
        res = _check(name, url, ok_statuses, expect_text=expect_text, expect_json_key=expect_json_key)
        results.append(res)
        overall_ok = overall_ok and bool(res["ok"])

    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")
    header = f"TornadoAI Monitor (hourly)\n{ts}\noverall: {'OK' if overall_ok else 'FAIL'}"
    lines = [header, ""]
    for r in results:
        if r.get("ok"):
            st = r.get("status")
            ms = r.get("ms")
            note = f" ({r.get('note')})" if r.get("note") else ""
            lines.append(f"[OK] {r['name']}: {st} {ms}ms{note}")
        else:
            st = r.get("status")
            ms = r.get("ms")
            err = r.get("error", "")
            suffix = f" {err}" if err else ""
            lines.append(f"[FAIL] {r['name']}: {st} {ms}ms{suffix}")

    _telegram_send("\n".join(lines))


if __name__ == "__main__":
    main()
