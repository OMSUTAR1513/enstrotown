"""SQLite persistence for indexed source messages, listings, alerts and settings."""

import json
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("BOT_DB_PATH", str(Path(__file__).with_name("bot_data.sqlite3"))))


class ClosingConnection(sqlite3.Connection):
    """Connection context manager that commits/rolls back and always closes.

    sqlite3's built-in `with connection:` commits or rolls back but does not close
    the handle. This project uses `with connect()` in many functions, so closing
    here prevents file-handle leakage during long-running Telegram sessions.
    """
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id INTEGER PRIMARY KEY,
            role TEXT NOT NULL DEFAULT 'USER'
                CHECK(role IN ('ADMIN', 'MERCHANT', 'BUYER', 'USER')),
            username TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            updated_by INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_user_roles_role ON user_roles(role, updated_at);
        CREATE TABLE IF NOT EXISTS role_audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER NOT NULL,
            target_user_id INTEGER NOT NULL,
            old_role TEXT NOT NULL,
            new_role TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_role_audit_created ON role_audit_events(created_at);
        CREATE TABLE IF NOT EXISTS source_parts (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            listing_key TEXT NOT NULL,
            media_group_id TEXT NOT NULL DEFAULT '',
            message_text TEXT NOT NULL DEFAULT '',
            ocr_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            sender_id INTEGER NOT NULL DEFAULT 0,
            sender_name TEXT NOT NULL DEFAULT '',
            sender_username TEXT NOT NULL DEFAULT '',
            has_photo INTEGER NOT NULL DEFAULT 0,
            media_file_id TEXT NOT NULL DEFAULT '',
            media_type TEXT NOT NULL DEFAULT '',
            media_filename TEXT NOT NULL DEFAULT '',
            local_media_path TEXT NOT NULL DEFAULT '',
            caption_text TEXT NOT NULL DEFAULT '',
            topic_id INTEGER NOT NULL DEFAULT 0,
            topic_title TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(chat_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS ix_source_parts_listing_key ON source_parts(listing_key);
        CREATE TABLE IF NOT EXISTS source_listings (
            listing_key TEXT PRIMARY KEY,
            source_chat_id INTEGER NOT NULL,
            message_ids TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            caption_text TEXT NOT NULL DEFAULT '',
            ocr_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            link TEXT NOT NULL DEFAULT '',
            image_count INTEGER NOT NULL DEFAULT 0,
            topic_id INTEGER NOT NULL DEFAULT 0,
            topic_title TEXT NOT NULL DEFAULT '',
            sender_id INTEGER NOT NULL DEFAULT 0,
            sender_name TEXT NOT NULL DEFAULT '',
            sender_username TEXT NOT NULL DEFAULT '',
            is_available INTEGER NOT NULL DEFAULT 1,
            amount_min REAL,
            amount_max REAL,
            amount_source TEXT NOT NULL DEFAULT 'missing',
            amount_currency TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_source_listings_created ON source_listings(created_at);
        CREATE TABLE IF NOT EXISTS user_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_alerts_user_active ON user_alerts(user_id, active);
        CREATE TABLE IF NOT EXISTS bot_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            first_name TEXT NOT NULL DEFAULT '',
            request_type TEXT NOT NULL,
            requirement TEXT NOT NULL,
            listing_key TEXT NOT NULL DEFAULT '',
            admin_chat_id INTEGER NOT NULL,
            admin_message_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            replied_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_bot_requests_admin_message
          ON bot_requests(admin_chat_id, admin_message_id);
        CREATE TABLE IF NOT EXISTS request_reply_targets (
            admin_chat_id INTEGER NOT NULL,
            admin_message_id INTEGER NOT NULL,
            request_id INTEGER NOT NULL,
            PRIMARY KEY(admin_chat_id, admin_message_id)
        );
        CREATE INDEX IF NOT EXISTS ix_reply_targets_request ON request_reply_targets(request_id);
        CREATE TABLE IF NOT EXISTS callback_actions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            query TEXT NOT NULL DEFAULT '',
            listing_key TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS alert_deliveries (
            alert_id INTEGER NOT NULL,
            listing_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent',
            claimed_at TEXT NOT NULL DEFAULT '',
            delivered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_error TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(alert_id, listing_key)
        );
        CREATE TABLE IF NOT EXISTS amount_admin_notices (
            listing_key TEXT PRIMARY KEY,
            admin_message_id INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS admin_post_drafts (
            user_id INTEGER PRIMARY KEY,
            draft_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS publish_destinations (
            chat_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            chat_type TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            added_by INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS listing_search_refs (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            listing_key TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(chat_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS ix_listing_search_refs_key ON listing_search_refs(listing_key);
        """)
        # Safe additive migrations for users upgrading an existing bot_data.sqlite3.
        migrations = {
            "source_parts": {
                "topic_id": "INTEGER NOT NULL DEFAULT 0",
                "topic_title": "TEXT NOT NULL DEFAULT ''",
                "sender_username": "TEXT NOT NULL DEFAULT ''",
                "media_file_id": "TEXT NOT NULL DEFAULT ''",
                "media_type": "TEXT NOT NULL DEFAULT ''",
                "media_filename": "TEXT NOT NULL DEFAULT ''",
                "local_media_path": "TEXT NOT NULL DEFAULT ''",
                "caption_text": "TEXT NOT NULL DEFAULT ''",
            },
            "source_listings": {
                "topic_id": "INTEGER NOT NULL DEFAULT 0",
                "topic_title": "TEXT NOT NULL DEFAULT ''",
                "sender_id": "INTEGER NOT NULL DEFAULT 0",
                "sender_name": "TEXT NOT NULL DEFAULT ''",
                "sender_username": "TEXT NOT NULL DEFAULT ''",
                "is_available": "INTEGER NOT NULL DEFAULT 1",
                "amount_min": "REAL",
                "amount_max": "REAL",
                "amount_source": "TEXT NOT NULL DEFAULT 'missing'",
                "amount_currency": "TEXT NOT NULL DEFAULT ''",
            },
            "alert_deliveries": {
                "status": "TEXT NOT NULL DEFAULT 'sent'",
                "claimed_at": "TEXT NOT NULL DEFAULT ''",
                "last_error": "TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, columns in migrations.items():
            present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in present:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        # Recalculate legacy amounts at startup. Older versions could mistake OCR
        # fragments such as the in-game text "€6:26.9K" for a listing price.
        from amounts import extract_listing_amount, extract_listing_currency
        legacy_rows = conn.execute(
            "SELECT listing_key, topic_title, caption_text, ocr_text FROM source_listings"
        ).fetchall()
        for listing in legacy_rows:
            low, high, source = extract_listing_amount(
                listing["topic_title"], listing["caption_text"], listing["ocr_text"]
            )
            currency = extract_listing_currency(
                listing["topic_title"], listing["caption_text"], listing["ocr_text"], source=source
            )
            conn.execute(
                "UPDATE source_listings SET amount_min=?, amount_max=?, amount_source=?, amount_currency=? WHERE listing_key=?",
                (low, high, source, currency, listing["listing_key"]),
            )


def set_setting(key, value):
    with connect() as conn:
        conn.execute(
            "INSERT INTO bot_settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def get_setting(key, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def delete_setting(key):
    with connect() as conn:
        conn.execute("DELETE FROM bot_settings WHERE key=?", (key,))


def make_listing_key(chat_id: int, message_id: int, media_group_id=None, existing_key=None):
    if existing_key:
        return existing_key
    if media_group_id:
        return f"album:{chat_id}:{media_group_id}"
    return f"message:{chat_id}:{message_id}"


def upsert_source_part(*, chat_id, message_id, media_group_id, message_text, ocr_text,
                       created_at, sender_id=0, sender_name="", sender_username="", has_photo=False,
                       media_file_id="", media_type="", media_filename="", local_media_path="",
                       caption_text="", topic_id=0, topic_title=""):
    from amounts import extract_listing_amount, extract_listing_currency
    """Save/update one Telegram message, then rebuild its album/listing search record."""
    with connect() as conn:
        old = conn.execute(
            "SELECT listing_key FROM source_parts WHERE chat_id=? AND message_id=?",
            (int(chat_id), int(message_id)),
        ).fetchone()
        listing_key = make_listing_key(chat_id, message_id, media_group_id,
                                       old["listing_key"] if old else None)
        conn.execute("""
            INSERT INTO source_parts
            (chat_id, message_id, listing_key, media_group_id, message_text, ocr_text,
             created_at, sender_id, sender_name, sender_username, has_photo, media_file_id, media_type,
             media_filename, local_media_path, caption_text, topic_id, topic_title)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
              listing_key=excluded.listing_key,
              media_group_id=excluded.media_group_id,
              message_text=excluded.message_text,
              ocr_text=excluded.ocr_text,
              created_at=excluded.created_at,
              sender_id=excluded.sender_id,
              sender_name=excluded.sender_name,
              sender_username=excluded.sender_username,
              has_photo=excluded.has_photo,
              media_file_id=excluded.media_file_id,
              media_type=excluded.media_type,
              media_filename=excluded.media_filename,
              local_media_path=excluded.local_media_path,
              caption_text=excluded.caption_text,
              topic_id=excluded.topic_id,
              topic_title=excluded.topic_title
        """, (int(chat_id), int(message_id), listing_key, media_group_id or "",
              message_text or "", ocr_text or "", created_at, int(sender_id or 0),
              sender_name or "", sender_username or "", 1 if has_photo else 0,
              media_file_id or "", media_type or "", media_filename or "", local_media_path or "",
              caption_text or "", int(topic_id or 0), topic_title or ""))

        rows = conn.execute(
            "SELECT * FROM source_parts WHERE listing_key=? ORDER BY message_id ASC",
            (listing_key,),
        ).fetchall()
        ids = [int(row["message_id"]) for row in rows]
        ids = sorted(set(ids))[:100]
        messages, ocrs, captions = [], [], []
        topic_titles = []
        topic_ids = []
        sender_id = 0
        sender_name = ""
        sender_username = ""
        for row in rows:
            if not sender_id and int(row["sender_id"] or 0):
                sender_id = int(row["sender_id"] or 0)
            if not sender_name and row["sender_name"]:
                sender_name = row["sender_name"]
            if not sender_username and row["sender_username"]:
                sender_username = row["sender_username"]
            if row["topic_title"] and row["topic_title"] not in topic_titles:
                topic_titles.append(row["topic_title"])
            if row["topic_id"] and int(row["topic_id"]) not in topic_ids:
                topic_ids.append(int(row["topic_id"]))
            text = (row["message_text"] or "").strip()
            caption = (row["caption_text"] or "").strip()
            ocr = (row["ocr_text"] or "").strip()
            if text and text not in messages:
                messages.append(text)
            if ocr and ocr not in ocrs:
                ocrs.append(ocr)
            if caption and caption not in captions:
                captions.append(caption)
        if not captions:
            captions = list(messages)
        caption_text = "\n\n".join(captions)
        ocr_text = "\n\n".join(ocrs)
        topic_title = " | ".join(topic_titles)
        body_parts = []
        if topic_title:
            body_parts.append("[TOPIC] " + topic_title)
        if caption_text:
            body_parts.append(caption_text)
        if ocr_text:
            body_parts.append("[TEXT READ FROM PHOTO]\n" + ocr_text)
        body = "\n\n".join(body_parts).strip()
        title = choose_title(captions, ocrs, body)
        amount_min, amount_max, amount_source = extract_listing_amount(topic_title, caption_text, ocr_text)
        amount_currency = extract_listing_currency(topic_title, caption_text, ocr_text, source=amount_source)
        created = min((r["created_at"] for r in rows), default=created_at)
        source_chat_id = int(chat_id)
        link = make_message_link(source_chat_id, ids[0] if ids else int(message_id))
        image_count = sum(int(row["has_photo"]) for row in rows)
        conn.execute("""
            INSERT INTO source_listings
            (listing_key, source_chat_id, message_ids, title, body, caption_text,
             ocr_text, created_at, updated_at, link, image_count, topic_id, topic_title,
             sender_id, sender_name, sender_username, amount_min, amount_max, amount_source, amount_currency)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(listing_key) DO UPDATE SET
              source_chat_id=excluded.source_chat_id,
              message_ids=excluded.message_ids,
              title=excluded.title,
              body=excluded.body,
              caption_text=excluded.caption_text,
              ocr_text=excluded.ocr_text,
              updated_at=CURRENT_TIMESTAMP,
              link=excluded.link,
              image_count=excluded.image_count,
              topic_id=excluded.topic_id,
              topic_title=excluded.topic_title,
              sender_id=excluded.sender_id,
              sender_name=excluded.sender_name,
              sender_username=excluded.sender_username,
              amount_min=excluded.amount_min,
              amount_max=excluded.amount_max,
              amount_source=excluded.amount_source,
              amount_currency=excluded.amount_currency
        """, (listing_key, source_chat_id, json.dumps(ids), title, body, caption_text,
              ocr_text, created, link, image_count, topic_ids[0] if topic_ids else 0,
              topic_title, sender_id, sender_name, sender_username,
              amount_min, amount_max, amount_source, amount_currency))
    return listing_key


def choose_title(captions, ocrs, body):
    # Prefer the first caption/title line; fall back to the first useful OCR line.
    for text in captions:
        for line in text.splitlines():
            line = " ".join(line.split()).strip(" -|•\t")
            if line and not line.startswith("/"):
                return line[:180]
    for text in ocrs:
        for line in text.splitlines():
            line = " ".join(line.split()).strip(" -|•\t")
            if len(line) >= 4:
                return line[:180]
    line = " ".join((body or "").split())
    return (line[:177] + "...") if len(line) > 180 else line or "Photo listing"


def make_message_link(chat_id: int, message_id: int) -> str:
    # Private supergroups use /c/<internal-id>/<message-id>. Membership is still required.
    value = str(chat_id)
    if value.startswith("-100"):
        return f"https://t.me/c/{value[4:]}/{message_id}"
    return ""


def get_listings():
    # Sold/removed listings stay in the database for audit/history but never appear in search.
    with connect() as conn:
        rows = conn.execute("SELECT * FROM source_listings WHERE is_available=1 ORDER BY created_at DESC").fetchall()
    output = []
    for row in rows:
        item = dict(row)
        try:
            item["message_ids"] = json.loads(item["message_ids"])
        except (ValueError, TypeError):
            item["message_ids"] = []
        output.append(item)
    return output


def get_listing(listing_key):
    with connect() as conn:
        row = conn.execute("SELECT * FROM source_listings WHERE listing_key=?", (listing_key,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["message_ids"] = json.loads(item["message_ids"] or "[]")
    return item


def count_listings():
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM source_listings WHERE is_available=1").fetchone()[0])


def count_all_listings():
    """Count active and sold/archived listing records in the local index."""
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM source_listings").fetchone()[0])


def count_source_parts():
    """Count individual indexed Telegram messages, including album parts."""
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM source_parts").fetchone()[0])


def export_index_snapshot():
    """Return listing/message index data only; never export customer tickets or tokens."""
    with connect() as conn:
        listings = [dict(row) for row in conn.execute(
            "SELECT * FROM source_listings ORDER BY created_at DESC"
        ).fetchall()]
        parts = [dict(row) for row in conn.execute(
            "SELECT * FROM source_parts ORDER BY created_at DESC, message_id DESC"
        ).fetchall()]
    for item in listings:
        try:
            item["message_ids"] = json.loads(item.get("message_ids") or "[]")
        except (ValueError, TypeError):
            item["message_ids"] = []
        item["is_available"] = bool(item.get("is_available", 1))
    for part in parts:
        part["has_photo"] = bool(part.get("has_photo", 0))
    return {"listings": listings, "source_parts": parts}



def mark_amount_admin_notice(listing_key, admin_message_id):
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO amount_admin_notices(listing_key, admin_message_id) VALUES(?, ?)",
            (str(listing_key), int(admin_message_id or 0)),
        )


def has_amount_admin_notice(listing_key):
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM amount_admin_notices WHERE listing_key=?", (str(listing_key),)
        ).fetchone()
        return bool(row)


def clear_operational_data():
    """Reset indexed listings, alerts, tickets and transient actions while keeping setup settings."""
    with connect() as conn:
        counts = {
            "listings": int(conn.execute("SELECT COUNT(*) FROM source_listings").fetchone()[0]),
            "messages": int(conn.execute("SELECT COUNT(*) FROM source_parts").fetchone()[0]),
            "alerts": int(conn.execute("SELECT COUNT(*) FROM user_alerts").fetchone()[0]),
            "requests": int(conn.execute("SELECT COUNT(*) FROM bot_requests").fetchone()[0]),
            "post_drafts": int(conn.execute("SELECT COUNT(*) FROM admin_post_drafts").fetchone()[0]),
        }
        conn.execute("DELETE FROM alert_deliveries")
        conn.execute("DELETE FROM amount_admin_notices")
        conn.execute("DELETE FROM callback_actions")
        conn.execute("DELETE FROM request_reply_targets")
        conn.execute("DELETE FROM bot_requests")
        conn.execute("DELETE FROM admin_post_drafts")
        conn.execute("DELETE FROM user_alerts")
        conn.execute("DELETE FROM source_parts")
        conn.execute("DELETE FROM source_listings")
        conn.execute("DELETE FROM listing_search_refs")
    return counts


def delete_listing_by_key(listing_key):
    """Permanently remove one listing and its message-index parts from local storage."""
    with connect() as conn:
        row = conn.execute(
            "SELECT message_ids FROM source_listings WHERE listing_key=?", (str(listing_key),)
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM source_parts WHERE listing_key=?", (str(listing_key),))
        conn.execute("DELETE FROM source_listings WHERE listing_key=?", (str(listing_key),))
        conn.execute("DELETE FROM alert_deliveries WHERE listing_key=?", (str(listing_key),))
        conn.execute("DELETE FROM callback_actions WHERE listing_key=?", (str(listing_key),))
        conn.execute("DELETE FROM amount_admin_notices WHERE listing_key=?", (str(listing_key),))
        conn.execute("DELETE FROM listing_search_refs WHERE listing_key=?", (str(listing_key),))
        # Keep historical request tickets, but detach them from the deleted listing.
        conn.execute("UPDATE bot_requests SET listing_key='' WHERE listing_key=?", (str(listing_key),))
    return True


def add_listing_search_reference(chat_id, message_id, listing_key):
    """Map a bot-delivered admin search result back to its indexed listing."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO listing_search_refs(chat_id, message_id, listing_key) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, message_id) DO UPDATE SET listing_key=excluded.listing_key, created_at=CURRENT_TIMESTAMP",
            (int(chat_id), int(message_id), str(listing_key)),
        )


def get_listing_key_by_search_reference(chat_id, message_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT listing_key FROM listing_search_refs WHERE chat_id=? AND message_id=?",
            (int(chat_id), int(message_id)),
        ).fetchone()
    return row["listing_key"] if row else None


def clear_index():
    """Clear only listing-index data; preserve settings, saved alerts and request tickets."""
    with connect() as conn:
        listing_count = int(conn.execute("SELECT COUNT(*) FROM source_listings").fetchone()[0])
        part_count = int(conn.execute("SELECT COUNT(*) FROM source_parts").fetchone()[0])
        # Preserve historical tickets while severing references to listings that are
        # about to be removed. This keeps later request views from pointing at ghosts.
        conn.execute(
            "UPDATE bot_requests SET listing_key='' "
            "WHERE listing_key IN (SELECT listing_key FROM source_listings)"
        )
        conn.execute("DELETE FROM source_parts")
        conn.execute("DELETE FROM source_listings")
        conn.execute("DELETE FROM alert_deliveries")
        conn.execute("DELETE FROM callback_actions")
        conn.execute("DELETE FROM amount_admin_notices")
        conn.execute("DELETE FROM listing_search_refs")
    return {"listings": listing_count, "messages": part_count}


def add_alert(user_id, query):
    with connect() as conn:
        cur = conn.execute("INSERT INTO user_alerts(user_id, query) VALUES (?, ?)",
                           (int(user_id), query))
        return int(cur.lastrowid)


def get_active_alerts():
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM user_alerts WHERE active=1 ORDER BY id")]


def get_user_alerts(user_id):
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM user_alerts WHERE user_id=? AND active=1 ORDER BY id", (int(user_id),))]


def count_user_alerts(user_id):
    with connect() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM user_alerts WHERE user_id=? AND active=1", (int(user_id),)
        ).fetchone()[0])


def delete_alert_by_position(user_id, position):
    alerts = get_user_alerts(user_id)
    if position < 1 or position > len(alerts):
        return False
    with connect() as conn:
        conn.execute("UPDATE user_alerts SET active=0 WHERE id=? AND user_id=?",
                     (alerts[position - 1]["id"], int(user_id)))
    return True


def claim_alert_delivery(alert_id, listing_key, *, stale_after_minutes=10):
    """Atomically reserve an alert/listing pair before sending its Telegram DM.

    A previously sent or currently claimed pair is skipped. A failed claim, or a
    stale in-progress claim left by a crashed process, can be retried. Telegram
    does not provide a sendMessage idempotency key, so a crash after Telegram
    accepted a send but before SQLite records success remains an at-least-once
    edge case; normal concurrent/repeated update paths are de-duplicated here.
    """
    alert_id = int(alert_id)
    listing_key = str(listing_key)
    stale_after_minutes = max(1, min(int(stale_after_minutes), 1440))
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, claimed_at FROM alert_deliveries WHERE alert_id=? AND listing_key=?",
            (alert_id, listing_key),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO alert_deliveries(alert_id, listing_key, status, claimed_at, delivered_at, last_error) "
                "VALUES(?, ?, 'sending', CURRENT_TIMESTAMP, '', '')",
                (alert_id, listing_key),
            )
            return True
        status = str(row["status"] or "sent").lower()
        if status == "failed":
            conn.execute(
                "UPDATE alert_deliveries SET status='sending', claimed_at=CURRENT_TIMESTAMP, last_error='' "
                "WHERE alert_id=? AND listing_key=?",
                (alert_id, listing_key),
            )
            return True
        if status == "sending":
            stale = conn.execute(
                "SELECT claimed_at < datetime('now', ?) FROM alert_deliveries "
                "WHERE alert_id=? AND listing_key=?",
                (f"-{stale_after_minutes} minutes", alert_id, listing_key),
            ).fetchone()[0]
            if stale:
                conn.execute(
                    "UPDATE alert_deliveries SET status='sending', claimed_at=CURRENT_TIMESTAMP, last_error='' "
                    "WHERE alert_id=? AND listing_key=?",
                    (alert_id, listing_key),
                )
                return True
        return False


def mark_alert_delivered(alert_id, listing_key):
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE alert_deliveries SET status='sent', delivered_at=CURRENT_TIMESTAMP, last_error='' "
            "WHERE alert_id=? AND listing_key=? AND status='sending'",
            (int(alert_id), str(listing_key)),
        )
        if cursor.rowcount:
            return True
        try:
            conn.execute(
                "INSERT INTO alert_deliveries(alert_id, listing_key, status, delivered_at) "
                "VALUES(?, ?, 'sent', CURRENT_TIMESTAMP)",
                (int(alert_id), str(listing_key)),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def mark_alert_delivery_failed(alert_id, listing_key, error=""):
    with connect() as conn:
        conn.execute(
            "UPDATE alert_deliveries SET status='failed', last_error=? "
            "WHERE alert_id=? AND listing_key=? AND status='sending'",
            (str(error or "")[:500], int(alert_id), str(listing_key)),
        )


def count_ocr_listings():
    with connect() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM source_listings WHERE TRIM(ocr_text) != ''"
        ).fetchone()[0])


# Request-routing records keep Telegram user IDs server-side; the admin request message
# itself contains the mandatory username and readable requirement.
def add_callback_action(token, user_id, action_type, query="", listing_key=""):
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO callback_actions(token,user_id,action_type,query,listing_key) VALUES(?,?,?,?,?)",
                     (token, int(user_id), action_type, query or "", listing_key or ""))


def get_callback_action(token):
    with connect() as conn:
        row = conn.execute("SELECT * FROM callback_actions WHERE token=?", (token,)).fetchone()
    return dict(row) if row else None


def delete_callback_action(token):
    with connect() as conn:
        conn.execute("DELETE FROM callback_actions WHERE token=?", (token,))


def consume_callback_action(token, user_id):
    """Atomically consume a one-use action token for its owner only.

    The immediate transaction prevents two rapid taps from creating duplicate
    Buy/Notify requests. Returns (action, status), where status is ok, missing,
    or forbidden. No schema migration is required.
    """
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM callback_actions WHERE token=?", (str(token),)
        ).fetchone()
        if row is None:
            conn.commit()
            return None, "missing"
        action = dict(row)
        if int(action["user_id"]) != int(user_id):
            conn.commit()
            return None, "forbidden"
        conn.execute("DELETE FROM callback_actions WHERE token=?", (str(token),))
        conn.commit()
        return action, "ok"


