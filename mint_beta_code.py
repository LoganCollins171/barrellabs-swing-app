#!/usr/bin/env python3
"""
BarrelLabs SwingAI — Beta code minting CLI.

This is an ADMIN-ONLY script. It writes directly to public.beta_codes
using the Supabase SERVICE ROLE key (bypasses RLS — by design, because
beta_codes has no public INSERT policy).

DO NOT deploy this script to a public host. Run it locally only.

Examples
--------
Mint a one-shot code for a single tester, 30 days of Solo Pro:
    ./mint_beta_code.py --plan solo_pro --note "alex@example.com"

Mint a launch batch — 50 codes, 30 days each, single-use:
    ./mint_beta_code.py --plan solo_pro --count 50 --note "Launch wave 1"

Mint a 60-day Coach Pro code that can be redeemed by up to 5 different
testers (e.g. a single travel-team trial):
    ./mint_beta_code.py --plan coach_pro --duration 60 --uses 5 \
        --note "Tigers 16U trial"

Custom code (instead of an auto-generated one):
    ./mint_beta_code.py --plan solo_pro --code BL-FOUNDERS-001 \
        --note "Founding-100 list"

Output
------
Prints one code per line to stdout (so you can pipe to a file / mailer).
Sends nothing — distributing the codes is on you.

Setup
-----
Put these in .streamlit/secrets.toml (do NOT commit):

    [supabase]
    url = "https://<project>.supabase.co"
    publishable_key = "eyJhbGc..."    # anon — already used by the app
    service_role_key = "eyJhbGc..."   # NEW — only used by this script

Or export as env vars:
    SUPABASE_URL=...
    SUPABASE_SERVICE_ROLE_KEY=...
"""

from __future__ import annotations

import argparse
import os
import random
import string
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------
#  Constants
# --------------------------------------------------------------------
VALID_PLANS = {"solo_pro", "family_pro", "coach_pro"}

# BL-004C (3): companion to the pricing.py checkout pause. Coach Pro's
# advertised roster/player-viewing features have no implementation
# anywhere in this codebase (verified in BL-004B). A beta code minted for
# coach_pro grants that entitlement through a path the pricing-page pause
# doesn't touch at all (this script writes straight to Supabase via the
# service-role key, bypassing the app entirely) — so it needs its own,
# separate guard. This blocks the operator (this is a local admin-only
# script, not customer-facing) from accidentally minting a new coach_pro
# code while the feature is incomplete. Does not affect solo_pro/
# family_pro, does not revoke any code already minted. Reverting is a
# one-line flip of this constant.
COACH_PRO_CODE_MINTING_PAUSED = True
DEFAULT_DURATION_DAYS = 30
CODE_ALPHABET = string.ascii_uppercase + string.digits
CODE_RANDOM_LEN = 6  # post-prefix, so BL-XXXXXX = 9 chars total


# --------------------------------------------------------------------
#  Credentials — env vars > secrets.toml
# --------------------------------------------------------------------
def _load_creds() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        return url, key

    # Fall back to .streamlit/secrets.toml
    try:
        import tomllib  # py3.11+
    except ImportError:
        try:
            import tomli as tomllib   # type: ignore
        except ImportError:
            print(
                "ERROR: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set "
                "and tomllib not available to read secrets.toml.",
                file=sys.stderr,
            )
            sys.exit(2)

    secrets_path = Path(__file__).parent / ".streamlit" / "secrets.toml"
    if not secrets_path.is_file():
        print(
            f"ERROR: Supabase credentials missing. Either:\n"
            f"  • export SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY, or\n"
            f"  • add them under [supabase] in {secrets_path}",
            file=sys.stderr,
        )
        sys.exit(2)

    with open(secrets_path, "rb") as fh:
        cfg = tomllib.load(fh)
    sb = cfg.get("supabase") or {}
    url = url or sb.get("url")
    key = key or sb.get("service_role_key")
    if not url or not key:
        print(
            "ERROR: [supabase].url / [supabase].service_role_key missing "
            "from secrets.toml.",
            file=sys.stderr,
        )
        sys.exit(2)
    return url, key


def _make_client():
    """Build a Supabase client bound to the SERVICE ROLE key."""
    try:
        from supabase import create_client
    except ImportError:
        print(
            "ERROR: supabase-py not installed. Run:\n"
            "    ./venv/bin/pip install supabase",
            file=sys.stderr,
        )
        sys.exit(2)
    url, key = _load_creds()
    return create_client(url, key)


# --------------------------------------------------------------------
#  Code generation
# --------------------------------------------------------------------
def _generate_code(prefix: str = "BL") -> str:
    """Generate a fresh code like BL-XXXXXX. Random + collision-checked
    upstream by the unique constraint on beta_codes.code."""
    rand = "".join(random.choices(CODE_ALPHABET, k=CODE_RANDOM_LEN))
    return f"{prefix}-{rand}"


