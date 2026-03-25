#!/usr/bin/env python3
"""Generate activation links (URL token + 4-digit PIN) and save to Supabase + CSV.

Usage:
  python scripts/generate_activation_links.py 200
"""

import csv
import os
import secrets
import sys
import time
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPTS_DIR, "..")
sys.path.insert(0, PROJECT_DIR)

env_path = os.path.join(PROJECT_DIR, ".env.local")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

from db.supabase_client import get_client


WEB_AUTH_SECRET = os.getenv("WEB_AUTH_SECRET", "")
if not WEB_AUTH_SECRET:
    raise RuntimeError("WEB_AUTH_SECRET is required to generate pin_hash")


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def pin_hash(pin: str) -> str:
    return hmac.new(WEB_AUTH_SECRET.encode(), pin.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_token() -> str:
    # 32 bytes => ~43 chars base64url after trimming '='
    return secrets.token_urlsafe(32)


def generate_pin() -> str:
    # 0000-9999
    return f"{secrets.randbelow(10000):04d}"


def main(count: int = 200):
    sb = get_client()

    base_url = os.getenv("ACTIVATION_BASE_URL", "https://www.tornadeai.com/activate")
    ttl_days = int(os.getenv("ACTIVATION_TTL_DAYS", "7"))
    expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)

    rows = []
    out = []
    for _ in range(count):
        token = generate_token()
        pin = generate_pin()

        th = sha256_hex(token)
        ph = pin_hash(pin)

        rows.append({
            "token_hash": th,
            "pin_hash": ph,
            "status": "issued",
            "attempts": 0,
            "expires_at": expires_at.isoformat(),
            "metadata": {},
        })
        out.append({
            "activation_url": f"{base_url}?t={token}",
            "pin": pin,
            "expires_at": expires_at.isoformat(),
        })

    ins = sb.table("activation_links").insert(rows).execute()
    if not ins.data:
        raise RuntimeError("Supabase insert failed (activation_links)")

    ts = time.strftime("%Y%m%d%H%M%S")
    csv_path = os.path.join(PROJECT_DIR, f"activation_links_{ts}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["activation_url", "pin", "expires_at"])
        w.writeheader()
        for r in out:
            w.writerow(r)

    print(f"Generated {len(out)} activation links")
    print(f"CSV saved: {csv_path}")
    print(f"Sample: {out[0]['activation_url']} PIN={out[0]['pin']}")


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    main(count)
