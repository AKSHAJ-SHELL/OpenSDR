"""Create an API key from the command line — the bootstrap path for the first key.

    python -m craftsman.create_key --name bootstrap --scopes admin
    python -m craftsman.create_key --name dashboard --scopes read operate

Prints the plaintext token once. Only its SHA-256 digest is stored, so the token
cannot be recovered later — copy it now.
"""

import argparse
import sys

from sqlalchemy import select

from craftsman.api.auth import SCOPES, generate_token, hash_token, key_prefix
from craftsman.core.db import run_migrations, session_scope
from craftsman.core.models import ApiKey, Org
from craftsman.core.tenancy import org_context, unscoped_context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="craftsman.create_key", description=__doc__)
    parser.add_argument("--name", required=True, help="human label for the key")
    parser.add_argument(
        "--scopes",
        required=True,
        nargs="+",
        choices=SCOPES,
        help="one or more of: read operate admin (admin implies operate implies read)",
    )
    parser.add_argument(
        "--org",
        default="default",
        help="org slug the key belongs to (M5.1; default: the default org)",
    )
    args = parser.parse_args(argv)

    run_migrations()  # ensure the schema (incl. api_keys) exists before inserting
    token = generate_token()
    scopes = list(dict.fromkeys(args.scopes))
    with session_scope() as db:
        # CLI runs as the operator on the box — resolving the slug is a
        # justified unscoped read; the insert happens inside that org
        with unscoped_context():
            org = db.scalar(select(Org).where(Org.slug == args.org))
        if org is None:
            print(f"No org with slug '{args.org}'. Existing orgs:", file=sys.stderr)
            with unscoped_context():
                for slug in db.scalars(select(Org.slug).order_by(Org.slug)):
                    print(f"  {slug}", file=sys.stderr)
            return 1
        with org_context(org.id):
            db.add(
                ApiKey(
                    name=args.name,
                    key_prefix=key_prefix(token),
                    key_hash=hash_token(token),
                    scopes=scopes,
                )
            )

    print(f"Created API key '{args.name}' with scopes {scopes} in org '{args.org}'.")
    print("Copy this token now — it will not be shown again:\n")
    print(f"  {token}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