def add_bot_request(*, user_id, username, first_name, request_type, requirement,
                    listing_key, admin_chat_id, admin_message_id):
    with connect() as conn:
        cur = conn.execute("""INSERT INTO bot_requests
            (user_id, username, first_name, request_type, requirement, listing_key,
             admin_chat_id, admin_message_id) VALUES(?,?,?,?,?,?,?,?)""",
            (int(user_id), username, first_name or "", request_type, requirement,
             listing_key or "", int(admin_chat_id), int(admin_message_id)))
        request_id = int(cur.lastrowid)
        conn.execute(
            "INSERT OR REPLACE INTO request_reply_targets(admin_chat_id,admin_message_id,request_id) VALUES(?,?,?)",
            (int(admin_chat_id), int(admin_message_id), request_id),
        )
        return request_id


def add_request_reply_target(request_id, chat_id, message_id):
    """Map copied source media to the same request, so a reply to the ticket OR media routes correctly."""
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO request_reply_targets(admin_chat_id,admin_message_id,request_id) VALUES(?,?,?)",
            (int(chat_id), int(message_id), int(request_id)),
        )


def get_request_by_admin_message(chat_id, message_id):
    with connect() as conn:
        row = conn.execute("""SELECT br.* FROM request_reply_targets rt
            JOIN bot_requests br ON br.id=rt.request_id
            WHERE rt.admin_chat_id=? AND rt.admin_message_id=? ORDER BY br.id DESC LIMIT 1""",
            (int(chat_id), int(message_id))).fetchone()
        if not row:
            # Backward compatibility for request rows created before the routing map existed.
            row = conn.execute("""SELECT * FROM bot_requests
                WHERE admin_chat_id=? AND admin_message_id=? ORDER BY id DESC LIMIT 1""",
                (int(chat_id), int(message_id))).fetchone()
    return dict(row) if row else None


