"""Pure helpers for validating Telegram source-chat setup and channel updates."""
import re
from urllib.parse import urlsplit

SUPPORTED_SOURCE_CHAT_TYPES = frozenset({"group", "supergroup", "channel"})
SUPPORTED_SOURCE_UPDATE_TYPES = frozenset({
    "message", "edited_message", "channel_post", "edited_channel_post"
})


def normalize_source_chat_reference(value: str) -> str | int:
    """Return a Bot API chat reference for a public t.me link/username or numeric ID."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Missing channel/group username or numeric chat ID.")
    if raw.startswith(("https://t.me/", "http://t.me/", "t.me/")):
        parsed = urlsplit(raw if "://" in raw else "https://" + raw)
        if (parsed.hostname or "").lower() not in ("t.me", "www.t.me"):
            raise ValueError("Only t.me links are supported.")
        parts = [part for part in parsed.path.split("/") if part]
        if not parts or parts[0].startswith("+") or parts[0] == "joinchat":
            raise ValueError(
                "Private invite links cannot identify a chat through getChat. "
                "Use its numeric -100… chat ID after adding the bot, or a public @username."
            )
        raw = parts[0]
    if raw.startswith("@"):
        if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", raw):
            raise ValueError("That does not look like a valid public Telegram username.")
        return raw
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", raw):
        return "@" + raw
    raise ValueError("Use @publicusername, a public t.me link, or a numeric chat ID.")


def source_chat_matches(chat_type: str, chat_id: int, expected_chat_id: int | None) -> bool:
    return chat_type in SUPPORTED_SOURCE_CHAT_TYPES and expected_chat_id is not None and int(chat_id) == int(expected_chat_id)


def source_sender_details(from_user=None, sender_chat=None):
    """Return (id, display name, username) for either a person or channel sender."""
    if from_user is not None:
        name = " ".join(part for part in [getattr(from_user, "first_name", ""),
                                            getattr(from_user, "last_name", "")] if part).strip()
        name = name or getattr(from_user, "username", "") or ""
        return int(getattr(from_user, "id", 0) or 0), name, getattr(from_user, "username", "") or ""
    if sender_chat is not None:
        return (int(getattr(sender_chat, "id", 0) or 0),
                getattr(sender_chat, "title", "") or "Channel publisher",
                getattr(sender_chat, "username", "") or "")
    return 0, "", ""
