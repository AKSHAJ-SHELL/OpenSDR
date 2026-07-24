"""One-click unsubscribe endpoint logic."""

from sqlalchemy.orm import Session

from craftsman.compliance.suppression import suppress
from craftsman.core.models import UnsubscribeToken
from craftsman.core.tenancy import org_context, unscoped_context

CONFIRM_HTML = """<!doctype html>
<html><head><title>Unsubscribed</title></head>
<body style="font-family: sans-serif; max-width: 480px; margin: 80px auto; text-align: center;">
<h2>You're unsubscribed.</h2>
<p>{email} won't receive any more emails from us.</p>
</body></html>"""


def process_unsubscribe(db: Session, token: str) -> str | None:
    """Suppress the token's email. Returns the email, or None if token unknown.

    `/u/{token}` is unauthenticated by design (RFC 8058): the token IS the
    credential, so the lookup runs unscoped and the suppression happens inside
    the token's own org — one org's unsubscribe never touches another's list
    (unless the operator enabled overlay propagation, ⛔ Gate M5 Q1a)."""
    with unscoped_context():
        row = db.get(UnsubscribeToken, token)
    if row is None:
        return None
    with org_context(row.org_id):
        suppress(db, row.lead_email, reason="unsubscribe")
    return row.lead_email
