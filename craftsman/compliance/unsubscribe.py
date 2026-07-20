"""One-click unsubscribe endpoint logic."""

from sqlalchemy.orm import Session

from craftsman.compliance.suppression import suppress
from craftsman.core.models import UnsubscribeToken

CONFIRM_HTML = """<!doctype html>
<html><head><title>Unsubscribed</title></head>
<body style="font-family: sans-serif; max-width: 480px; margin: 80px auto; text-align: center;">
<h2>You're unsubscribed.</h2>
<p>{email} won't receive any more emails from us.</p>
</body></html>"""


def process_unsubscribe(db: Session, token: str) -> str | None:
    """Suppress the token's email. Returns the email, or None if token unknown."""
    row = db.get(UnsubscribeToken, token)
    if row is None:
        return None
    suppress(db, row.lead_email, reason="unsubscribe")
    return row.lead_email
