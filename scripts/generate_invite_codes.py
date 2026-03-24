#!/usr/bin/env python3
"""Generate invite codes and save to Supabase + CSV."""

import csv
import os
import random
import string
import sys

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

from db.supabase_client import get_client


def generate_code() -> str:
    """Generate a code like TRN-A3K9-X7M2"""
    chars = string.ascii_uppercase + string.digits
    part1 = "".join(random.choices(chars, k=4))
    part2 = "".join(random.choices(chars, k=4))
    return f"TRN-{part1}-{part2}"


def main(count: int = 200):
    sb = get_client()

    # Get existing codes to avoid duplicates
    existing = sb.table("invite_codes").select("code").execute()
    existing_codes = {r["code"] for r in (existing.data or [])}

    codes = []
    while len(codes) < count:
        code = generate_code()
        if code not in existing_codes:
            codes.append(code)
            existing_codes.add(code)

    # Insert to Supabase
    rows = [{"code": code} for code in codes]
    sb.table("invite_codes").insert(rows).execute()

    # Save CSV
    csv_path = os.path.join(PROJECT_DIR, "invite_codes.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["code", "status"])
        for code in codes:
            writer.writerow([code, "未使用"])

    print(f"Generated {len(codes)} invite codes")
    print(f"CSV saved: {csv_path}")
    print(f"Sample: {codes[0]}, {codes[1]}, {codes[2]}")


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    main(count)