def mark_request_replied(request_id):
    with connect() as conn:
        conn.execute("UPDATE bot_requests SET status='replied', replied_at=CURRENT_TIMESTAMP WHERE id=?",
                     (int(request_id),))


def get_topic_title(topic_id):
    with connect() as conn:
        row = conn.execute("SELECT value FROM bot_settings WHERE key=?", (f"topic_title:{int(topic_id)}",)).fetchone()
    return row["value"] if row else ""


def set_topic_title(topic_id, title):
    """Remember a topic label and rebuild already-indexed listings in that topic."""
    topic_id = int(topic_id)
    set_setting(f"topic_title:{topic_id}", title)
    with connect() as conn:
        rows = conn.execute("SELECT * FROM source_parts WHERE topic_id=? ORDER BY message_id", (topic_id,)).fetchall()
        saved = [dict(row) for row in rows]
        if saved:
            conn.execute("UPDATE source_parts SET topic_title=? WHERE topic_id=?", (title, topic_id))
    # Upsert each message outside the DB connection so its album/listing record is rebuilt.
    for row in saved:
        upsert_source_part(
            chat_id=row["chat_id"], message_id=row["message_id"],
            media_group_id=row["media_group_id"], message_text=row["message_text"],
            ocr_text=row["ocr_text"], created_at=row["created_at"],
            sender_id=row["sender_id"], sender_name=row["sender_name"],
            sender_username=row["sender_username"],
            has_photo=bool(row["has_photo"]),
            media_file_id=row["media_file_id"], media_type=row["media_type"],
            media_filename=row["media_filename"], local_media_path=row["local_media_path"],
            caption_text=row["caption_text"], topic_id=topic_id, topic_title=title,
        )


