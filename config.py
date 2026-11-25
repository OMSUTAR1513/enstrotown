"""All editable bot copy, source settings, aliases and matching limits live here."""

# Set this only if you already know the numeric ID of the private source group.
# Otherwise leave None and run /setsource@YourBotUsername in that group.
SOURCE_CHAT_ID = None
# Keep invite links out of the source code when sharing/selling it. Configure a
# private listing group with /setsource https://t.me/+YOUR_INVITE_LINK instead.
SOURCE_GROUP_INVITE_LINK = ""
ADMIN_GROUP_INVITE_LINK = ""

BOT_DISPLAY_NAME = "Abhinazh Bot V5.8.5 - Account Finder"
# Optional fallback contact. Leave blank to use the username of the person who runs /setadmin.
OWNER_USERNAME = ""

# Immutable root administrator. Authorization is based on Telegram numeric user ID, never username.
SUPERADMIN_USER_ID = 1485615852

LOCAL_MEDIA_MAX_MB = 100
AUTO_BACKUP_RETENTION = 5
WELCOME_TEXT = (
    "👋 Welcome to {bot_name}!\n\n"
    "Find available account listings quickly and filter them by kingdom, amount, features or recency.\n\n"
    "🔎 Search: type what you need or use /find <filters>\n"
    "💰 Amount: use Filter by Amount or /filter amount 500-2000\n"
    "🔔 Alerts: save a filter and get notified when a matching listing appears.\n\n"
    "Use the buttons below to get started."
)

HELP_TEXT = (
    "🔎 LORDS MOBILE ACCOUNT SEARCH\n"
    "Use /find <filters> or type a search in this private chat. Combine filters in one search.\n\n"
    "SEARCH EXAMPLES\n"
    "• /find K1700 5champ\n"
    "• /find K1500 K1700 5champ\n"
    "• /find rally lead K1500-2000 $1000-$2000\n"
    "• /find price under 1000\n"
    "• /find 30-50 cheapest\n"
    "• /find restricted 5 champs 120 blessed\n"
    "• /find 7d cheapest rally lead\n\n"
    "FILTERS\n"
    "Account type: trap, solo trap, rally trap, filler, rally lead, emperor, titan.\n"
    "Kingdom: K1700, K1500 K1700, bornk1900.\n"
    "Price: price:1000-5000, under 2000, ₹500-780, ¥680, £100, $500-$1500.\n"
    "Features: 1000% 1500%, 5fam, 5champ, blasts, skins, troops, t5.\n"
    "Other filters: restricted, open, cheapest, newest, expensive, 2h, 24h, 7d.\n"
    "Use strict: before keywords when every term must match.\n\n"
    "BUY AND NOTIFY\n"
    "🛒 Buy sends a request ticket to the private admin group. The listing card updates in place.\n"
    "🔔 Notify can save a matching alert and send a request ticket. Alerts are limited to specific filters.\n"
    "Start the bot before requesting a private reply. Your Telegram user ID is used for delivery.\n\n"
    "ALERTS\n"
    "/alertme <filters> — save an alert.\n"
    "/myalerts — view your active alerts.\n"
    "/cancelalert N — cancel alert N.\n"
    "/wishlist <filters> — alias for /alertme.\n\n"
    "LISTING STATUS AND OLD POSTS\n"
    "Reply to a listing in the source group with /sold to hide it from search and future alerts; use /relist to restore it.\n"
    "Reply to an old source post with /indexreply to import it. Bots cannot fetch arbitrary old group history by ID.\n"
    "In the admin group, reply exactly 'sold' to a listing-specific request ticket/copied media, or use Mark sold, to remove it from the local index. The original Telegram post stays in the group.\n\n"
    "ADMIN AND GROUP SETUP\n"
    "/adminpanel — open the admin controls.\n"
    "/add post — approved Admins and Merchants: upload 1–10 screenshots, then answer the guided account fields.\n"
    "Use /skip for optional fields and /back to correct the previous answer. Review the generated caption, use /editcaption to revise, /confirm to publish, or /cancel to discard.\n"
    "/delete — admins: reply to an account result from your /find search to remove it from the searchable database.\n"
    "/deleteall — Superadmin only; confirm to clear all searchable account listings after a verified backup.\n"
    "/setposttopic — choose the forum topic for new published listings; /setposttopic general sends to General.\n"
    "/setsource — run in a source group as its admin, or DM /setsource @channelusername for a channel.\n"
    "/destinations — view primary and additional publication destinations. Superadmin: /adddestination @username or chat ID; /removedestination @username or chat ID.\n"
    "/setadmin — run inside the separate private admin-only request group from your personal account.\n"
    "/settopicname Accounts 0-500 — label the current forum topic.\n"
    "/settopicname 123456 Accounts 0-500 — label by topic ID.\n"
    "/topicid — show the current/replied-to topic ID.\n"
    "/source — show the configured source chat; /unsetsource — disconnect it (Superadmin only in private chat).\n"
    "/index status — view index counts; /index export — export JSON; /index import — reply to a JSON export to merge it.\n"
    "/index delete — Superadmin only; confirms before clearing the local index.\n"
    "/audit — check group setup, bot membership, storage and alerts.\n"
    "/stats — view index, OCR and your alert count.\n\n"
    "ADMIN REPLIES AND PRICE\n"
    "Reply directly to a request ticket in the private admin group with text or media; the bot forwards it to the correct requester.\n"
    "Price detection checks the forum topic title first, then the written source caption/text. OCR is searchable but is not the official price. If no price is found, edit the original source caption or forum topic title; do not reply to the amount-review notice.\n\n"
    "LIMITATIONS\n"
    "Only new posts after setup and old posts explicitly imported with /indexreply are indexed. OCR can misread small text. Telegram does not notify bots when a post is deleted, so mark sold or remove it explicitly."
)

ALERT_PREFIX = "🔔 New listing matches your saved alert"
REQUEST_PREFIX = "📥 NEW ACCOUNT REQUEST"
BUY_BUTTON_TEXT = "🛒 Buy this account"
NOTIFY_BUTTON_TEXT = "🔔 Notify me when available"
ADMIN_REQUESTS_ENABLED = True
MAX_REQUEST_TEXT = 3500
MAX_ALERTS_PER_USER = 5
MAX_SEARCH_RESULTS = 5
MAX_INDEXED_MESSAGES_PER_LISTING = 100
ALBUM_QUIET_SECONDS = 3.0
MIN_RANKED_MATCH_RATIO = 0.34
ALERTS_REQUIRE_ALL_KEYWORDS = True

# Search aliases are normalized before matching. Keep aliases conservative so filters
# don't accidentally match unrelated words.
ALIASES = {
    "champ": "champion",
    "champs": "champion",
    "champions": "champion",
    "leaderskin": "leader skin",
    "leaderskins": "leader skin",
    "fam": "familiar",
    "fams": "familiar",
    "kingdom": "kingdom",
    "rallylead": "rally lead",
    "solotrap": "solo trap",
}

SORT_KEYWORDS = {
    "cheapest": "cheapest",
    "lowest": "cheapest",
    "lowprice": "cheapest",
    "lowestprice": "cheapest",
    "cheapestprice": "cheapest",
    "newest": "newest",
    "latest": "newest",
    "recent": "newest",
    "expensive": "expensive",
    "highestprice": "expensive",
    "highest": "highest",
}

# These words are structural hints, not requirements to find literally in each post.
STOP_WORDS = {
    "a", "an", "the", "and", "or", "with", "for", "to", "of", "in", "on",
    "account", "accounts", "please", "find", "show", "me", "want", "need", "filter", "sort", "by",
}
