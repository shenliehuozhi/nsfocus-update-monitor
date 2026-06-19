#!/usr/bin/env python3
"""Migrate subscription_rules.attachment_max_mb → customers.attachment_max_mb.

Background
----------
After the refactor that moved the email attachment size source from the
subscription rule to the customer record (so the per-customer setting on
the customer profile drives everything), any rule that previously had a
non-zero attachment_max_mb but whose customer had 0/unset would silently
revert to the global default. This script migrates those values
one-shot so existing user intent is preserved.

Behavior
--------
For each (subscription_rule, customer) pair where
    subscription_rules.attachment_max_mb > 0
    AND customer.attachment_max_mb is 0 (unset)
the script writes MAX(rule.attachment_max_mb) across that customer's
rules into customer.attachment_max_mb. Multiple rules sharing a
customer are collapsed with MAX to keep the largest explicit user
intent (a stricter override would surprise users who intentionally set
a higher cap on one rule).

Usage
-----
Dry-run (default): print planned changes, do not write.
Apply:            --apply
"""

import argparse
import os
import sys

# Add project root so we can import src.models.database
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.database import get_db


def plan(db):
    """Return a list of (customer_id, customer_name, new_mb, source_rules)
    tuples describing the planned updates."""
    rows = db.execute(
        """
        SELECT sr.customer_id AS customer_id,
               c.name AS customer_name,
               sr.attachment_max_mb AS rule_mb,
               sr.id AS rule_id,
               sr.name AS rule_name
        FROM subscription_rules sr
        LEFT JOIN customers c ON sr.customer_id = c.id
        WHERE sr.attachment_max_mb > 0
          AND sr.customer_id IS NOT NULL
        ORDER BY sr.customer_id, sr.id
        """
    ).fetchall()

    # Only migrate when the customer's current value is 0/unset
    per_customer = {}
    for r in rows:
        cust_row = db.execute(
            "SELECT attachment_max_mb FROM customers WHERE id = ?",
            (r["customer_id"],),
        ).fetchone()
        cust_mb = (cust_row["attachment_max_mb"] if cust_row else 0) or 0
        if cust_mb > 0:
            # Customer already configured — leave alone
            continue
        bucket = per_customer.setdefault(
            r["customer_id"],
            {
                "customer_name": r["customer_name"] or "(unnamed)",
                "max_mb": 0,
                "rules": [],
            },
        )
        bucket["max_mb"] = max(bucket["max_mb"], int(r["rule_mb"]))
        bucket["rules"].append(f'{r["rule_name"]}({r["rule_id"]})={r["rule_mb"]}MB')

    return [
        (cid, info["customer_name"], info["max_mb"], info["rules"])
        for cid, info in per_customer.items()
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to the DB. Without this flag the script is a dry-run.",
    )
    args = parser.parse_args()

    db = get_db()
    plan_rows = plan(db)

    if not plan_rows:
        print("Nothing to migrate.")
        return 0

    print(f"{'DRY-RUN' if not args.apply else 'APPLY'} — {len(plan_rows)} customer(s):")
    for cid, name, new_mb, rules in plan_rows:
        print(f"  customer {cid} {name!r}: → {new_mb}MB")
        print(f"    sources: {', '.join(rules)}")

    if not args.apply:
        print()
        print("Re-run with --apply to write these changes.")
        return 0

    # Apply
    for cid, _name, new_mb, _rules in plan_rows:
        db.execute(
            "UPDATE customers SET attachment_max_mb = ? WHERE id = ?",
            (new_mb, cid),
        )
    db.commit()
    print(f"\nMigrated {len(plan_rows)} customer(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())