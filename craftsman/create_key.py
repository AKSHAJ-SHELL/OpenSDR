"""Create an API key from the command line — the bootstrap path for the first key.

    python -m craftsman.create_key --name bootstrap --scopes admin
    python -m craftsman.create_key --name dashboard --scopes read operate

Prints the plaintext token once. Only its SHA-256 digest is stored, so the token
cannot be recovered later — copy it now.
"""

import argparse
import sys

from craftsman.api.auth import SCOPES, generate_token, hash_token, key_prefix
from craftsman.core.db import init_db, session_scope
from craftsman.core.models import ApiKey


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
    args = parser.parse_args(argv)

    init_db()
    token = generate_token()
    scopes = list(dict.fromkeys(args.scopes))
    with session_scope() as db:
        db.add(
            ApiKey(
                name=args.name,
                key_prefix=key_prefix(token),
                key_hash=hash_token(token),
                scopes=scopes,
            )
        )

    print(f"Created API key '{args.name}' with scopes {scopes}.")
    print("Copy this token now — it will not be shown again:\n")
    print(f"  {token}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