def get_listing_key_by_source_message(chat_id, message_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT listing_key FROM source_parts WHERE chat_id=? AND message_id=?",
            (int(chat_id), int(message_id)),
        ).fetchone()
    return row["listing_key"] if row else None


def set_listing_availability_by_source_message(chat_id, message_id, available):
    key = get_listing_key_by_source_message(chat_id, message_id)
    if not key:
        return None
    with connect() as conn:
        conn.execute("UPDATE source_listings SET is_available=? WHERE listing_key=?",
                     (1 if available else 0, key))
        row = conn.execute("SELECT * FROM source_listings WHERE listing_key=?", (key,)).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["message_ids"] = json.loads(item.get("message_ids") or "[]")
    except (ValueError, TypeError):
        item["message_ids"] = []
    return item


def get_request_by_id(request_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM bot_requests WHERE id=?", (int(request_id),)).fetchone()
    return dict(row) if row else None


def set_listing_availability_by_key(listing_key, available):
    with connect() as conn:
        conn.execute("UPDATE source_listings SET is_available=? WHERE listing_key=?",
                     (1 if available else 0, str(listing_key)))
        row = conn.execute("SELECT * FROM source_listings WHERE listing_key=?", (str(listing_key),)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["message_ids"] = json.loads(item.get("message_ids") or "[]")
    return item


def get_latest_listing():
    with connect() as conn:
        row = conn.execute("SELECT * FROM source_listings WHERE is_available=1 ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["message_ids"] = json.loads(item.get("message_ids") or "[]")
    except (ValueError, TypeError):
        item["message_ids"] = []
    return item


def save_admin_post_draft(user_id, draft):
    """Persist an admin's in-progress/published listing draft safely as JSON."""
    payload = json.dumps(draft or {}, ensure_ascii=False, separators=(",", ":"))
    with connect() as conn:
        conn.execute(
            "INSERT INTO admin_post_drafts(user_id, draft_json, updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id) DO UPDATE SET draft_json=excluded.draft_json, updated_at=CURRENT_TIMESTAMP",
            (int(user_id), payload),
        )


def get_admin_post_draft(user_id):
    """Load an admin post draft, returning None when no draft exists or JSON is invalid."""
    with connect() as conn:
        row = conn.execute(
            "SELECT draft_json FROM admin_post_drafts WHERE user_id=?", (int(user_id),)
        ).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row["draft_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def delete_admin_post_draft(user_id):
    with connect() as conn:
        conn.execute("DELETE FROM admin_post_drafts WHERE user_id=?", (int(user_id),))


def add_publish_destination(chat_id, title="", chat_type="", username="", added_by=0):
    """Add or refresh one separately managed account-publication destination."""
    with connect() as conn:
        conn.execute(
            """INSERT INTO publish_destinations(chat_id, title, chat_type, username, added_by)
               VALUES(?,?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 title=excluded.title, chat_type=excluded.chat_type,
                 username=excluded.username, added_by=excluded.added_by""",
            (int(chat_id), str(title or ""), str(chat_type or ""),
             str(username or "").lstrip("@"), int(added_by or 0)),
        )


def remove_publish_destination(chat_id):
    with connect() as conn:
        cur = conn.execute("DELETE FROM publish_destinations WHERE chat_id=?", (int(chat_id),))
        return cur.rowcount > 0


def get_publish_destination(chat_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM publish_destinations WHERE chat_id=?", (int(chat_id),)
        ).fetchone()
    return dict(row) if row else None


def list_publish_destinations():
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM publish_destinations ORDER BY title COLLATE NOCASE, chat_id"
        ).fetchall()
    return [dict(row) for row in rows]


# Application roles are deliberately separate from Telegram group-admin status.
# SUPERADMIN is not assignable through this table; it is an immutable configured ID.
MANAGEABLE_USER_ROLES = {"ADMIN", "MERCHANT", "BUYER", "USER"}


def upsert_user_profile(user_id, username="", display_name=""):
    """Remember a Telegram user's basic public profile without changing their role."""
    user_id = int(user_id)
    username = str(username or "").strip().lstrip("@")
    display_name = str(display_name or "").strip()
    with connect() as conn:
        conn.execute(
            """INSERT INTO user_roles(user_id, role, username, display_name, updated_by)
               VALUES(?, 'USER', ?, ?, 0)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=CASE WHEN excluded.username<>'' THEN excluded.username ELSE user_roles.username END,
                 display_name=CASE WHEN excluded.display_name<>'' THEN excluded.display_name ELSE user_roles.display_name END""",
            (user_id, username, display_name),
        )


def get_user_role(user_id):
    """Return the app-level role, defaulting safely to USER."""
    with connect() as conn:
        row = conn.execute(
            "SELECT role FROM user_roles WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
    return str(row["role"]).upper() if row else "USER"


def get_user_role_record(user_id):
    """Return role/profile data for a target ID, including a safe default record."""
    with connect() as conn:
        row = conn.execute(
            "SELECT user_id, role, username, display_name, updated_by, created_at, updated_at "
            "FROM user_roles WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
    if row:
        return dict(row)
    return {
        "user_id": int(user_id), "role": "USER", "username": "",
        "display_name": "", "updated_by": 0, "created_at": "",
        "updated_at": "",
    }


def set_user_role(user_id, role, actor_user_id, *, protected_user_id=None):
    """Atomically change a role and append an immutable audit event.

    Returns (old_role, new_role, changed). SUPERADMIN is not a valid stored role.
    """
    user_id = int(user_id)
    actor_user_id = int(actor_user_id)
    role = str(role or "").strip().upper()
    if role not in MANAGEABLE_USER_ROLES:
        raise ValueError("Role must be ADMIN, MERCHANT, BUYER, or USER.")
    if protected_user_id is not None and user_id == int(protected_user_id):
        raise ValueError("The configured Superadmin account is protected.")
    with connect() as conn:
        row = conn.execute(
            "SELECT role FROM user_roles WHERE user_id=?",
            (user_id,),
        ).fetchone()
        old_role = str(row["role"]).upper() if row else "USER"
        if old_role == role:
            return old_role, role, False
        conn.execute(
            """INSERT INTO user_roles(user_id, role, updated_by)
               VALUES(?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 role=excluded.role,
                 updated_by=excluded.updated_by,
                 updated_at=CURRENT_TIMESTAMP""",
            (user_id, role, actor_user_id),
        )
        conn.execute(
            """INSERT INTO role_audit_events
               (actor_user_id, target_user_id, old_role, new_role)
               VALUES(?, ?, ?, ?)""",
            (actor_user_id, user_id, old_role, role),
        )
    return old_role, role, True


def list_user_roles(limit=8, offset=0):
    """List explicitly assigned non-default roles for the Superadmin panel."""
    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))
    with connect() as conn:
        rows = conn.execute(
            """SELECT user_id, role, username, display_name, updated_at
               FROM user_roles
               WHERE role <> 'USER'
               ORDER BY CASE role WHEN 'ADMIN' THEN 0 WHEN 'MERCHANT' THEN 1 ELSE 2 END,
                        updated_at DESC, user_id ASC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM user_roles WHERE role <> 'USER'"
        ).fetchone()[0]
    return [dict(row) for row in rows], int(total)


def list_role_audit_events(limit=8, offset=0):
    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))
    with connect() as conn:
        rows = conn.execute(
            """SELECT actor_user_id, target_user_id, old_role, new_role, created_at
               FROM role_audit_events
               ORDER BY id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM role_audit_events").fetchone()[0]
    return [dict(row) for row in rows], int(total)
