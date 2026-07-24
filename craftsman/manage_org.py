"""Instance-operator org management (M5.1c) — runs on the box, like create_key.

    python -m craftsman.manage_org list
    python -m craftsman.manage_org create --name "Acme" --slug acme
    python -m craftsman.manage_org set-quota --org acme --daily-send-cap 500 \
        --max-mailboxes 5 --enrichment-daily-budget 200
    python -m craftsman.manage_org set-quota --org acme --daily-send-cap unlimited

Quotas are host-imposed: there is deliberately no API for a tenant to change
its own. `unlimited` (or omitting a flag) clears/keeps a quota respectively.
"""

import argparse
import sys

from sqlalchemy import select

from craftsman.core.db import run_migrations, session_scope
from craftsman.core.models import Org
from craftsman.core.tenancy import unscoped_context


def _quota(value: str | None):
    """None = not provided (keep); 'unlimited' = clear; else int."""
    if value is None:
        return ...  # sentinel: leave unchanged
    if value.lower() in ("unlimited", "none", "null"):
        return None
    return int(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="craftsman.manage_org", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list orgs with quotas and usage")

    create = sub.add_parser("create", help="create a new org")
    create.add_argument("--name", required=True)
    create.add_argument("--slug", required=True)

    quota = sub.add_parser("set-quota", help="set/clear per-org quotas")
    quota.add_argument("--org", required=True, help="org slug")
    quota.add_argument("--daily-send-cap", default=None)
    quota.add_argument("--max-mailboxes", default=None)
    quota.add_argument("--enrichment-daily-budget", default=None)

    args = parser.parse_args(argv)
    run_migrations()

    with unscoped_context(), session_scope() as db:
        if args.cmd == "list":
            for org in db.scalars(select(Org).order_by(Org.slug)):
                print(
                    f"{org.slug}: name={org.name!r} "
                    f"send_cap={org.daily_send_cap or 'unlimited'} (used {org.sent_today}) "
                    f"mailboxes={org.max_mailboxes or 'unlimited'} "
                    f"enrich={org.enrichment_daily_budget or 'unlimited'} "
                    f"(used {org.enrichment_calls_today})"
                )
            return 0

        if args.cmd == "create":
            if db.scalar(select(Org).where(Org.slug == args.slug)):
                print(f"org '{args.slug}' already exists", file=sys.stderr)
                return 1
            db.add(Org(name=args.name, slug=args.slug))
            print(f"Created org '{args.slug}'. Mint a key with:")
            print(f"  python -m craftsman.create_key --name bootstrap --scopes admin --org {args.slug}")
            return 0

        # set-quota
        org = db.scalar(select(Org).where(Org.slug == args.org))
        if org is None:
            print(f"no org with slug '{args.org}'", file=sys.stderr)
            return 1
        for attr, raw in (
            ("daily_send_cap", args.daily_send_cap),
            ("max_mailboxes", args.max_mailboxes),
            ("enrichment_daily_budget", args.enrichment_daily_budget),
        ):
            val = _quota(raw)
            if val is not ...:
                setattr(org, attr, val)
        db.add(org)
        print(
            f"{org.slug}: send_cap={org.daily_send_cap or 'unlimited'} "
            f"mailboxes={org.max_mailboxes or 'unlimited'} "
            f"enrich={org.enrichment_daily_budget or 'unlimited'}"
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
