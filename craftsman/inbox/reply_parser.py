"""Strip quoted history from inbound replies before classification."""

from email_reply_parser import EmailReplyParser


def strip_quoted(body: str) -> str:
    """Return only the fresh reply text (no quoted thread, no signature blocks)."""
    reply = EmailReplyParser.parse_reply(body or "")
    return (reply or "").strip()