# --------------------------------------------------------------------
#  Insert
# --------------------------------------------------------------------
def _insert_code(
    sb,
    *,
    code: str,
    plan_id: str,
    duration_days: int,
    max_redemptions: int,
    expires_at: Optional[datetime],
    notes: Optional[str],
) -> dict:
    payload = {
        "code":             code,
        "plan_id":          plan_id,
        "duration_days":    duration_days,
        "max_redemptions":  max_redemptions,
        "redeemed_count":   0,
        "expires_at":       expires_at.isoformat() if expires_at else None,
        "notes":            notes,
    }
    resp = sb.table("beta_codes").insert(payload).execute()
    rows = resp.data or []
    if not rows:
        raise RuntimeError(f"Insert returned no rows for code {code!r}")
    return rows[0]


# --------------------------------------------------------------------
#  CLI
# --------------------------------------------------------------------
def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        description="Mint BarrelLabs SwingAI beta/promo codes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--plan", default="solo_pro", choices=sorted(VALID_PLANS),
                   help="Plan tier the code grants (default: solo_pro).")
    p.add_argument("--duration", type=int, default=DEFAULT_DURATION_DAYS,
                   help=f"Duration in days each redemption grants (default: {DEFAULT_DURATION_DAYS}).")
    p.add_argument("--count", type=int, default=1,
                   help="How many unique codes to mint (default: 1).")
    p.add_argument("--uses", type=int, default=1,
                   help="How many times each code can be redeemed before "
                        "exhausting (default: 1 — single-use).")
    p.add_argument("--expires-in-days", type=int, default=None,
                   help="Optional: code stops being redeemable this many "
                        "days from now (different from --duration, which "
                        "is the per-redemption grant length).")
    p.add_argument("--code", default=None,
                   help="Custom code string. Only works with --count 1.")
    p.add_argument("--note", default=None,
                   help="Free-form note (e.g. campaign name / recipient).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be inserted without writing.")
    args = p.parse_args(argv)

    if args.duration <= 0:
        print("ERROR: --duration must be > 0.", file=sys.stderr)
        return 2
    if args.count < 1:
        print("ERROR: --count must be >= 1.", file=sys.stderr)
        return 2
    if args.uses < 1:
        print("ERROR: --uses must be >= 1.", file=sys.stderr)
        return 2
    if args.code and args.count != 1:
        print("ERROR: --code can only be combined with --count 1.", file=sys.stderr)
        return 2
    if args.plan == "coach_pro" and COACH_PRO_CODE_MINTING_PAUSED:
        print(
            "ERROR: Coach Pro code minting is paused (BL-004C) — its "
            "advertised roster/player-viewing features aren't built yet. "
            "Flip COACH_PRO_CODE_MINTING_PAUSED in this file to override "
            "if you're intentionally testing that incomplete feature.",
            file=sys.stderr,
        )
        return 2

    expires_at: Optional[datetime] = None
    if args.expires_in_days is not None:
        if args.expires_in_days <= 0:
            print("ERROR: --expires-in-days must be > 0.", file=sys.stderr)
            return 2
        expires_at = datetime.now(timezone.utc) + timedelta(days=args.expires_in_days)

    # ---- Build the list of codes we want to insert ----
    codes_to_insert: list[str] = []
    if args.code:
        codes_to_insert.append(args.code.strip().upper())
    else:
        seen: set[str] = set()
        # Generate slightly more than needed to absorb (extremely unlikely)
        # local collisions; uniqueness against the DB is enforced by the
        # primary key on beta_codes.code (insert will error if duplicate).
        while len(codes_to_insert) < args.count:
            c = _generate_code()
            if c in seen:
                continue
            seen.add(c)
            codes_to_insert.append(c)

    if args.dry_run:
        print(f"# DRY RUN — would insert {len(codes_to_insert)} code(s):")
        print(f"# plan={args.plan}  duration={args.duration}d  "
              f"uses={args.uses}  expires_at={expires_at}  note={args.note!r}")
        for c in codes_to_insert:
            print(c)
        return 0

    # ---- Actually insert ----
    sb = _make_client()
    inserted: list[str] = []
    failures: list[tuple[str, str]] = []
    for c in codes_to_insert:
        try:
            row = _insert_code(
                sb,
                code=c,
                plan_id=args.plan,
                duration_days=args.duration,
                max_redemptions=args.uses,
                expires_at=expires_at,
                notes=args.note,
            )
            inserted.append(row.get("code") or c)
        except Exception as exc:
            failures.append((c, str(exc)))

    for c in inserted:
        print(c)

    if failures:
        print(
            f"\n# {len(failures)} code(s) failed to insert:",
            file=sys.stderr,
        )
        for c, err in failures:
            print(f"#   {c}: {err}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
