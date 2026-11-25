"""Telegram listing finder for a private group, with image OCR and saved alerts."""

import asyncio
import json
import logging
import os
import re
import shutil
import zipfile
import mimetypes
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from types import SimpleNamespace
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ChatType
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

import config
import database
from search_engine import evaluate_listing, parse_query, search_listings
from time_utils import format_created_at
from source_chat_utils import normalize_source_chat_reference, source_chat_matches, source_sender_details

try:
    from PIL import Image, ImageEnhance, ImageOps
except Exception:  # OCR is optional; text/caption indexing still works.
    Image = ImageEnhance = ImageOps = None

try:
    import pytesseract
except Exception:
    pytesseract = None

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("listing-finder")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def source_chat_id():
    value = database.get_setting("source_chat_id")
    if value:
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    if config.SOURCE_CHAT_ID is not None:
        return int(config.SOURCE_CHAT_ID)
    return None


def is_private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == ChatType.PRIVATE)


def admin_chat_id():
    value = database.get_setting("admin_chat_id")
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def owner_username():
    value = database.get_setting("owner_username", "") or getattr(config, "OWNER_USERNAME", "")
    return str(value or "").strip().lstrip("@")


def owner_display_name():
    return str(database.get_setting("owner_name", "") or "").strip()


def configured_source_title():
    return str(database.get_setting("source_chat_title", "") or "").strip()


def source_group_link():
    # An explicitly saved empty value means that a newly configured private group
    # has not yet supplied a link; do not accidentally show a link to the old group.
    saved = database.get_setting("source_group_invite_link", None)
    value = saved if saved is not None else getattr(config, "SOURCE_GROUP_INVITE_LINK", "")
    return safe_telegram_url(value)


def latest_listing():
    try:
        return database.get_latest_listing()
    except Exception:
        return None


def safe_telegram_url(value):
    """Accept only well-formed HTTPS Telegram links for inline URL buttons."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048 or any(ch.isspace() for ch in raw):
        return ""
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme != "https" or host not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
        return ""
    if parts.username or parts.password or port not in (None, 443) or parts.query or parts.fragment:
        return ""
    path = parts.path or ""
    allowed = (
        re.fullmatch(r"/\+[A-Za-z0-9_-]{5,128}", path)
        or re.fullmatch(r"/joinchat/[A-Za-z0-9_-]{5,128}", path)
        or re.fullmatch(r"/[A-Za-z0-9_]{5,32}", path)
        or re.fullmatch(r"/[A-Za-z0-9_]{5,32}/[0-9]+", path)
        or re.fullmatch(r"/c/[0-9]+/[0-9]+", path)
    )
    return raw if allowed else ""


def home_navigation_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Back to Home", callback_data="home:root")
    ]])


def welcome_screen_text():
    text = config.WELCOME_TEXT.format(bot_name=config.BOT_DISPLAY_NAME)
    link = source_group_link()
    owner = owner_username()
    source_title = configured_source_title()
    if link:
        text += f"\n\n📂 Listing group{f' ({source_title})' if source_title else ''}: {link}"
    latest = latest_listing()
    if latest and (latest.get("sender_username") or latest.get("sender_name")):
        latest_owner = f"@{latest['sender_username']}" if latest.get("sender_username") else latest.get("sender_name")
        text += f"\n📦 Latest listing owner: {latest_owner}"
    display_name = owner_display_name()
    owner_text = (f"{display_name} (@{owner})" if display_name and owner else
                  (f"@{owner}" if owner else display_name))
    text += f"\n👤 Owner / help desk: {owner_text}" if owner_text else "\n👤 Owner / help desk: not configured yet"
    return text


def help_screen_text():
    """Build Help with optional setup details while staying inside Telegram's text limit."""
    text = str(config.HELP_TEXT or "Help is not configured yet.")
    link = source_group_link()
    owner = owner_username()
    source_title = configured_source_title()
    if link:
        text += f"\n\n📂 Listing group{f' ({source_title[:80]})' if source_title else ''}: {link}"
    latest = latest_listing()
    if latest and (latest.get("sender_username") or latest.get("sender_name")):
        latest_owner = (f"@{latest['sender_username']}" if latest.get("sender_username")
                        else str(latest.get("sender_name") or "")[:80])
        if latest_owner:
            text += f"\n📦 Latest listing owner: {latest_owner}"
    display_name = owner_display_name()[:80]
    owner_text = (f"{display_name} (@{owner})" if display_name and owner else
                  (f"@{owner}" if owner else display_name))
    if owner_text:
        text += f"\n👤 Owner / help desk: {owner_text}"
    if len(text) <= 4096:
        return text
    # Keep the main help content and end cleanly rather than returning text that
    # Telegram will reject because an optional group title/link made it too long.
    clipped = text[:4093].rsplit("\n", 1)[0].rstrip()
    return clipped + "…"


def homepage_keyboard():
    rows = []
    listing_link = safe_telegram_url(source_group_link())
    source_title = configured_source_title()
    latest = latest_listing()
    latest_link = safe_telegram_url(latest.get("link")) if latest else ""
    owner = owner_username()
    if listing_link:
        label = f"📂 Browse {source_title}" if source_title else "📂 Browse listings"
        rows.append([InlineKeyboardButton(label[:60], url=listing_link)])
    if latest and latest_link:
        title = " ".join((latest.get("title") or "Latest account listing").split())[:34]
        rows.append([InlineKeyboardButton(f"🆕 Latest: {title}", url=latest_link)])
    rows.append([
        InlineKeyboardButton("🔎 Search", callback_data="home:search"),
        InlineKeyboardButton("💰 Filter by amount", callback_data="home:amount"),
    ])
    rows.append([
        InlineKeyboardButton("🔔 My alerts", callback_data="home:alerts"),
        InlineKeyboardButton("📖 Help", callback_data="home:help"),
    ])
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", owner):
        rows.append([InlineKeyboardButton("👤 Contact owner / help desk", url=f"https://t.me/{owner}")])
    return InlineKeyboardMarkup(rows)


async def answer_callback_safely(query_obj, text=None, *, show_alert=False):
    """A stale callback acknowledgement must never prevent the screen update."""
    try:
        if text:
            await query_obj.answer(text=text, show_alert=show_alert)
        else:
            await query_obj.answer()
        return True
    except TelegramError as exc:
        log.info("Callback acknowledgement failed for %r: %s", getattr(query_obj, "data", ""), exc)
        return False


def compact_help_caption():
    """Return a useful Help summary that fits Telegram's 1,024-char media-caption limit."""
    return (
        "📖 HELP — ACCOUNT FINDER\n\n"
        "🔎 SEARCH\n"
        "Use /find followed by keywords and filters. Example: /find K1700 5champ\n\n"
        "💰 AMOUNT\n"
        "Use /filter amount 500-2000, /filter amount under 1000, or /filter amount over 500.\n\n"
        "🔔 ALERTS\n"
        "Create: /alertme K1700 5champ\n"
        "View: /myalerts\n"
        "Cancel: /cancelalert 1\n\n"
        "🛒 BUY / NOTIFY\n"
        "Use the buttons on a listing card. The bot sends the request to its private admin group.\n\n"
        "Admins: /adminpanel. Approved Merchants: /add post. Group setup: /setsource in the primary listing group; use /destinations to view publication targets."
    )


def _callback_screen_payload(query_obj, text):
    """Select a Telegram-compatible edit method and enforce its text/caption limit."""
    message = getattr(query_obj, "message", None)
    if _callback_message_is_captionable(message):
        content = str(text or " ")
        if len(content) > 1024:
            content = compact_help_caption() if content.startswith(str(config.HELP_TEXT or "")) else content[:1023].rstrip() + "…"
        return "caption", content
    content = str(text or " ")
    if len(content) > 4096:
        content = content[:4095].rstrip() + "…"
    return "text", content


async def _edit_callback_payload(query_obj, payload_type, text, reply_markup, disable_web_page_preview):
    """Apply an edit to the existing message; media messages must use editMessageCaption."""
    if payload_type == "caption":
        return await query_obj.edit_message_caption(caption=text, reply_markup=reply_markup)
    return await query_obj.edit_message_text(
        text=text,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )


async def edit_callback_screen(query_obj, text, reply_markup=None, *, disable_web_page_preview=False):
    """Update the current bot message in place; never send or delete a replacement menu."""
    payload_type, safe_text = _callback_screen_payload(query_obj, text)
    try:
        await _edit_callback_payload(
            query_obj, payload_type, safe_text, reply_markup, disable_web_page_preview
        )
        return True
    except BadRequest as exc:
        detail = " ".join(str(exc).split()).casefold()
        if "message is not modified" in detail:
            return True
        # Retry only the content edit without optional buttons. This keeps the same
        # message and is safer than sending a replacement menu into the chat.
        if reply_markup is not None:
            try:
                await _edit_callback_payload(
                    query_obj, payload_type, safe_text, None, disable_web_page_preview
                )
                log.warning("Updated existing callback screen without its keyboard: %s", exc)
                return True
            except TelegramError as fallback_exc:
                log.warning("Could not edit existing screen without keyboard: %s", fallback_exc)
                try:
                    await query_obj.edit_message_reply_markup(reply_markup=None)
                    log.warning("Removed rejected keyboard from existing screen")
                except TelegramError as markup_exc:
                    log.error("Could not remove rejected keyboard from existing screen: %s", markup_exc)
        log.warning("Could not edit callback screen in place (type=%s, chars=%s): %s",
                    payload_type, len(safe_text), exc)
        return False
    except TelegramError as exc:
        log.warning("Could not edit callback screen in place (type=%s, chars=%s): %s",
                    payload_type, len(safe_text), exc)
        return False


def _callback_message_is_captionable(message):
    """Identify Telegram message types whose existing caption can be edited."""
    if getattr(message, "caption", None) is not None:
        return True
    return any(getattr(message, field, None) for field in (
        "photo", "video", "animation", "document", "audio"
    ))


async def edit_callback_status(query_obj, status_text, *, append=True):
    """Show an action result on the clicked message without sending a new chat message.

    Text messages are edited with editMessageText; copied media cards are edited
    with editMessageCaption. The original content is retained where Telegram's
    text/caption limit permits, and the clicked keyboard is removed.
    """
    message = getattr(query_obj, "message", None)
    if message is None:
        log.warning("Cannot update callback status: callback has no accessible message")
        return False

    captionable = _callback_message_is_captionable(message)
    current = (getattr(message, "caption", None) if captionable
               else getattr(message, "text", None)) or ""
    limit = 1024 if captionable else 4096
    status_text = str(status_text or "Action completed.").strip()
    if append and current.strip():
        suffix = "\n\n" + status_text
        available = max(0, limit - len(suffix))
        preserved = current[:available]
        if len(current) > available and available > 1:
            preserved = current[:available - 1].rstrip() + "…"
        new_text = preserved + suffix
    else:
        new_text = status_text[:limit]

    try:
        if captionable:
            await query_obj.edit_message_caption(caption=new_text, reply_markup=None)
        else:
            await query_obj.edit_message_text(
                text=new_text, reply_markup=None, disable_web_page_preview=True
            )
        return True
    except BadRequest as exc:
        if "message is not modified" in str(exc).casefold():
            return True
        log.warning("Could not update clicked message in place: %s", exc)
        # Last-resort recovery: remove the stale action buttons without sending
        # or deleting a message. The original screen stays visible.
        try:
            await query_obj.edit_message_reply_markup(reply_markup=None)
        except TelegramError as markup_exc:
            log.warning("Could not remove action buttons after a failed in-place edit: %s", markup_exc)
        return False
    except TelegramError as exc:
        log.warning("Could not update clicked message in place: %s", exc)
        try:
            await query_obj.edit_message_reply_markup(reply_markup=None)
        except TelegramError as markup_exc:
            log.warning("Could not remove action buttons after a failed in-place edit: %s", markup_exc)
        return False


def local_storage_root():
    path = Path(__file__).resolve().parent / "local_storage"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup_root():
    path = Path(__file__).resolve().parent / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def remove_listing_local_storage(listing_key: str):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(listing_key))[:160]
    path = local_storage_root() / safe
    if path.exists():
        try:
            shutil.rmtree(path)
        except OSError as exc:
            log.warning("Could not remove local listing storage %s: %s", path, exc)


def safe_listing_dir(listing_key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(listing_key))[:160]
    path = local_storage_root() / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


async def save_message_media_locally(message, context, listing_key: str):
    """Download source media and caption/text into local_storage/listing_key."""
    media = get_message_media(message)
    folder = safe_listing_dir(listing_key)
    caption_text = (message.caption or message.text or "").strip()
    (folder / f"message_{message.message_id}_caption.txt").write_text(caption_text, encoding="utf-8")
    if not media:
        return {"media_file_id": "", "media_type": "", "media_filename": "", "local_media_path": ""}
    if media.get("size") and media["size"] > int(config.LOCAL_MEDIA_MAX_MB * 1024 * 1024):
        (folder / f"message_{message.message_id}_MEDIA_SKIPPED.txt").write_text(
            f"Media was larger than LOCAL_MEDIA_MAX_MB={config.LOCAL_MEDIA_MAX_MB}.\n"
            f"Telegram file_id: {media['file_id']}\n", encoding="utf-8"
        )
        return {"media_file_id": media["file_id"], "media_type": media["media_type"],
                "media_filename": media["filename"], "local_media_path": ""}
    try:
        tg_file = await context.bot.get_file(media["file_id"])
        data = bytes(await tg_file.download_as_bytearray())
        filename = media["filename"] or f"message_{message.message_id}.bin"
        destination = folder / filename
        destination.write_bytes(data)
        return {"media_file_id": media["file_id"], "media_type": media["media_type"],
                "media_filename": filename, "local_media_path": str(destination.relative_to(Path(__file__).resolve().parent))}
    except Exception as exc:
        log.warning("Could not archive local media for listing %s message %s: %s", listing_key, message.message_id, exc)
        return {"media_file_id": media["file_id"], "media_type": media["media_type"],
                "media_filename": media["filename"], "local_media_path": ""}


def create_automatic_backup(label="startup"):
    """Create and verify a SQLite + local-media archive before reporting success."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", str(label or "backup"))[:40] or "backup"
    target = backup_root() / f"abhinazh_auto_backup_{stamp}_{safe_label}.zip"
    temp_db = backup_root() / f"._backup_{stamp}.sqlite3"
    temp_archive = backup_root() / f"._backup_{stamp}.zip.tmp"
    src = None
    dst = None
    try:
        src = database.connect()
        dst = database.sqlite3.connect(temp_db)
        src.backup(dst)
        integrity = dst.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).lower() != "ok":
            raise RuntimeError(f"SQLite backup integrity check failed: {integrity!r}")
        dst.close()
        dst = None
        src.close()
        src = None

        root = local_storage_root()
        with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(temp_db, arcname="bot_data.sqlite3")
            for path in root.rglob("*"):
                if path.is_file():
                    zf.write(path, arcname=str(Path("local_storage") / path.relative_to(root)))

        # Do not report backup success or allow destructive actions unless the
        # resulting archive passes ZIP CRC validation and includes the database.
        with zipfile.ZipFile(temp_archive, "r") as zf:
            if zf.testzip() is not None:
                raise RuntimeError("Backup archive failed ZIP CRC verification")
            names = zf.namelist()
            if "bot_data.sqlite3" not in names or zf.getinfo("bot_data.sqlite3").file_size <= 0:
                raise RuntimeError("Backup archive does not contain a valid database file")

        os.replace(temp_archive, target)
        temp_db.unlink(missing_ok=True)
        archives = sorted(
            backup_root().glob("abhinazh_auto_backup_*.zip"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old in archives[int(config.AUTO_BACKUP_RETENTION):]:
            try:
                old.unlink()
            except OSError as exc:
                log.warning("Could not prune old backup %s: %s", old, exc)
        return target
    except Exception as exc:
        log.exception("Automatic backup failed: %s", exc)
        for path in (temp_archive, temp_db, target):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return None
    finally:
        if dst is not None:
            try:
                dst.close()
            except Exception:
                pass
        if src is not None:
            try:
                src.close()
            except Exception:
                pass


def get_message_media(message):
    """Return Telegram file metadata for photos/documents/video/audio/animation."""
    if getattr(message, "photo", None):
        obj = message.photo[-1]
        return {"file_id": obj.file_id, "media_type": "photo", "filename": f"message_{message.message_id}.jpg", "size": getattr(obj, "file_size", 0) or 0}
    candidates = [
        ("document", ".bin"), ("video", ".mp4"), ("animation", ".mp4"),
        ("audio", ".bin"), ("voice", ".ogg"),
    ]
    for attr, fallback_ext in candidates:
        obj = getattr(message, attr, None)
        if not obj:
            continue
        filename = getattr(obj, "file_name", "") or f"message_{message.message_id}{fallback_ext}"
        if not Path(filename).suffix:
            ext = mimetypes.guess_extension(getattr(obj, "mime_type", "") or "") or fallback_ext
            filename += ext
        return {"file_id": obj.file_id, "media_type": attr, "filename": Path(filename).name,
                "size": getattr(obj, "file_size", 0) or 0}
    return None


def india_timezone():
    """Return India time. Termux may lack the OS IANA timezone database."""
    try:
        return ZoneInfo("Asia/Kolkata")
    except (ZoneInfoNotFoundError, OSError):
        # India uses UTC+05:30 year-round; this fallback avoids requiring tzdata
        # for the Buy/Notify callback ticket formatter to work on a fresh Termux.
        return timezone(timedelta(hours=5, minutes=30), name="IST")


def source_message_number(item):
    ids = item.get("message_ids") or []
    return int(min(ids)) if ids else None


def user_name(user):
    if not user:
        return ""
    return " ".join(part for part in [user.first_name, user.last_name] if part).strip() or user.username or ""


def parse_message_image(message):
    media = get_message_media(message)
    if not media:
        return None
    if media["media_type"] == "photo":
        return media["file_id"]
    document = getattr(message, "document", None)
    if document and (document.mime_type or "").lower().startswith("image/"):
        return media["file_id"]
    return None


def preprocess_for_ocr(data: bytes):
    if Image is None:
        return None
    image = Image.open(BytesIO(data)).convert("RGB")
    # Enlarging and enhancing often helps with tiny text in account screenshots.
    max_side = max(image.size)
    if max_side < 1800:
        scale = min(2.0, 1800 / max_side)
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image)
    image = ImageEnhance.Contrast(image).enhance(1.35)
    return image


def run_ocr(data: bytes) -> str:
    if pytesseract is None or Image is None:
        return ""
    try:
        image = preprocess_for_ocr(data)
        # PSM 11 is suited to screenshots with separated text blocks.
        first = pytesseract.image_to_string(image, config="--psm 11")
        text = "\n".join(line.strip() for line in first.splitlines() if line.strip())
        return text[:12000]
    except Exception as exc:
        log.info("OCR skipped for one image: %s", exc)
        return ""


async def ocr_message_image(message, context: ContextTypes.DEFAULT_TYPE) -> str:
    file_id = parse_message_image(message)
    if not file_id:
        return ""
    if pytesseract is None or Image is None:
        return ""
    try:
        tg_file = await context.bot.get_file(file_id)
        data = bytes(await tg_file.download_as_bytearray())
        # Tesseract work is blocking, so do it off the event loop.
        return await asyncio.to_thread(run_ocr, data)
    except Exception as exc:
        log.warning("Could not download/read photo message %s: %s", message.message_id, exc)
        return ""


def configured_superadmin_id():
    """Return the immutable Superadmin Telegram ID configured in config.py."""
    try:
        value = int(getattr(config, "SUPERADMIN_USER_ID", 0))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def is_superadmin_user_id(user_id):
    root_id = configured_superadmin_id()
    try:
        return root_id is not None and int(user_id) == root_id
    except (TypeError, ValueError):
        return False


def app_role(user_id):
    """Resolve the application role; the configured root ID always wins."""
    if is_superadmin_user_id(user_id):
        return "SUPERADMIN"
    return database.get_user_role(int(user_id))


def remember_effective_user(update: Update):
    user = update.effective_user
    if user:
        database.upsert_user_profile(
            user.id,
            username=user.username or "",
            display_name=user.full_name or user.username or "",
        )


def is_saved_owner(user_id):
    try:
        saved = database.get_setting("owner_user_id")
        return bool(saved and int(saved) == int(user_id))
    except (TypeError, ValueError):
        return False


async def can_access_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """App Admin/Superadmin access is separate from Telegram group permissions."""
    user, chat = update.effective_user, update.effective_chat
    if not user or not chat:
        return False
    if is_superadmin_user_id(user.id) or app_role(user.id) == "ADMIN":
        return chat.type == ChatType.PRIVATE or chat.id == admin_chat_id()
    if chat.type == ChatType.PRIVATE:
        return is_saved_owner(user.id)
    if chat.id == admin_chat_id():
        return await user_is_chat_admin(update, context)
    return False


async def can_create_listing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Admins and approved Merchants may create listings; Merchant access is not admin access."""
    if await can_access_admin_panel(update, context):
        return True
    user, chat = update.effective_user, update.effective_chat
    return bool(
        user and chat and chat.type == ChatType.PRIVATE
        and app_role(user.id) == "MERCHANT"
    )


def superadmin_panel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Manage user roles", callback_data="role:menu", style="primary")],
        [InlineKeyboardButton("🧾 Role change history", callback_data="role:audit:0", style="primary")],
        [InlineKeyboardButton("🧹 Delete all lists / index", callback_data="admin:delete-all", style="danger")],
        [InlineKeyboardButton("♻️ Reset bot operational data", callback_data="admin:reset", style="danger")],
        [InlineKeyboardButton("💾 Create backup now", callback_data="admin:backup", style="success")],
        [InlineKeyboardButton("🏠 Home", callback_data="role:home", style="primary")],
    ])


def role_management_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Manage user by Telegram ID", callback_data="role:lookup", style="primary")],
        [InlineKeyboardButton("📋 View assigned roles", callback_data="role:list:0", style="primary")],
        [InlineKeyboardButton("🧾 View role audit history", callback_data="role:audit:0", style="primary")],
        [InlineKeyboardButton("⬅️ Back to admin panel", callback_data="role:panel", style="primary")],
    ])


def role_target_keyboard(target_id, current_role):
    choices = [
        ("🛡️ Promote to Admin", "ADMIN", "success"),
        ("🏪 Approve as Merchant", "MERCHANT", "success"),
        ("👤 Set as Buyer", "BUYER", "primary"),
        ("⬇️ Demote to User", "USER", "danger"),
    ]
    rows = []
    for label, role, style in choices:
        if role != current_role:
            rows.append([InlineKeyboardButton(
                label, callback_data=f"role:confirm:{int(target_id)}:{role}", style=style
            )])
    rows.append([InlineKeyboardButton("⬅️ Back to roles", callback_data="role:menu", style="primary")])
    return InlineKeyboardMarkup(rows)


def _role_label(record):
    display = str(record.get("display_name") or "").strip()
    username = str(record.get("username") or "").strip().lstrip("@")
    if display and username:
        return f"{display} (@{username})"
    return f"@{username}" if username else (display or "Telegram user")


async def show_superadmin_panel(query_obj):
    await edit_callback_screen(
        query_obj,
        "👑 SUPERADMIN CONTROL PANEL\n\n"
        "Root account: 1485615852\n"
        "Manage application roles, review role-change history, create backups and manage the local index.\n\n"
        "Role changes require confirmation. The configured Superadmin cannot be demoted.",
        reply_markup=superadmin_panel_keyboard(),
    )


async def show_role_menu(query_obj):
    await edit_callback_screen(
        query_obj,
        "👥 USER & ROLE MANAGEMENT\n\n"
        "Choose one task. Users are identified by their numeric Telegram ID; usernames can change and are not used for authorization.",
        reply_markup=role_management_keyboard(),
    )


async def show_role_target(query_obj, target_id):
    record = database.get_user_role_record(target_id)
    role = app_role(target_id)
    display = _role_label(record)
    await edit_callback_screen(
        query_obj,
        "👤 USER ROLE\n\n"
        f"Name: {display}\n"
        f"Telegram ID: {int(target_id)}\n"
        f"Current role: {role}\n\n"
        "Choose the new role. A confirmation screen appears before saving.",
        reply_markup=role_target_keyboard(target_id, role),
    )


async def edit_role_lookup_prompt(context, context_data, text, reply_markup=None):
    """Replace the existing role-lookup prompt; never create a second bot screen."""
    target = context_data.get("role_lookup_screen") or {}
    chat_id = target.get("chat_id")
    message_id = target.get("message_id")
    if not chat_id or not message_id:
        log.warning("Role lookup prompt reference missing; refusing to create a duplicate screen")
        return False
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
        return True
    except BadRequest as exc:
        if "message is not modified" in str(exc).casefold():
            return True
        log.warning("Could not edit stored role-lookup prompt in place: %s", exc)
        return False
    except TelegramError as exc:
        log.warning("Could not edit stored role-lookup prompt in place: %s", exc)
        return False


async def role_target_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Consume a numeric ID only while the Superadmin explicitly requested one."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not is_private(update):
        return
    if not context.user_data.get("role_lookup_pending"):
        return
    if not is_superadmin_user_id(user.id):
        context.user_data.pop("role_lookup_pending", None)
        context.user_data.pop("role_lookup_screen", None)
        return

    raw_id = (message.text or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,18}", raw_id):
        await edit_role_lookup_prompt(
            context,
            context.user_data,
            "⚠️ INVALID TELEGRAM ID\n\nSend digits only, without @ or spaces.\nExample: 123456789\n\nYou can cancel with the button below.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Cancel", callback_data="role:cancel-lookup", style="danger")
            ]]),
        )
        return
    target_id = int(raw_id)
    if target_id > 9223372036854775807:
        await edit_role_lookup_prompt(
            context,
            context.user_data,
            "⚠️ THAT ID IS TOO LARGE\n\nCheck the number and send it again.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Cancel", callback_data="role:cancel-lookup", style="danger")
            ]]),
        )
        return
    if is_superadmin_user_id(target_id):
        await edit_role_lookup_prompt(
            context,
            context.user_data,
            "🔒 THIS IS THE PROTECTED SUPERADMIN ACCOUNT\n\nThe root account cannot be changed. Send another Telegram ID or cancel.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Cancel", callback_data="role:cancel-lookup", style="danger")
            ]]),
        )
        return

    database.upsert_user_profile(target_id)
    record = database.get_user_role_record(target_id)
    role = app_role(target_id)
    screen_ref = context.user_data.get("role_lookup_screen")
    context.user_data.pop("role_lookup_pending", None)
    context.user_data.pop("role_lookup_screen", None)
    await edit_role_lookup_prompt(
        context,
        {"role_lookup_screen": screen_ref},
        "👤 USER ROLE\n\n"
        f"Name: {_role_label(record)}\n"
        f"Telegram ID: {target_id}\n"
        f"Current role: {role}\n\n"
        "Choose the new role. A confirmation screen appears before saving.",
        reply_markup=role_target_keyboard(target_id, role),
    )


async def handle_role_callback(query_obj, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """All role-management callbacks re-check the fixed Superadmin ID server-side."""
    user = query_obj.from_user
    chat = query_obj.message.chat if query_obj.message else None
    if not user or not is_superadmin_user_id(user.id):
        await answer_callback_safely(query_obj, "Only the configured Superadmin can manage roles.", show_alert=True)
        return
    if not chat or chat.type != ChatType.PRIVATE:
        await answer_callback_safely(query_obj, "Open the bot's private chat to manage user roles.", show_alert=True)
        return

    data = (query_obj.data or "").split(":")
    action = data[1] if len(data) > 1 else ""

    if action == "panel":
        await answer_callback_safely(query_obj)
        await show_superadmin_panel(query_obj)
        return
    if action == "home":
        await answer_callback_safely(query_obj)
        await edit_callback_screen(
            query_obj, welcome_screen_text(),
            reply_markup=homepage_keyboard(),
            disable_web_page_preview=True,
        )
        return
    if action == "menu":
        context.user_data.pop("role_lookup_pending", None)
        context.user_data.pop("role_lookup_screen", None)
        await answer_callback_safely(query_obj)
        await show_role_menu(query_obj)
        return
    if action == "lookup":
        context.user_data["role_lookup_pending"] = True
        context.user_data["role_lookup_screen"] = {
            "chat_id": chat.id,
            "message_id": query_obj.message.message_id,
        }
        await answer_callback_safely(query_obj)
        await edit_callback_screen(
            query_obj,
            "🔎 FIND A USER\n\n"
            "Send the user's numeric Telegram ID in your next message.\n"
            "Example: 123456789\n\n"
            "Usernames are not suitable for access control because they can change.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Cancel", callback_data="role:cancel-lookup", style="danger")
            ]]),
        )
        return
    if action == "cancel-lookup":
        context.user_data.pop("role_lookup_pending", None)
        context.user_data.pop("role_lookup_screen", None)
        await answer_callback_safely(query_obj, "Cancelled.")
        await show_role_menu(query_obj)
        return
    if action == "list":
        try:
            offset = max(0, int(data[2]))
        except (IndexError, ValueError):
            offset = 0
        rows, total = database.list_user_roles(limit=8, offset=offset)
        lines = ["📋 ASSIGNED APPLICATION ROLES", ""]
        if not rows:
            lines.append("No non-default roles have been assigned yet.")
        else:
            for row in rows:
                lines.append(
                    f"• {row['role']} — {_role_label(row)}\n  ID: {row['user_id']}"
                )
        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"role:list:{max(0, offset-8)}", style="primary"))
        if offset + 8 < total:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"role:list:{offset+8}", style="primary"))
        keyboard = []
        if nav:
            keyboard.append(nav)
        keyboard.append([InlineKeyboardButton("⬅️ Back to roles", callback_data="role:menu", style="primary")])
        await answer_callback_safely(query_obj)
        await edit_callback_screen(query_obj, 
            "\n".join(lines) + f"\n\nShowing {min(offset+1,total) if total else 0}–{min(offset+len(rows),total)} of {total}.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return
    if action == "audit":
        try:
            offset = max(0, int(data[2]))
        except (IndexError, ValueError):
            offset = 0
        rows, total = database.list_role_audit_events(limit=8, offset=offset)
        lines = ["🧾 ROLE CHANGE HISTORY", ""]
        if not rows:
            lines.append("No role changes have been recorded.")
        else:
            for row in rows:
                lines.append(
                    f"• {row['old_role']} → {row['new_role']}\n"
                    f"  User: {row['target_user_id']} | By: {row['actor_user_id']}\n"
                    f"  {row['created_at']}"
                )
        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"role:audit:{max(0, offset-8)}", style="primary"))
        if offset + 8 < total:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"role:audit:{offset+8}", style="primary"))
        keyboard = ([nav] if nav else []) + [
            [InlineKeyboardButton("⬅️ Back to roles", callback_data="role:menu", style="primary")]
        ]
        await answer_callback_safely(query_obj)
        await edit_callback_screen(query_obj, 
            "\n".join(lines) + f"\n\nShowing {min(offset+1,total) if total else 0}–{min(offset+len(rows),total)} of {total}.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return
    if action == "confirm":
        try:
            target_id = int(data[2])
            new_role = str(data[3]).upper()
        except (IndexError, ValueError):
            await answer_callback_safely(query_obj, "Invalid role-change request.", show_alert=True)
            return
        if is_superadmin_user_id(target_id):
            await answer_callback_safely(query_obj, "The Superadmin account is protected.", show_alert=True)
            return
        if new_role not in database.MANAGEABLE_USER_ROLES:
            await answer_callback_safely(query_obj, "Unsupported role.", show_alert=True)
            return
        record = database.get_user_role_record(target_id)
        current = app_role(target_id)
        if current == new_role:
            await answer_callback_safely(query_obj, "That user already has this role.", show_alert=True)
            return
        await answer_callback_safely(query_obj)
        await edit_callback_screen(query_obj, 
            "⚠️ CONFIRM ROLE CHANGE\n\n"
            f"Telegram user ID: {target_id}\n"
            f"Current role: {current}\n"
            f"New role: {new_role}\n\n"
            "This change is recorded in the role audit log. Continue?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm change", callback_data=f"role:apply:{target_id}:{new_role}", style="success")],
                [InlineKeyboardButton("Cancel", callback_data=f"role:select:{target_id}", style="danger")],
            ]),
        )
        return
    if action == "select":
        try:
            target_id = int(data[2])
        except (IndexError, ValueError):
            await answer_callback_safely(query_obj, "Invalid user reference.", show_alert=True)
            return
        if is_superadmin_user_id(target_id):
            await answer_callback_safely(query_obj, "The Superadmin account is protected.", show_alert=True)
            return
        await answer_callback_safely(query_obj)
        await show_role_target(query_obj, target_id)
        return
    if action == "apply":
        try:
            target_id = int(data[2])
            new_role = str(data[3]).upper()
            old_role, saved_role, changed = database.set_user_role(
                target_id, new_role, user.id, protected_user_id=configured_superadmin_id()
            )
        except (IndexError, ValueError) as exc:
            await answer_callback_safely(query_obj, str(exc) or "Invalid role change.", show_alert=True)
            return
        if changed:
            log.info(
                "Role changed actor_user_id=%s target_user_id=%s old_role=%s new_role=%s",
                user.id, target_id, old_role, saved_role,
            )
            response = (
                "✅ ROLE UPDATED\n\n"
                f"User ID: {target_id}\n"
                f"Previous role: {old_role}\n"
                f"New role: {saved_role}\n\n"
                "The change has been saved and recorded in the audit history."
            )
        else:
            response = f"No change made. User {target_id} already has role {saved_role}."
        await answer_callback_safely(query_obj, "Role saved." if changed else "No change.")
        await edit_callback_screen(query_obj, 
            response,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 Manage another user", callback_data="role:menu", style="primary")],
                [InlineKeyboardButton("👑 Superadmin panel", callback_data="role:panel", style="primary")],
            ]),
        )
        return

    await answer_callback_safely(query_obj, "Unknown role-management action.", show_alert=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remember_effective_user(update)
    await update.effective_message.reply_text(
        welcome_screen_text(),
        reply_markup=homepage_keyboard(),
        disable_web_page_preview=True,
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        help_screen_text(), reply_markup=homepage_keyboard(), disable_web_page_preview=True
    )


async def user_is_chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in ("administrator", "creator")
    except TelegramError as exc:
        log.warning("Could not verify group administrator: %s", exc)
        return False


async def can_manage_source_records(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Configured app admins can manage listing records; group admins retain legacy access."""
    user = update.effective_user
    if not user:
        return False
    return (
        is_superadmin_user_id(user.id)
        or app_role(user.id) == "ADMIN"
        or await user_is_chat_admin(update, context)
    )


async def can_configure_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Only an app Admin/Superadmin who is also a Telegram group admin can change group routing."""
    user = update.effective_user
    if not user or not (is_superadmin_user_id(user.id) or app_role(user.id) == "ADMIN"):
        return False
    return await user_is_chat_admin(update, context)


async def _save_source_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, target_chat, *, link=""):
    """Persist a verified source group/channel without retaining a topic from another destination."""
    previous_source_id = source_chat_id()
    if previous_source_id != int(target_chat.id):
        database.delete_setting("post_topic_id")
    database.set_setting("source_chat_id", int(target_chat.id))
    database.set_setting("source_chat_title", getattr(target_chat, "title", "") or getattr(target_chat, "full_name", "") or "Source chat")
    public_username = getattr(target_chat, "username", None)
    resolved_link = link or (f"https://t.me/{public_username}" if public_username else "")
    database.set_setting("source_group_invite_link", resolved_link)
    user = update.effective_user
    if user and is_superadmin_user_id(user.id):
        database.set_setting("owner_user_id", user.id)
        database.set_setting("owner_name", user.full_name or user.username or "Superadmin")
        database.set_setting("owner_username", user.username or "")


async def channel_post_command_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route channel-post commands to the fixed Superadmin privately.

    Channel posts identify the channel (sender_chat), not the human who
    published them. Do not reply in the channel: the bot may be a member/admin
    without the `can_post_messages` right, and configuration must never be
    authorized from a channel identity.
    """
    message = update.channel_post or update.edited_channel_post
    if not message or not message.chat or message.chat.type != ChatType.CHANNEL:
        return
    text = (message.text or message.caption or "").strip()
    match = re.match(r"^/([A-Za-z0-9_]+)(?:@[A-Za-z0-9_]+)?(?:\s|$)", text)
    if not match:
        return
    command = match.group(1).lower()
    guidance = {
        "setsource": (
            "A /setsource command was posted by a channel. For secure setup, "
            "open my private chat from your personal Telegram account and send "
            "/setsource @channelusername. Both you and the bot must be channel administrators."
        ),
        "setadmin": (
            "A /setadmin command was posted by a channel. Configure the private "
            "admin request group separately: send /setadmin from your personal "
            "Telegram account inside that private group."
        ),
        "unsetsource": "For security, send /unsetsource to me in private chat as the fixed Superadmin.",
        "source": "For source status, open my private chat and send /source.",
        "audit": "For setup diagnostics, open my private chat and send /audit.",
    }
    if command not in guidance:
        return
    root_id = configured_superadmin_id()
    if not root_id:
        log.error("Cannot notify Superadmin about channel command: SUPERADMIN_USER_ID is not configured")
        return
    title = getattr(message.chat, "title", "") or "Telegram channel"
    notice = f"Channel command notice from {title} (chat ID {message.chat.id}):\n\n{guidance[command]}"
    try:
        await context.bot.send_message(chat_id=root_id, text=notice, disable_web_page_preview=True)
    except TelegramError as exc:
        log.warning(
            "Could not DM Superadmin about channel command=%s chat_id=%s: %s",
            command, message.chat.id, exc,
        )


async def setsource(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat:
        return

    # Secure channel setup must be initiated in private chat. A channel_post
    # identifies sender_chat (the channel), not the human who pressed Publish.
    if chat.type == ChatType.PRIVATE:
        if not user or not (is_superadmin_user_id(user.id) or app_role(user.id) == "ADMIN"):
            await message.reply_text("🔒 Only the configured Superadmin or an app Admin can configure the source chat.")
            return
        if not context.args:
            await message.reply_text(
                "To connect a source channel, send /setsource @channelusername here in this private chat.\n"
                "The bot must already be an administrator of that channel, and you must also be a channel administrator.\n\n"
                "For a source group, you can also run /setsource inside the group as its Telegram administrator."
            )
            return
        try:
            reference = normalize_source_chat_reference(context.args[0])
            target = await context.bot.get_chat(reference)
            if target.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
                raise ValueError("The selected chat must be a group, supergroup, or channel.")
            human_member = await context.bot.get_chat_member(target.id, user.id)
            if human_member.status not in ("administrator", "creator"):
                raise ValueError("Telegram confirms that you are not an administrator of that target chat.")
            bot_member = await context.bot.get_chat_member(target.id, context.bot.id)
            if target.type == ChatType.CHANNEL:
                if bot_member.status not in ("administrator", "creator"):
                    raise ValueError("Add the bot as a channel administrator first. Telegram delivers channel posts to bots that are members of the channel.")
            elif bot_member.status not in ("member", "administrator", "creator"):
                raise ValueError("The bot has not joined the selected group. Add it to the group first.")
            await _save_source_chat(update, context, target)
        except (ValueError, TelegramError) as exc:
            log.info("Source-chat setup rejected for user %s: %s", user.id, exc)
            await message.reply_text(f"⚠️ Source chat was not changed. {exc}")
            return
        target_title = getattr(target, "title", "") or getattr(target, "full_name", "") or "Source chat"
        await message.reply_text(
            f"✅ Source {target.type} connected: {target_title}.\n"
            "I will index new text, captions, supported media and edits from this chat.\n"
            "Telegram does not let bots fetch arbitrary old channel/group history automatically."
        )
        return

    if chat.type == ChatType.CHANNEL:
        await message.reply_text(
            "This command was posted as the channel, so Telegram does not provide the human administrator's user ID for secure authorization.\n\n"
            "Open the bot's private chat and send /setsource @channelusername. The bot must be a channel administrator."
        )
        return

    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Run /setsource in the source group, or send /setsource @channelusername to the bot in private chat.")
        return
    if not user:
        await message.reply_text("Telegram received this as an anonymous/group identity, not your personal account. Switch 'Send as' to your own Telegram account, then run /setsource again so I can verify your Superadmin/Admin permissions.")
        return
    if not await can_configure_group(update, context):
        await message.reply_text("Only the configured Superadmin or an app Admin who is also a Telegram administrator of this group can set the source group.")
        return
    old_source_id = source_chat_id()
    provided_link = next((arg.strip() for arg in (context.args or [])
                          if arg.strip().startswith("https://t.me/")), "")
    if provided_link:
        group_link = provided_link
    elif getattr(chat, "username", None):
        group_link = f"https://t.me/{chat.username}"
    elif old_source_id == chat.id:
        group_link = source_group_link()
    elif old_source_id is None:
        group_link = str(getattr(config, "SOURCE_GROUP_INVITE_LINK", "") or "").strip()
    else:
        group_link = ""
    await _save_source_chat(update, context, chat, link=group_link)
    await message.reply_text(
        "✅ Source group saved. I will index new text, captions, photo text and edits from this group.\n"
        "I cannot fetch old group history automatically. Reply to an old post with /indexreply, "
        "or forward the old post into the group to index it.\n"
        + (f"Homepage group link saved: {group_link}" if group_link else
           "No invite link was saved for this private group. You can set SOURCE_GROUP_INVITE_LINK in config.py to show a Browse button on the homepage.")
    )


async def source_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    saved_id = source_chat_id()
    title = database.get_setting("source_chat_title", "")
    if saved_id is None:
        msg = "No source chat set yet. For a group, run /setsource in the group as its administrator. For a channel, send /setsource @channelusername to me in private chat."
    else:
        topic_id = configured_post_topic_id()
        topic_label = f"Forum publishing topic ID: {topic_id}" if topic_id else "Forum publishing topic: General/default"
        msg = f"Source chat: {title or 'Configured chat'}\nChat ID: {saved_id}\n{topic_label}\nIndexed listings: {database.count_listings()}"
    await update.effective_message.reply_text(msg)


def _publish_destination_label(item):
    title = str(item.get("title") or "Telegram chat").strip()
    username = str(item.get("username") or "").strip().lstrip("@")
    chat_id = int(item.get("chat_id"))
    return f"{title} (@{username}) — {chat_id}" if username else f"{title} — {chat_id}"


async def add_destination_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register an additional group/channel used for account publication."""
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat:
        return
    if chat.type != ChatType.PRIVATE:
        await message.reply_text("For security, add publication destinations in the bot's private chat.")
        return
    if not user or not is_superadmin_user_id(user.id):
        await message.reply_text("⛔ Only the fixed Superadmin can add publication destinations.")
        return
    if not context.args:
        await message.reply_text(
            "Usage: /adddestination @channelusername or /adddestination -1001234567890\n"
            "Add the bot to the target group/channel first. You must also be a Telegram administrator there."
        )
        return
    try:
        reference = normalize_source_chat_reference(context.args[0])
        target = await context.bot.get_chat(reference)
        if target.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
            raise ValueError("The destination must be a group, supergroup, or channel.")
        human_member = await context.bot.get_chat_member(target.id, user.id)
        if human_member.status not in ("administrator", "creator"):
            raise ValueError("Telegram does not confirm that you are an administrator of that destination.")
        bot_member = await context.bot.get_chat_member(target.id, context.bot.id)
        if target.type == ChatType.CHANNEL:
            if bot_member.status not in ("administrator", "creator"):
                raise ValueError("The bot must be a channel administrator with permission to post messages.")
            if getattr(bot_member, "can_post_messages", True) is False:
                raise ValueError("The bot's channel administrator role does not allow it to post messages.")
        elif bot_member.status not in ("member", "administrator", "creator"):
            raise ValueError("The bot must first join the target group.")
        if source_chat_id() == int(target.id):
            raise ValueError("This chat is already the primary source/indexing destination; it is added automatically.")
        database.add_publish_destination(
            int(target.id),
            title=getattr(target, "title", "") or getattr(target, "full_name", "") or "Telegram chat",
            chat_type=str(target.type), username=getattr(target, "username", "") or "",
            added_by=user.id,
        )
    except (ValueError, TelegramError) as exc:
        await message.reply_text(f"⚠️ Destination was not added. {exc}")
        return
    await message.reply_text(
        "✅ Publication destination added. New approved listings will be copied there as well.\n"
        + _publish_destination_label({
            "title": getattr(target, "title", "") or getattr(target, "full_name", "") or "Telegram chat",
            "username": getattr(target, "username", "") or "", "chat_id": target.id,
        })
    )


async def remove_destination_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat:
        return
    if chat.type != ChatType.PRIVATE:
        await message.reply_text("For security, remove publication destinations in the bot's private chat.")
        return
    if not user or not is_superadmin_user_id(user.id):
        await message.reply_text("⛔ Only the fixed Superadmin can remove publication destinations.")
        return
    if not context.args:
        await message.reply_text("Usage: /removedestination <chat ID or @username>. Use /destinations to see saved destinations.")
        return
    raw = str(context.args[0]).strip()
    destination = None
    if raw.lstrip("-").isdigit():
        destination = database.get_publish_destination(int(raw))
    else:
        wanted = raw.lstrip("@").lower()
        destination = next(
            (item for item in database.list_publish_destinations()
             if str(item.get("username") or "").lower() == wanted),
            None,
        )
    if not destination:
        await message.reply_text("No saved additional destination matched that ID or username. The primary source cannot be removed here.")
        return
    removed = database.remove_publish_destination(destination["chat_id"])
    await message.reply_text(
        "✅ Destination removed. Existing posts are unchanged; future posts will no longer be copied there."
        if removed else "That destination was already removed."
    )


async def destinations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    user = update.effective_user
    if not user or not (is_superadmin_user_id(user.id) or app_role(user.id) == "ADMIN"):
        await message.reply_text("⛔ Destination settings are visible only to the Superadmin and app Admins.")
        return
    lines = ["📣 ACCOUNT PUBLICATION DESTINATIONS"]
    primary_id = source_chat_id()
    if primary_id is None:
        lines.append("Primary source/index destination: not configured")
    else:
        lines.append(f"Primary source/index destination: {configured_source_title() or 'Configured chat'} — {primary_id}")
    extras = [
        item for item in database.list_publish_destinations()
        if primary_id is None or int(item["chat_id"]) != int(primary_id)
    ]
    if extras:
        lines.extend(["", "Additional destinations:"])
        lines.extend(f"{index}. {_publish_destination_label(item)}" for index, item in enumerate(extras, 1))
    else:
        lines.extend(["", "No additional destinations configured."])
    if is_superadmin_user_id(user.id):
        lines.extend([
            "",
            "Add: /adddestination @channelusername or chat ID",
            "Remove: /removedestination @username or chat ID",
        ])
    await message.reply_text("\n".join(lines))


async def unsetsource(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    if not chat or not message:
        return
    if chat.type == ChatType.PRIVATE:
        if not user or not is_superadmin_user_id(user.id):
            await message.reply_text("Only the fixed Superadmin can unset the source chat from private chat.")
            return
        database.delete_setting("source_chat_id")
        database.delete_setting("source_chat_title")
        database.delete_setting("source_group_invite_link")
        database.delete_setting("post_topic_id")
        await message.reply_text("✅ Source chat disconnected. Existing indexed records are preserved.")
        return
    if chat.type == ChatType.CHANNEL:
        await message.reply_text("For security, send /unsetsource to me in private chat as the Superadmin.")
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Run this command in the source group, or privately as the Superadmin for a channel.")
        return
    if not await can_configure_group(update, context):
        await update.effective_message.reply_text("Only the configured Superadmin or an app Admin who is also a Telegram administrator of this group can remove the source setting.")
        return
    if source_chat_id() != chat.id:
        await update.effective_message.reply_text("This group is not the configured source group.")
        return
    database.delete_setting("source_chat_id")
    database.delete_setting("source_chat_title")
    database.delete_setting("post_topic_id")
    await update.effective_message.reply_text("Source group unset. I will not index this group until set again.")


async def index_message(message, context, *, from_reply=False, allow_bot_post=False):
    chat = message.chat
    if not chat or source_chat_id() != chat.id:
        return None
    # Ignore bot-authored updates during normal ingestion, but explicitly permit
    # messages returned by our own confirmed publisher so they enter the index.
    if message.from_user and message.from_user.is_bot and not allow_bot_post:
        return None
    caption = (message.caption or "").strip()
    text = (message.text or "").strip()
    body = caption or text
    media = get_message_media(message)
    if not body and not media:
        return None
    ocr_text = await ocr_message_image(message, context) if parse_message_image(message) else ""
    topic_id = int(getattr(message, "message_thread_id", 0) or 0)
    topic_title = database.get_topic_title(topic_id) if topic_id else ""
    # Listing key must be stable before media is saved so album members share the same folder.
    listing_key = database.make_listing_key(chat.id, message.message_id, message.media_group_id)
    local_media = await save_message_media_locally(message, context, listing_key)
    sender_id, sender_name, sender_username = source_sender_details(
        message.from_user, getattr(message, "sender_chat", None)
    )
    listing_key = database.upsert_source_part(
        chat_id=chat.id,
        message_id=message.message_id,
        media_group_id=message.media_group_id,
        message_text=text if not caption else "",
        caption_text=caption,
        ocr_text=ocr_text,
        created_at=(message.date or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        sender_id=sender_id,
        sender_name=sender_name,
        sender_username=sender_username,
        has_photo=bool(parse_message_image(message)),
        media_file_id=local_media.get("media_file_id", ""),
        media_type=local_media.get("media_type", ""),
        media_filename=local_media.get("media_filename", ""),
        local_media_path=local_media.get("local_media_path", ""),
        topic_id=topic_id,
        topic_title=topic_title,
    )
    schedule_alert_check(context.application, listing_key)
    schedule_amount_admin_check(context.application, listing_key)
    current_item = database.get_listing(listing_key)
    if current_item:
        latest = database.get_latest_listing()
        if latest:
            database.set_setting("latest_listing_title", latest.get("title") or "Latest account listing")
            database.set_setting("latest_listing_link", latest.get("link") or "")
            database.set_setting("latest_listing_owner", latest.get("sender_username") or latest.get("sender_name") or "")
    return listing_key


def build_amount_admin_notice_text(item: dict) -> str:
    """Explain how admins can correct a missing amount using supported source edits."""
    return (
        "⚠️ AMOUNT REQUIRED - ADMIN REVIEW\n\n"
        f"Listing: {item.get('title') or 'Untitled listing'}\n"
        f"Topic: {item.get('topic_title') or 'No topic title'}\n"
        f"Source post: {item.get('link') or 'Private source post'}\n\n"
        "I could not detect a currency and amount in the source topic or written caption.\n\n"
        "To correct it, edit the original source post's caption/text, or update the forum topic title "
        "so it includes the price and currency. The bot will re-index the source edit or topic-title update.\n\n"
        "Please do not reply to this notice: replies to this notice are not linked to the listing."
    )


def schedule_amount_admin_check(application: Application, listing_key: str):
    tasks = application.bot_data.setdefault("amount_check_tasks", {})
    old_task = tasks.get(listing_key)
    if old_task and not old_task.done():
        old_task.cancel()

    async def settle_then_check():
        try:
            await asyncio.sleep(config.ALBUM_QUIET_SECONDS)
            item = database.get_listing(listing_key)
            dest = admin_chat_id()
            if not item or not dest or item.get("amount_min") is not None or database.has_amount_admin_notice(listing_key):
                return
            admin_text = build_amount_admin_notice_text(item)
            sent = await application.bot.send_message(dest, admin_text, disable_web_page_preview=True)
            database.mark_amount_admin_notice(listing_key, sent.message_id)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Amount-admin check failed for %s", listing_key)
        finally:
            current = tasks.get(listing_key)
            if current is asyncio.current_task():
                tasks.pop(listing_key, None)

    tasks[listing_key] = asyncio.create_task(settle_then_check())


def schedule_alert_check(application: Application, listing_key: str):
    tasks = application.bot_data.setdefault("alert_check_tasks", {})
    old_task = tasks.get(listing_key)
    if old_task and not old_task.done():
        old_task.cancel()

    async def settle_then_alert():
        try:
            await asyncio.sleep(config.ALBUM_QUIET_SECONDS)
            item = database.get_listing(listing_key)
            if item:
                await check_alerts_for_listing(application, item)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Alert check failed for listing %s", listing_key)
        finally:
            current = tasks.get(listing_key)
            if current is asyncio.current_task():
                tasks.pop(listing_key, None)

    tasks[listing_key] = asyncio.create_task(settle_then_alert())


async def ingest_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat:
        return
    if not source_chat_matches(chat.type, chat.id, source_chat_id()):
        return
    # Telegram includes topic titles when the create/edit service message is delivered.
    # Existing topic titles can be seeded once with /settopicname inside each topic.
    created = getattr(message, "forum_topic_created", None)
    edited = getattr(message, "forum_topic_edited", None)
    if created and getattr(created, "name", None):
        database.set_topic_title(message.message_id, created.name)
        return
    if edited and getattr(edited, "name", None):
        topic_id = int(getattr(message, "message_thread_id", 0) or message.message_id)
        database.set_topic_title(topic_id, edited.name)
        return
    command_text = (message.text or message.caption or "").lstrip()
    if command_text.startswith("/"):
        return
    await index_message(message, context)


async def indexreply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Import an old listing only when the command replies to that source message."""
    message = update.effective_message
    chat = update.effective_chat
    if not message:
        log.warning("/indexreply received without an effective message")
        return

    # Give a useful, visible answer even when the command is typed normally in private chat.
    if not message.reply_to_message:
        if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await message.reply_text(
                "ℹ️ /indexreply must be used in the configured listing group. "
                "Open that group, reply directly to the old listing post, then send /indexreply.\n\n"
                "Telegram does not let bots fetch arbitrary old group history by typing a message ID."
            )
        elif source_chat_id() != chat.id:
            await message.reply_text(
                "⚠️ This is not the configured listing group. Open the configured source group, "
                "reply to the original listing, and send /indexreply."
            )
        else:
            await message.reply_text(
                "ℹ️ I received /indexreply, but you did not reply to a listing.\n\n"
                "1. Find the old listing in this group.\n"
                "2. Tap and hold it, then choose Reply.\n"
                "3. Send /indexreply as the reply.\n\n"
                "I cannot fetch old group history on my own; I need the original message as a reply."
            )
        return

    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or source_chat_id() != chat.id:
        await message.reply_text("⚠️ Use /indexreply as a reply inside the configured source group.")
        return
    if not await can_manage_source_records(update, context):
        await message.reply_text("⚠️ Only a configured bot Admin or source-group administrator can manually import an old post.")
        return

    target = message.reply_to_message
    try:
        result = await index_message(target, context, from_reply=True)
        if result:
            await message.reply_text(
                f"✅ Indexed the replied-to post (message ID {target.message_id}). "
                "Its caption/text and readable photo text are now searchable."
            )
        else:
            await message.reply_text(
                "⚠️ I could not index that post. It may contain no caption/text or readable image content, "
                "or the bot may not have source-group access. Check the latest Termux logs."
            )
    except Exception:
        log.exception("Manual /indexreply failed for source message %s", getattr(target, "message_id", "?"))
        await message.reply_text(
            "❌ The import failed because of an internal error. The traceback was logged in Termux; "
            "send the last error lines to support after removing tokens and private links."
        )


def _copied_message_id(value):
    if isinstance(value, dict):
        value = value.get("message_id") or value.get("id")
    return getattr(value, "message_id", None) or getattr(value, "id", None) or (int(value) if isinstance(value, int) else None)


async def copy_listing_messages(bot, destination_chat_id: int, item: dict, request_id=None):
    """Copy all source messages, falling back to one-by-one copies if bulk copy fails."""
    ids = item.get("message_ids") or []
    ids = sorted(set(int(value) for value in ids))[:config.MAX_INDEXED_MESSAGES_PER_LISTING]
    copied_ids = []
    errors = []
    if not ids:
        return copied_ids, ["No source message IDs were indexed."]
    try:
        copied = await bot.copy_messages(
            chat_id=destination_chat_id,
            from_chat_id=int(item["source_chat_id"]),
            message_ids=ids,
            disable_notification=False,
        )
        copied_ids = [mid for obj in (copied or []) if (mid := _copied_message_id(obj))]
        if not copied_ids:
            errors.append("Bulk copy returned no copied message IDs.")
    except TelegramError as exc:
        errors.append(f"Bulk copy failed: {exc}")
        log.warning("Bulk copy failed for listing %s to chat %s: %s", item.get("listing_key"), destination_chat_id, exc)
        # Retry each message separately. This preserves whatever is copyable even if
        # Telegram rejects one item in the album/listing.
        for message_id in ids:
            try:
                copied_message = await bot.copy_message(
                    chat_id=destination_chat_id,
                    from_chat_id=int(item["source_chat_id"]),
                    message_id=message_id,
                    disable_notification=False,
                )
                mid = _copied_message_id(copied_message)
                if mid:
                    copied_ids.append(mid)
            except TelegramError as one_exc:
                errors.append(f"Source message {message_id}: {one_exc}")
                log.warning("Single-message copy failed for source message %s: %s", message_id, one_exc)
    if not copied_ids and ids and not any("Source message" in error for error in errors):
        # Bulk response was empty without raising; retry individually.
        for message_id in ids:
            try:
                copied_message = await bot.copy_message(
                    chat_id=destination_chat_id, from_chat_id=int(item["source_chat_id"]),
                    message_id=message_id, disable_notification=False,
                )
                mid = _copied_message_id(copied_message)
                if mid:
                    copied_ids.append(mid)
            except TelegramError as one_exc:
                errors.append(f"Source message {message_id}: {one_exc}")
    if request_id is not None:
        for copied_id in copied_ids:
            database.add_request_reply_target(int(request_id), int(destination_chat_id), int(copied_id))
    return copied_ids, errors


async def attach_actions_to_copied_listing(bot, destination_chat_id: int, copied_ids, reply_markup):
    """Put the Buy/Notify buttons below the copied source listing when possible."""
    if copied_ids:
        try:
            await bot.edit_message_reply_markup(
                chat_id=destination_chat_id,
                message_id=int(copied_ids[-1]),
                reply_markup=reply_markup,
            )
            return True
        except TelegramError as exc:
            log.info("Could not attach action buttons to copied listing: %s", exc)
    return False


async def send_listing_to_user(bot, user_id: int, item: dict, *, summary: str, reply_markup,
                              track_for_admin_delete: bool = False):
    """Send listing context/media and optionally map this admin copy to its source record."""
    summary_message = await bot.send_message(chat_id=user_id, text=summary, disable_web_page_preview=True)
    if track_for_admin_delete and getattr(summary_message, "message_id", None):
        try:
            database.add_listing_search_reference(user_id, summary_message.message_id, item["listing_key"])
        except Exception:
            log.exception("Could not register admin search summary for deletion mapping")
    copied_ids, errors = await copy_listing_messages(bot, user_id, item)
    if track_for_admin_delete:
        for copied_id in copied_ids:
            try:
                database.add_listing_search_reference(user_id, copied_id, item["listing_key"])
            except Exception:
                log.exception("Could not register copied admin search message %s for deletion mapping", copied_id)
    if copied_ids:
        attached = await attach_actions_to_copied_listing(bot, user_id, copied_ids, reply_markup)
        if not attached:
            await bot.send_message(
                chat_id=user_id,
                text="Choose what you want to do with this listing:",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
        return True
    fallback = "I found the listing, but Telegram did not allow its original post/media to be copied."
    if item.get("link"):
        fallback += f"\nOpen the original post: {item['link']}"
    if errors:
        log.warning("All copy attempts failed for listing %s: %s", item.get("listing_key"), " | ".join(errors))
    await bot.send_message(
        chat_id=user_id,
        text=fallback + "\nYou can still submit a request below.",
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    return False


def pretty_match(item):
    pct = round(float(item.get("score", 0)) * 100)
    nmatched = len(item.get("matched_terms", []))
    total = int(item.get("total_terms", 0))
    match_line = f"Match score: {pct}%"
    if total:
        match_line += f" ({nmatched}/{total} keywords matched)"
    return match_line


async def search_for_query(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    message = update.effective_message
    if not is_private(update):
        await message.reply_text("For privacy, open my private chat and run /find there.")
        return
    query = (query or "").strip()
    parsed = parse_query(query)
    has_structured_filter = any(parsed["options"].get(k) is not None for k in
                                ("price_min", "price_max", "kingdom_min", "kingdom_max", "posted_within_hours"))
    sort_only_query = parsed["options"]["sort"] != "relevance"
    if not query or (not parsed["terms"] and not has_structured_filter and not sort_only_query):
        await message.reply_text(
            "Usage: /find <keywords or filters>\n"
            "Example: /find K1700 5champ\n"
            "Example: /find price:1000-5000 rally lead\n"
            "Example: /find kingdom:1500-1700 leader skin"
        )
        return
    results = search_listings(query, limit=config.MAX_SEARCH_RESULTS)
    if not results:
        buy_token = make_action_token(update.effective_user.id, "buy_query", query=query)
        notify_token = make_action_token(update.effective_user.id, "notify_query", query=query)
        await message.reply_text(
            "No suitable listing was found in the active index.\n\n"
            "Choose what you want the admin to do. Both options include your exact search criteria and username.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 Request / buy this account type", callback_data=f"act:{buy_token}")],
                [InlineKeyboardButton(config.NOTIFY_BUTTON_TEXT, callback_data=f"act:{notify_token}")],
            ]),
            disable_web_page_preview=True,
        )
        return

    await message.reply_text(
        f"Found {len(results)} best match(es) for: {query}\n"
        "I will copy the original listing message(s) below, including photos when Telegram allows it."
    )
    for index, item in enumerate(results, 1):
        created = format_created_at(item.get("created_at"))
        post_no = source_message_number(item)
        seller = (f"@{item.get('sender_username')}" if item.get("sender_username")
                  else (item.get("sender_name") or "Unknown / hidden poster"))
        summary = (
            f"📦 MATCH #{index}: {item.get('title') or 'Untitled listing'}\n\n"
            f"🎯 {pretty_match(item)}\n"
            f"🆔 Listing / source message number: #{post_no if post_no is not None else 'unknown'}\n"
            f"🕒 Posted: {created}\n"
            f"📂 Topic/category: {item.get('topic_title') or 'Not available (topic title not registered)'}\n"
            f"👤 Listing owner: {seller}\n"
            f"💰 Amount: {amount_summary(item)}\n"
            f"🖼️ Images indexed: {item.get('image_count', 0)}"
        )
        if item.get("link"):
            summary += f"\nSource post: {item['link']}"
        buy_token = make_action_token(update.effective_user.id, "buy_listing", query=query,
                                      listing_key=item["listing_key"])
        notify_token = make_action_token(update.effective_user.id, "notify_listing", query=query,
                                         listing_key=item["listing_key"])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(config.BUY_BUTTON_TEXT, callback_data=f"act:{buy_token}")],
            [InlineKeyboardButton(config.NOTIFY_BUTTON_TEXT, callback_data=f"act:{notify_token}")],
        ])
        # Put the buttons directly under the copied listing media when Telegram permits it.
        actor_id = update.effective_user.id
        can_delete_search_results = (
            is_superadmin_user_id(actor_id)
            or app_role(actor_id) == "ADMIN"
            or is_saved_owner(actor_id)
        )
        await send_listing_to_user(
            context.bot, actor_id, item, summary=summary, reply_markup=keyboard,
            track_for_admin_delete=can_delete_search_results,
        )

    # If we returned only partial ranked matches, give the user a direct option to
    # notify the admin/subscribe for the complete requested criteria too.
    total_terms = len(parsed["terms"])
    has_full_match = (total_terms == 0) or any(
        len(item.get("matched_terms", [])) == total_terms for item in results
    )
    if total_terms and not has_full_match:
        buy_token = make_action_token(update.effective_user.id, "buy_query", query=query)
        notify_token = make_action_token(update.effective_user.id, "notify_query", query=query)
        await message.reply_text(
            "No listing matched every keyword. You can ask the admin to source the exact account type or save an alert for the complete filter.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 Request / buy this account type", callback_data=f"act:{buy_token}")],
                [InlineKeyboardButton(config.NOTIFY_BUTTON_TEXT, callback_data=f"act:{notify_token}")],
            ])
        )


async def find_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await search_for_query(update, context, " ".join(context.args).strip())


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permanently remove an indexed listing by replying to its admin search result."""
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat:
        return
    if not await can_access_admin_panel(update, context):
        await message.reply_text("🔒 Only configured bot Admins and the Superadmin can delete listings.")
        return
    target = getattr(message, "reply_to_message", None)
    if not target:
        await message.reply_text(
            "Reply to the account result (its summary or copied screenshot) with /delete.\n"
            "This removes the account from the searchable database; the original Telegram source post remains."
        )
        return

    listing_key = database.get_listing_key_by_search_reference(chat.id, target.message_id)
    if not listing_key and chat.id == source_chat_id():
        listing_key = database.get_listing_key_by_source_message(chat.id, target.message_id)
    if not listing_key and chat.id == admin_chat_id():
        request = database.get_request_by_admin_message(chat.id, target.message_id)
        listing_key = (request or {}).get("listing_key") or ""

    if not listing_key:
        await message.reply_text(
            "I couldn't match that replied-to message to a listing. Run /find again with this bot version, "
            "then reply to the account summary or copied screenshot with /delete."
        )
        return
    item = database.get_listing(listing_key)
    if not item:
        await message.reply_text("That account has already been removed from the searchable database.")
        return

    title = item.get("title") or "Untitled listing"
    source_ids = item.get("message_ids") or []
    if not database.delete_listing_by_key(listing_key):
        await message.reply_text("The account could not be removed because its database record changed. Search again and retry.")
        return
    remove_listing_local_storage(listing_key)
    await message.reply_text(
        f"🗑️ ACCOUNT DELETED FROM SEARCH DATABASE\n\n{title}\n"
        f"Removed listing record and {len(source_ids)} indexed source message(s).\n"
        "The original Telegram post is unchanged and will not appear in /find or new listing alerts."
    )


async def private_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle explicit Superadmin role-ID input before falling back to normal search."""
    if not is_private(update) or not update.effective_message:
        return
    if context.user_data.get("new_listing"):
        return
    if context.user_data.get("role_lookup_pending"):
        await role_target_input(update, context)
        return
    remember_effective_user(update)
    query = (update.effective_message.text or "").strip()
    if query:
        await search_for_query(update, context, query)


def make_action_token(user_id, action_type, query="", listing_key=""):
    import secrets
    token = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
    database.add_callback_action(token, user_id, action_type, query, listing_key)
    return token


def amount_summary(item):
    low = item.get("amount_min")
    high = item.get("amount_max")
    source = item.get("amount_source") or "missing"
    if low is None:
        return "Not explicitly stated; admin should confirm"
    def fmt(value):
        return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"
    if high is None or (isinstance(high, (int, float)) and high == float("inf")):
        amount = f"{fmt(low)}+"
    elif float(low) == float(high):
        amount = fmt(low)
    else:
        amount = f"{fmt(low)}–{fmt(high)}"
    currency = str(item.get("amount_currency") or "").strip()
    source_label = "topic title" if source == "topic" else ("photo OCR" if source == "ocr" else "post text/caption")
    if currency in {"₹", "€", "£", "¥", "$"}:
        amount = f"{currency}{amount.replace('–', f'–{currency}')}"
        currency_note = f"currency marker: {currency}"
    elif currency:
        currency_note = f"currency: {currency}"
    else:
        currency_note = "currency not stated"
    return f"{amount} — source: {source_label}; {currency_note}"


def _clean_text_block(text, max_chars=850, *, ocr=False):
    """Normalize listing copy into readable paragraphs without dumping OCR noise."""
    lines_out = []
    seen = set()
    for raw in (text or "").replace("\r", "\n").splitlines():
        line = " ".join(raw.split()).strip(" •|—-\t")
        if not line:
            continue
        low = line.lower()
        if low.startswith("[topic]") or low.startswith("[text read from photo]"):
            continue
        if ocr:
            # Avoid common screenshot/UI OCR noise but keep useful account stats.
            alnum = sum(ch.isalnum() for ch in line)
            if alnum < 3 or len(line) > 220:
                continue
            if re.fullmatch(r"[\W_]+", line):
                continue
            if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", line):  # in-game clock/time
                continue
            if re.fullmatch(r"[€$₹]\s*\d{1,4}\s*:\s*[\d.,]+[kK]?", line):  # OCR often reads a UI timer as currency
                continue
            if re.fullmatch(r"\d{7,}", line):  # unlabelled long numeric UI IDs are not account details
                continue
        key = low
        if key in seen:
            continue
        seen.add(key)
        lines_out.append(line)
        candidate = "\n".join(lines_out)
        if len(candidate) >= max_chars:
            return candidate[:max_chars].rsplit("\n", 1)[0] + "\n…"
    return "\n".join(lines_out)[:max_chars]


def render_request_text(*, request_id, username, first_name, user_id, action_type,
                        query, item=None):
    """Human-readable admin ticket with clear paragraphs and exact source metadata."""
    uname = f"@{username}" if username else "MISSING USERNAME"
    name_line = first_name or "Not provided"
    requested_at = format_created_at(now_iso())
    lines = [
        f"📥 ACCOUNT REQUEST #{request_id}",
        "━━━━━━━━━━━━━━━━━━━━",
        "👤 REQUESTER DETAILS",
        f"Username: {uname}",
        f"Name: {name_line}",
        f"Telegram user ID: {user_id}",
        f"Request submitted: {requested_at}",
        "",
    ]
    if action_type in ("buy_listing", "buy_query"):
        lines += [
            "🛒 REQUEST TYPE: BUY / FIND ACCOUNT TO PURCHASE",
            "Explicit intent: The user wants to buy the selected account or an account matching this requirement.",
            f"Search/filter requirement: {query or 'Not provided'}",
            "",
        ]
    elif action_type == "notify_listing":
        lines += [
            "🔔 REQUEST TYPE: NOTIFY WHEN AVAILABLE",
            "Explicit intent: The user wants to be notified whenever this type of account becomes available.",
            f"User requirement: {query or 'Not provided'}",
            "",
        ]
    else:
        lines += [
            "🔔 REQUEST TYPE: FIND AND NOTIFY",
            "Explicit intent: The user wants an account matching these criteria and notification when one becomes available.",
            f"Exact user requirement: {query or 'Not provided'}",
            "",
        ]

    if item:
        post_no = source_message_number(item)
        owner = (f"@{item.get('sender_username')}" if item.get("sender_username")
                 else (item.get("sender_name") or "Unknown / poster username hidden"))
        topic = item.get("topic_title") or "Not available (topic title not registered)"
        source_group = database.get_setting("source_chat_title", "Configured listing group") or "Configured listing group"
        caption = _clean_text_block(item.get("caption_text") or "", 900)
        ocr = _clean_text_block(item.get("ocr_text") or "", 700, ocr=True)
        lines += [
            "📦 SELECTED LISTING DETAILS",
            f"Account/listing title: {item.get('title') or 'Untitled listing'}",
            f"Source group: {source_group}",
            f"Listing group link: {source_group_link() or 'Not configured'}",
            f"Topic/category: {topic}",
            f"Topic ID: {item.get('topic_id') or 'Not available'}",
            f"Post/message number: #{post_no if post_no is not None else 'unknown'}",
            f"Uploaded at: {format_created_at(item.get('created_at'))}",
            f"Posted by / listing owner: {owner}",
            f"Amount: {amount_summary(item)}",
            f"Images in this listing: {item.get('image_count', 0)}",
            "",
            "📝 POST TEXT / CAPTION",
            caption or "No written caption was attached to the source post.",
        ]
        if ocr:
            lines += ["", "🔎 READABLE TEXT EXTRACTED FROM PHOTOS (OCR; may contain errors)", ocr]
        lines += [
            "",
            f"🔗 Original listing post: {item.get('link') or 'Private post link unavailable'}",
            "📎 The bot will copy the original listing media/messages below this ticket when Telegram permits.",
            "",
        ]
    else:
        lines += [
            "📦 SELECTED LISTING DETAILS",
            "No active listing in the index matched this requirement.",
            "Please search the private listing group and reply to this ticket with a suitable account or information.",
            "",
        ]

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "✅ ADMIN INSTRUCTIONS",
        "1. Reply directly to THIS request message with your response.",
        "2. Text, photo, video, document, or album replies will be delivered to the requester in private chat.",
        f"Delivery target: {uname} (Telegram user ID {user_id})",
    ]
    return "\n".join(lines)[:config.MAX_REQUEST_TEXT]


def admin_panel_text(user_id=None):
    if is_superadmin_user_id(user_id):
        return (
            "👑 SUPERADMIN CONTROL PANEL\n\n"
            "Manage application roles, review role-change history, create backups and manage local index data.\n"
            "Destructive actions require confirmation. A verified backup must succeed before any data is deleted."
        )
    return (
        "🛠️ ABHINAZH BOT ADMIN PANEL\n\n"
        "Manage approved admin operations. A verified backup is required before destructive data changes."
    )


def admin_panel_keyboard(user_id=None):
    rows = []
    if is_superadmin_user_id(user_id):
        rows.extend([
            [InlineKeyboardButton("👥 Manage user roles", callback_data="role:menu", style="primary")],
            [InlineKeyboardButton("🧾 Role change history", callback_data="role:audit:0", style="primary")],
            [InlineKeyboardButton("🧹 Delete all lists / index", callback_data="admin:delete-all", style="danger")],
            [InlineKeyboardButton("♻️ Reset bot operational data", callback_data="admin:reset", style="danger")],
        ])
    # Ordinary app admins can create a local backup, but bulk deletion/reset is
    # deliberately reserved for the immutable Superadmin account.
    rows.extend([
        [InlineKeyboardButton("💾 Create backup now", callback_data="admin:backup", style="success")],
        [InlineKeyboardButton("🏠 Home", callback_data="admin:home", style="primary")],
    ])
    return InlineKeyboardMarkup(rows)


def admin_result_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin:panel", style="primary")],
        [InlineKeyboardButton("🏠 Home", callback_data="admin:home", style="primary")],
    ])


def confirmation_keyboard(confirm_action):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data=confirm_action, style="danger")],
        [InlineKeyboardButton("Cancel", callback_data="admin:cancel", style="primary")],
    ])


async def run_backup(label):
    return await asyncio.to_thread(create_automatic_backup, label)


def clear_local_media_archive():
    """Remove archived listing media and return the number of failed removals."""
    media_root = local_storage_root()
    failures = 0
    try:
        children = list(media_root.iterdir())
    except OSError as exc:
        log.exception("Could not inspect local listing archive %s: %s", media_root, exc)
        return 1
    for child in children:
        try:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        except OSError as exc:
            failures += 1
            log.warning("Could not remove local listing storage %s: %s", child, exc)
    return failures


async def adminpanel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not await can_access_admin_panel(update, context):
        if message:
            await message.reply_text("🔒 The admin panel is restricted to configured admins and the Superadmin.")
        return
    user = update.effective_user
    await message.reply_text(
        admin_panel_text(user.id),
        reply_markup=admin_panel_keyboard(user.id),
    )


async def handle_admin_callback(query_obj, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not query_obj.message or not await can_access_admin_panel(update, context):
        await answer_callback_safely(query_obj, "This control is restricted to configured bot administrators.", show_alert=True)
        return

    user_id = update.effective_user.id if update.effective_user else 0
    action = query_obj.data.split(":", 1)[1] if ":" in query_obj.data else ""
    if action in ("delete-all", "delete-all-confirm", "reset", "reset-confirm") and not is_superadmin_user_id(user_id):
        await answer_callback_safely(
            query_obj,
            "Only the configured Superadmin can delete the full index or reset operational data.",
            show_alert=True,
        )
        return
    await answer_callback_safely(query_obj)

    if action in ("panel", "cancel"):
        await edit_callback_screen(
            query_obj, admin_panel_text(user_id),
            reply_markup=admin_panel_keyboard(user_id),
        )
        return

    if action == "home":
        await edit_callback_screen(
            query_obj, welcome_screen_text(),
            reply_markup=homepage_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if action == "backup":
        backup = await run_backup("manual")
        if backup:
            text = f"✅ BACKUP CREATED\n\nFile: {backup.name}\nYour database and local listing archive were included."
        else:
            text = "⚠️ BACKUP FAILED\n\nNo data was changed. Check the Termux log, correct the storage issue, and try again."
        await edit_callback_screen(query_obj, text, reply_markup=admin_result_keyboard(user_id))
        return

    if action == "delete-all":
        await edit_callback_screen(
            query_obj,
            "⚠️ DELETE ALL LISTS / INDEX?\n\n"
            "This removes locally indexed listings, indexed source messages, and their archived media/captions.\n"
            "It does not delete original Telegram posts, alerts, group settings or request tickets.\n\n"
            "A backup must complete before deletion. Continue?",
            reply_markup=confirmation_keyboard("admin:delete-all-confirm"),
        )
        return

    if action == "delete-all-confirm":
        backup = await run_backup("before_delete_all")
        if not backup:
            await edit_callback_screen(
                query_obj,
                "⚠️ DELETION CANCELLED — BACKUP FAILED\n\n"
                "Nothing was deleted. Check Termux storage and logs, then retry from the Admin Panel.",
                reply_markup=admin_result_keyboard(user_id),
            )
            return
        counts = database.clear_index()
        media_cleanup_failures = await asyncio.to_thread(clear_local_media_archive)
        cleanup_note = (
            "Archived listing media/captions removed."
            if media_cleanup_failures == 0 else
            f"⚠️ {media_cleanup_failures} local archive item(s) could not be removed. Check Termux logs and remove them manually if needed."
        )
        await edit_callback_screen(
            query_obj,
            "✅ LOCAL INDEX CLEARED\n\n"
            f"Listings removed: {counts['listings']}\n"
            f"Indexed source messages removed: {counts['messages']}\n"
            f"{cleanup_note} Original Telegram posts and saved alerts remain.",
            reply_markup=admin_result_keyboard(user_id),
        )
        return

    if action == "reset":
        await edit_callback_screen(
            query_obj,
            "⚠️ RESET BOT OPERATIONAL DATA?\n\n"
            "This clears local listings/index, archived listing media, saved alerts, request tickets, temporary button data, and saved admin-post drafts/recovery records.\n"
            "The configured source/admin group settings and application roles are retained.\n\n"
            "A backup must complete before the reset. Continue?",
            reply_markup=confirmation_keyboard("admin:reset-confirm"),
        )
        return

    if action == "reset-confirm":
        backup = await run_backup("before_reset")
        if not backup:
            await edit_callback_screen(
                query_obj,
                "⚠️ RESET CANCELLED — BACKUP FAILED\n\n"
                "Nothing was reset. Check Termux storage and logs, then retry from the Admin Panel.",
                reply_markup=admin_result_keyboard(user_id),
            )
            return
        counts = database.clear_operational_data()
        media_cleanup_failures = await asyncio.to_thread(clear_local_media_archive)
        cleanup_note = (
            "All local listing media/captions were removed."
            if media_cleanup_failures == 0 else
            f"⚠️ {media_cleanup_failures} local archive item(s) could not be removed. Check Termux logs and clean them manually if needed."
        )
        await edit_callback_screen(
            query_obj,
            "♻️ OPERATIONAL RESET COMPLETE\n\n"
            f"Listings removed: {counts['listings']}\n"
            f"Indexed messages removed: {counts['messages']}\n"
            f"Alerts removed: {counts['alerts']}\n"
            f"Request mappings removed: {counts['requests']}\n\n"
            f"{cleanup_note}\n"
            "Source/admin group settings, Superadmin identity, roles and role audit history were retained.\n"
            "Saved admin post drafts and recovery records were also cleared; check any uncertain publication at the destination before resetting.",
            reply_markup=admin_result_keyboard(user_id),
        )
        return

    await answer_callback_safely(query_obj, "Unknown admin action. Open /adminpanel and try again.", show_alert=True)


async def _callback_action_impl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_obj = update.callback_query
    if not query_obj:
        return
    remember_effective_user(update)
    if (query_obj.data or "").startswith("role:"):
        await handle_role_callback(query_obj, update, context)
        return
    if (query_obj.data or "").startswith("admin:"):
        await handle_admin_callback(query_obj, update, context)
        return
    if (query_obj.data or "").startswith("index:"):
        if not await user_can_manage_index(update, context):
            await answer_callback_safely(query_obj, "Only the configured owner or a group admin can manage the index.", show_alert=True)
            return
        action_name = query_obj.data.split(":", 1)[1]
        if action_name == "delete-confirm" and not is_superadmin_user_id(update.effective_user.id if update.effective_user else 0):
            await answer_callback_safely(query_obj, "Only the configured Superadmin can clear the entire listing index.", show_alert=True)
            return
        if action_name == "delete-cancel":
            await answer_callback_safely(query_obj, "Index clear cancelled.")
            await edit_callback_screen(
                query_obj,
                "Index clear cancelled. No listing data was changed.\n\nYou can reopen /index delete if needed.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Back to Home", callback_data="home:root")
                ]]),
            )
            return
        if action_name != "delete-confirm":
            await answer_callback_safely(query_obj, "Unknown index action.", show_alert=True)
            return
        backup = await run_backup("before_index_clear")
        if not backup:
            await answer_callback_safely(query_obj, "Backup failed. Nothing was deleted.", show_alert=True)
            await edit_callback_screen(
                query_obj,
                "⚠️ INDEX CLEAR CANCELLED — BACKUP FAILED\n\nNothing was deleted. Check Termux storage/logs and try again.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Back to Home", callback_data="home:root")
                ]]),
            )
            return
        counts = database.clear_index()
        media_cleanup_failures = await asyncio.to_thread(clear_local_media_archive)
        export_dir = Path(__file__).resolve().parent / "exports"
        deleted_exports = 0
        if export_dir.exists():
            for export_path in export_dir.glob("listing_index_*.json"):
                try:
                    export_path.unlink()
                    deleted_exports += 1
                except OSError as exc:
                    log.warning("Could not delete local index export %s: %s", export_path, exc)
        await answer_callback_safely(query_obj, "Local index cleared.", show_alert=True)
        await edit_callback_screen(
            query_obj,
            "🧹 LOCAL INDEX CLEARED\n\n"
            f"Listings removed: {counts['listings']}\n"
            f"Indexed source messages removed: {counts['messages']}\n"
            f"Local JSON index exports removed: {deleted_exports}\n"
            + ("Local archived media/captions removed.\n\n" if media_cleanup_failures == 0 else
               f"⚠️ {media_cleanup_failures} local media/archive item(s) could not be removed; check Termux logs.\n\n")
            + "The original Telegram group posts, bot settings and saved user alerts remain.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Back to Home", callback_data="home:root")
            ]]),
        )
        return

    callback_data = str(query_obj.data or "").strip()
    # Backward compatibility for old inline keyboards generated by earlier builds.
    # A legacy "resume" button is a return-to-home shortcut; it does not restart
    # indexing or alter source-group configuration.
    if callback_data in ("help", "home_help"):
        action = "home:help"
    elif callback_data in ("resume", "home", "home_resume", "home:resume"):
        action = "home:root"
    elif callback_data.startswith("home:"):
        action = callback_data
    else:
        action = None

    if action and action.startswith("home:"):
        await answer_callback_safely(query_obj)
        if action in ("home:root", "home:home"):
            response_text = welcome_screen_text()
            reply_markup = homepage_keyboard()
            disable_preview = True
        elif action == "home:amount":
            response_text = (
                "💰 FILTER BY AMOUNT\n\n"
                "Enter /filter amount followed by a range, minimum or maximum.\n\n"
                "Examples:\n/filter amount 500-2000\n/filter amount under 1000\n"
                "/filter amount over 5000\n/filter amount ₹500-₹2000\n/find USD 100-250\n\n"
                "The bot compares amounts stored from the listing topic or written caption. "
                "Different currencies are not converted into one another."
            )
            reply_markup = home_navigation_keyboard()
            disable_preview = False
        elif action == "home:search":
            response_text = (
                "🔎 SEARCH ACCOUNTS\n\n"
                "Send /find followed by keywords and filters. Combine filters in one command.\n\n"
                "Examples:\n/find K1700 5champ\n/find K1500 K1700 5champ\n"
                "/find price 1000-5000 rally lead\n/find restricted 5 champs 120 blessed\n"
                "/find 7d cheapest rally lead\n\n"
                "No suitable result? Use Buy or Notify on a matching listing."
            )
            reply_markup = home_navigation_keyboard()
            disable_preview = False
        elif action == "home:alerts":
            alerts = database.get_user_alerts(query_obj.from_user.id)
            if not alerts:
                response_text = "🔔 YOUR ALERTS\n\nYou have no active alerts.\nCreate one with /alertme K1700 5champ."
            else:
                lines = ["🔔 YOUR ACTIVE ALERTS", ""]
                for pos, alert in enumerate(alerts, 1):
                    lines.append(f"{pos}. {alert['query']}")
                lines += ["", "Cancel an alert with /cancelalert N."]
                response_text = "\n".join(lines)
            reply_markup = home_navigation_keyboard()
            disable_preview = False
        elif action == "home:help":
            response_text = help_screen_text()
            reply_markup = home_navigation_keyboard()
            disable_preview = True
        else:
            await answer_callback_safely(query_obj, "This menu has expired. Send /start to reopen it.", show_alert=True)
            return

        await edit_callback_screen(
            query_obj,
            response_text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_preview,
        )
        return

    if (query_obj.data or "").startswith("soldreq:"):
        if not query_obj.message or query_obj.message.chat.id != admin_chat_id():
            await answer_callback_safely(query_obj, "This button only works in the configured admin group.", show_alert=True)
            return
        if not (
            is_superadmin_user_id(query_obj.from_user.id)
            or app_role(query_obj.from_user.id) == "ADMIN"
            or await user_is_chat_admin(update, context)
        ):
            await answer_callback_safely(query_obj, "Only an administrator can mark a listing sold.", show_alert=True)
            return
        try:
            req_id = int(query_obj.data.split(":", 1)[1])
        except (ValueError, IndexError):
            await answer_callback_safely(query_obj, "Invalid request reference.", show_alert=True)
            return
        request = database.get_request_by_id(req_id)
        if not request or not request.get("listing_key"):
            await answer_callback_safely(query_obj, "This request has no specific listing to mark sold.", show_alert=True)
            return
        sold_item = database.get_listing(request["listing_key"])
        if not sold_item:
            await answer_callback_safely(query_obj, "Listing not found in the index.", show_alert=True)
            return
        database.delete_listing_by_key(request["listing_key"])
        remove_listing_local_storage(request["listing_key"])
        await answer_callback_safely(query_obj, "Listing removed from the local index.")
        current_text = getattr(query_obj.message, "text", None) or "Listing request"
        status_text = (
            f"\n\n✅ STATUS: MARKED SOLD / REMOVED\n"
            f"Title: {sold_item.get('title') or 'Untitled listing'}\n"
            f"Source post: #{source_message_number(sold_item) or 'unknown'}\n"
            "The original Telegram source post is unchanged."
        )
        await edit_callback_screen(query_obj, (current_text + status_text)[:4000], reply_markup=None)
        return
    token = (query_obj.data or "").partition(":")[2]
    action, token_status = database.consume_callback_action(token, query_obj.from_user.id)
    if token_status == "missing":
        await answer_callback_safely(query_obj, "This button has expired. Please search again.", show_alert=True)
        return
    if token_status == "forbidden":
        await answer_callback_safely(query_obj, "This button belongs to another user.", show_alert=True)
        return
    if not action:
        await answer_callback_safely(query_obj, "This button could not be verified. Please search again.", show_alert=True)
        return
    await answer_callback_safely(query_obj)
    user = query_obj.from_user
    item = database.get_listing(action.get("listing_key")) if action.get("listing_key") else None
    action_type = action["action_type"]
    is_notify_action = action_type in ("notify_query", "notify_listing")
    if action_type in ("buy_listing", "notify_listing") and (not item or not int(item.get("is_available", 1))):
        await edit_callback_status(
            query_obj,
            "⚠️ This listing has been marked sold or removed and is no longer available. Search again or create a fresh alert.",
        )
        return

    # Save Notify subscriptions before trying to contact the admin group. That way a
    # group-permission problem cannot silently prevent the customer's future alert.
    auto_alert_created = False
    alert_limit_reached = False
    alert_needs_specific_filter = False
    if is_notify_action:
        alert_query = action.get("query", "").strip()
        if not alert_query and item:
            alert_query = item.get("title", "").strip()
        parsed_alert = parse_query(alert_query)
        has_alert_filter = bool(parsed_alert["terms"]) or any(
            parsed_alert["options"].get(k) is not None
            for k in ("price_min", "price_max", "kingdom_min", "kingdom_max", "posted_within_hours")
        )
        if has_alert_filter and database.count_user_alerts(user.id) < config.MAX_ALERTS_PER_USER:
            database.add_alert(user.id, alert_query)
            auto_alert_created = True
        elif not has_alert_filter:
            alert_needs_specific_filter = True
        else:
            alert_limit_reached = True

    dest_chat = admin_chat_id()
    if not dest_chat:
        if is_notify_action:
            msg = "✅ Your notification filter was saved." if auto_alert_created else "⚠️ No automatic alert was saved."
            await edit_callback_status(
                query_obj,
                msg + " The private admin group is not configured, so no request ticket was sent. "
                "Ask the admin to run /setadmin inside the private admin group.",
            )
        else:
            await edit_callback_status(
                query_obj,
                "⚠️ The private admin request group is not configured, so your request was not sent. "
                "Ask the admin to run /setadmin inside that group.",
            )
        return
    request_text = render_request_text(
        request_id="PENDING", username=user.username or "", first_name=user.full_name,
        user_id=user.id, action_type=action_type, query=action.get("query", ""), item=item
    )
    try:
        admin_message = await context.bot.send_message(
            chat_id=dest_chat, text=request_text, disable_web_page_preview=True
        )
        request_id = database.add_bot_request(
            user_id=user.id, username=user.username or "", first_name=user.full_name,
            request_type=action_type, requirement=action.get("query", ""),
            listing_key=action.get("listing_key", ""), admin_chat_id=dest_chat,
            admin_message_id=admin_message.message_id,
        )
        request_text = render_request_text(
            request_id=request_id, username=user.username or "", first_name=user.full_name,
            user_id=user.id, action_type=action_type, query=action.get("query", ""), item=item
        )
        admin_markup = None
        if item:
            admin_markup = InlineKeyboardMarkup([[InlineKeyboardButton(
                "✅ Mark this listing sold / remove", callback_data=f"soldreq:{request_id}"
            )]])
        await context.bot.edit_message_text(
            chat_id=dest_chat, message_id=admin_message.message_id, text=request_text,
            disable_web_page_preview=True, reply_markup=admin_markup
        )
        # Copy the selected post/photos beneath the ticket. Replying to the ticket OR
        # any copied source-media message routes the admin's answer to this requester.
        if item and item.get("message_ids"):
            copied_ids, copy_errors = await copy_listing_messages(
                context.bot, dest_chat, item, request_id=request_id
            )
            if not copied_ids:
                error_detail = "\n".join(copy_errors[-2:])[:500]
                try:
                    await context.bot.send_message(
                        chat_id=dest_chat,
                        text=(f"⚠️ ORIGINAL LISTING MEDIA WAS NOT COPIED for request #{request_id}.\n"
                              f"Open the original post: {item.get('link') or 'link unavailable'}\n"
                              f"Reason: {error_detail or 'Telegram did not return any copied messages.'}")
                    )
                except TelegramError:
                    pass
            elif len(copied_ids) < len(item.get("message_ids") or []):
                await context.bot.send_message(
                    chat_id=dest_chat,
                    text=(f"⚠️ Partial media copy for request #{request_id}: copied {len(copied_ids)} of "
                          f"{len(item.get('message_ids') or [])} source messages.\n"
                          f"Original post: {item.get('link') or 'link unavailable'}")
                )
        confirmation = (
            f"✅ Your {('buy' if action_type in ('buy_listing', 'buy_query') else 'notify')}-request has been sent to the admin. "
            f"Request #{request_id}. The admin can reply with text or media, and I will deliver it here."
        )
        if is_notify_action:
            if auto_alert_created:
                confirmation += "\n\nI also saved an automatic alert for this filter and will send matching listings when they are indexed."
            elif alert_limit_reached:
                confirmation += f"\n\nYour automatic alert could not be added because you may already have {config.MAX_ALERTS_PER_USER} active alerts. Cancel one with /cancelalert N, then use /alertme with your filter."
            elif alert_needs_specific_filter:
                confirmation += "\n\nThe admin request was sent, but the search did not contain specific account criteria, so no automatic alert was saved. Use /alertme with account keywords or a price/kingdom filter."
        await edit_callback_status(query_obj, confirmation)
    except TelegramError as exc:
        log.exception("Could not route user request to admin group: %s", exc)
        if is_notify_action and auto_alert_created:
            await edit_callback_status(
                query_obj,
                "⚠️ Your notification filter was saved and can still alert you about future matches, "
                "but the admin request could not be delivered. Ask the owner to check the admin group's bot permissions.",
            )
        elif is_notify_action:
            await edit_callback_status(
                query_obj,
                "⚠️ The admin request could not be delivered and no automatic alert was saved. "
                "Check the admin group, then create an alert with /alertme and your filter.",
            )
        else:
            await edit_callback_status(
                query_obj,
                "⚠️ Your request could not be delivered to the admin group. Ask the owner to check bot membership and permissions.",
            )


async def callback_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log callback failures and report them as an alert, never as a new chat message."""
    try:
        await _callback_action_impl(update, context)
    except Exception as exc:
        callback = getattr(update, "callback_query", None)
        callback_data = getattr(callback, "data", "") if callback else ""
        callback_user = getattr(getattr(callback, "from_user", None), "id", None) if callback else None
        log.exception(
            "Unhandled callback/button failure. callback_data=%r user_id=%s",
            callback_data, callback_user,
        )
        if callback:
            try:
                await callback.answer(
                    "This action could not be completed. Please try again. If it keeps happening, send the owner the time of the error.",
                    show_alert=True,
                )
            except TelegramError as answer_exc:
                log.info("Could not acknowledge failed callback %r: %s", callback_data, answer_exc)


async def setadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat:
        return
    if chat.type == ChatType.CHANNEL:
        await message.reply_text(
            "This is a channel, not the private admin-only group. Send /setadmin from your personal Telegram account inside a separate private group.\n"
            "Do not post it as the channel. Channel posts do not identify the human administrator."
        )
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Run /setadmin from your personal Telegram account inside your private admin-only group.")
        return
    if not update.effective_user:
        await message.reply_text("Telegram received this as an anonymous/group identity, not your personal account. Switch 'Send as' to your own Telegram account, then run /setadmin again.")
        return
    if not await can_configure_group(update, context):
        await message.reply_text("Only the configured Superadmin or an app Admin who is also a Telegram administrator of this group can configure the admin request group.")
        return
    database.set_setting("admin_chat_id", chat.id)
    database.set_setting("admin_chat_title", chat.title or "Private admin group")
    if update.effective_user and is_superadmin_user_id(update.effective_user.id):
        user = update.effective_user
        # Only the fixed root account can set or repair the legacy owner label.
        # Native Telegram group-admin status grants group setup access, not root
        # ownership of the application.
        database.set_setting("owner_user_id", user.id)
        database.set_setting("owner_name", user.full_name or user.username or "Superadmin")
        database.set_setting("owner_username", user.username or "")
    await message.reply_text(
        "✅ Private admin group configured. Buy and notify requests will appear here. "
        "Reply directly to a bot request message with your response, photo, or other media; "
        "I will deliver that reply to the requesting user's private chat.\n\n"
        "You can also reply exactly `sold` to a listing-specific ticket or its copied listing media to permanently remove that listing from the local index."
    )


async def user_can_manage_index(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Superadmin/app Admin, the legacy saved owner, or configured group admins can manage index data."""
    user, chat = update.effective_user, update.effective_chat
    if not user or not chat:
        return False
    role = app_role(user.id)
    if chat.type == ChatType.PRIVATE:
        return (
            is_superadmin_user_id(user.id)
            or role == "ADMIN"
            or is_saved_owner(user.id)
        )
    if chat.id not in (source_chat_id(), admin_chat_id()):
        return False
    return is_superadmin_user_id(user.id) or role == "ADMIN" or await user_is_chat_admin(update, context)


async def index_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    if not await user_can_manage_index(update, context):
        await message.reply_text(
            "🔒 Index management is restricted to the configured owner in private chat or an administrator of the configured listing/admin group."
        )
        return
    action = (context.args[0].lower() if context.args else "status")
    if action in ("status", "show", "view"):
        await message.reply_text(
            "🗂️ LOCAL INDEX STATUS\n\n"
            f"Active listings: {database.count_listings()}\n"
            f"Stored listing records (including sold/archived source-group entries): {database.count_all_listings()}\n"
            f"Indexed source messages/photos: {database.count_source_parts()}\n"
            f"Listings with OCR text: {database.count_ocr_listings()}\n\n"
            "Storage file: bot_data.sqlite3 in the bot project folder. It persists across restarts.\n"
            "Use /index export to download a JSON snapshot. Only the Superadmin can use /index delete to clear listing-index data."
        )
        return
    if action in ("export", "backup", "download"):
        snapshot = database.export_index_snapshot()
        export_dir = Path(__file__).resolve().parent / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / f"listing_index_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        export_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        with export_path.open("rb") as export_file:
            await message.reply_document(
                document=export_file,
                filename=export_path.name,
                caption=(
                    f"✅ Index export created. Listings: {len(snapshot['listings'])}; "
                    f"source messages: {len(snapshot['source_parts'])}.\n"
                    f"A local copy is saved in the project's exports folder: {export_path.name}."
                ),
            )
        return
    if action == "import":
        target = message.reply_to_message
        document = getattr(target, "document", None) if target else None
        if not document or not (document.file_name or "").lower().endswith(".json"):
            await message.reply_text(
                "To restore/merge an index backup, upload the JSON export to this private chat, then reply to that file with /index import.\n"
                "For safety, imports are accepted only when every saved message belongs to the currently configured source group."
            )
            return
        if not source_chat_id():
            await message.reply_text("Configure the listing group with /setsource before importing an index backup.")
            return
        try:
            telegram_file = await context.bot.get_file(document.file_id)
            payload = bytes(await telegram_file.download_as_bytearray())
            snapshot = json.loads(payload.decode("utf-8"))
            parts = snapshot.get("source_parts")
            listings = snapshot.get("listings", [])
            if not isinstance(parts, list) or not isinstance(listings, list):
                raise ValueError("JSON must contain 'source_parts' and 'listings' arrays")
            foreign_chats = {int(part.get("chat_id")) for part in parts if int(part.get("chat_id", 0)) != source_chat_id()}
            if foreign_chats:
                await message.reply_text(
                    "Import stopped: this backup contains messages from a different source group. "
                    "Use the same configured source group as the backup, or start with a fresh project configured to that group."
                )
                return
            imported = 0
            for part in parts:
                database.upsert_source_part(
                    chat_id=int(part["chat_id"]), message_id=int(part["message_id"]),
                    media_group_id=part.get("media_group_id") or None,
                    message_text=part.get("message_text") or "", ocr_text=part.get("ocr_text") or "",
                    created_at=part.get("created_at") or now_iso(), sender_id=int(part.get("sender_id") or 0),
                    sender_name=part.get("sender_name") or "", sender_username=part.get("sender_username") or "",
                    has_photo=bool(part.get("has_photo")),
                    media_file_id=part.get("media_file_id") or "",
                    media_type=part.get("media_type") or "",
                    media_filename=part.get("media_filename") or "",
                    local_media_path=part.get("local_media_path") or "",
                    caption_text=part.get("caption_text") or "",
                    topic_id=int(part.get("topic_id") or 0),
                    topic_title=part.get("topic_title") or "",
                )
                imported += 1
            for listing in listings:
                key = listing.get("listing_key")
                if key:
                    database.set_listing_availability_by_key(key, bool(listing.get("is_available", True)))
            await message.reply_text(
                f"✅ INDEX BACKUP MERGED\n\nImported/updated {imported} message records.\n"
                f"Active listings now: {database.count_listings()}\n"
                "Import updates the local index; it does not post anything to Telegram groups or replace your bot settings."
            )
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            await message.reply_text(f"Could not import this JSON backup: {exc}")
        except TelegramError as exc:
            log.exception("Could not download index import file: %s", exc)
            await message.reply_text("Telegram could not download the file. Please upload the JSON export again and retry.")
        except Exception as exc:
            log.exception("Unexpected index import failure: %s", exc)
            await message.reply_text(f"Index import failed ({type(exc).__name__}). See the Termux log for details.")
        return
    if action in ("delete", "clear", "reset"):
        if not is_superadmin_user_id(update.effective_user.id if update.effective_user else 0):
            await message.reply_text("🔒 Only the configured Superadmin can clear the entire listing index.")
            return
        await message.reply_text(
            "⚠️ CLEAR THE LOCAL LISTING INDEX?\n\n"
            "This removes every indexed listing and indexed source-message record from the local database. "
            "It does not delete Telegram group posts, bot settings, saved user alerts, or request tickets. "
            "New listing messages will rebuild the index.\n\n"
            "Press Confirm only if you want to continue.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🧹 Confirm clear index", callback_data="index:delete-confirm"),
                InlineKeyboardButton("Cancel", callback_data="index:delete-cancel"),
            ]]),
        )
        return
    await message.reply_text(
        "Usage:\n/index status — view counts and local database location\n"
        "/index export — download a JSON export and save a local copy\n"
        "/index import — reply to a JSON backup file to merge/restore its entries\n"
        "/index delete — Superadmin only; ask for confirmation before clearing every indexed listing"
    )


async def delete_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Superadmin shortcut for the existing backed-up, confirmed local-index clear."""
    message = update.effective_message
    if not message:
        return
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_superadmin_user_id(user_id):
        await message.reply_text("🔒 /deleteall is restricted to the configured Superadmin.")
        return
    if not await user_can_manage_index(update, context):
        await message.reply_text("Open the bot's private chat and run /deleteall there.")
        return
    await message.reply_text(
        "⚠️ DELETE ALL ACCOUNT LISTINGS FROM THE LOCAL DATABASE?\n\n"
        "This permanently removes every indexed account and its searchable message records. "
        "It does NOT wipe the entire bot database: bot settings, admin roles, saved alerts, request tickets "
        "and the configured groups remain. Original Telegram posts are not deleted.\n\n"
        "A verified backup must succeed before deletion. Press Confirm only if you want to continue.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🧹 Confirm delete all listings", callback_data="index:delete-confirm"),
            InlineKeyboardButton("Cancel", callback_data="index:delete-cancel"),
        ]]),
    )


async def audit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    if not await user_can_manage_index(update, context):
        await message.reply_text("🔒 The diagnostic audit is available only to the configured owner or group administrators.")
        return
    bot_user = await context.bot.get_me()
    checks = []
    source_id = source_chat_id()
    admin_id = admin_chat_id()
    checks.append(f"Source chat configured: {'YES' if source_id else 'NO — run /setsource'}")
    checks.append(f"Admin request group configured: {'YES' if admin_id else 'NO — run /setadmin'}")
    if source_id:
        try:
            configured_source = await context.bot.get_chat(source_id)
            checks.append(f"Source chat type: {configured_source.type}")
            checks.append(f"Source chat title: {configured_source.title or getattr(configured_source, 'full_name', '') or 'unknown'}")
        except TelegramError as exc:
            checks.append(f"Source chat lookup: ERROR ({type(exc).__name__})")
    for label, chat_id in (("source chat", source_id), ("admin group", admin_id)):
        if not chat_id:
            continue
        try:
            member = await context.bot.get_chat_member(chat_id, bot_user.id)
            status = getattr(member, "status", "unknown")
            checks.append(f"Bot membership in {label}: {status}")
        except TelegramError as exc:
            checks.append(f"Bot membership in {label}: ERROR ({type(exc).__name__})")
    checks += [
        f"Active listings: {database.count_listings()}",
        f"Stored listing records: {database.count_all_listings()}",
        f"Indexed message parts: {database.count_source_parts()}",
        f"OCR package available: {'YES' if pytesseract is not None and Image is not None else 'NO'}",
        f"Saved active alerts: {len(database.get_active_alerts())}",
        f"Owner label: {owner_display_name() or owner_username() or 'not set'}",
        "Button handlers registered: home, buy/request, notify, mark-sold, index-clear/cancel",
        "Channel source setup requires the bot to be a channel administrator. Group source setup requires privacy mode disabled or commands/replies explicitly addressed to the bot.",
        "This checks configuration/membership only; a real test post and a real test request are still needed to confirm end-to-end delivery.",
    ]
    await message.reply_text("🩺 BOT CONFIGURATION AUDIT\n\n" + "\n".join(checks))


async def admin_reply_forwarder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or chat.id != admin_chat_id():
        return
    actor = update.effective_user
    if not actor or not (
        is_superadmin_user_id(actor.id)
        or app_role(actor.id) == "ADMIN"
        or await user_is_chat_admin(update, context)
    ):
        return
    # Service commands are handled elsewhere. Ordinary text/media is only routed when
    # it is a reply to a registered request (directly or as part of a media album).
    if message.text and message.text.startswith("/"):
        return
    reply = getattr(message, "reply_to_message", None)
    if reply and message.text and re.fullmatch(r"\s*sold\s*[.!✅]*\s*", message.text, flags=re.IGNORECASE):
        request = database.get_request_by_admin_message(chat.id, reply.message_id)
        if request:
            listing_key = (request.get("listing_key") or "").strip()
            if not listing_key:
                await message.reply_text(
                    f"Request #{request['id']} does not refer to one specific indexed listing, so nothing was deleted."
                )
                return
            item = database.get_listing(listing_key)
            if not item:
                await message.reply_text("That listing has already been removed from the local index.")
                return
            removed_title = item.get("title") or "Untitled listing"
            removed_count = len(item.get("message_ids") or [])
            database.delete_listing_by_key(listing_key)
            remove_listing_local_storage(listing_key)
            await message.reply_text(
                f"🗑️ SOLD — REMOVED FROM LOCAL INDEX\n\n{removed_title}\n"
                f"Removed one listing record and {removed_count} indexed source message(s).\n"
                "The original Telegram group post is unchanged. Use /indexreply on that source post to import it again if needed."
            )
            return
    if message.media_group_id:
        batches = context.application.bot_data.setdefault("admin_reply_media_batches", {})
        key = f"{chat.id}:{message.media_group_id}"
        batch = batches.setdefault(key, {"messages": {}, "task": None})
        batch["messages"][message.message_id] = message
        old_task = batch.get("task")
        if old_task and not old_task.done():
            old_task.cancel()

        async def flush_media_batch():
            try:
                await asyncio.sleep(1.3)
                current = batches.get(key)
                if not current:
                    return
                msgs = sorted(current["messages"].values(), key=lambda m: m.message_id)
                request = None
                for msg in msgs:
                    replied = getattr(msg, "reply_to_message", None)
                    if replied:
                        request = database.get_request_by_admin_message(chat.id, replied.message_id)
                        if request:
                            break
                if request:
                    ids = [m.message_id for m in msgs]
                    try:
                        # Prefer a true forward so the user sees the admin's original
                        # message attribution and the media album stays grouped.
                        await context.bot.forward_messages(
                            chat_id=int(request["user_id"]), from_chat_id=chat.id,
                            message_ids=ids, disable_notification=False
                        )
                        database.mark_request_replied(request["id"])
                    except TelegramError as forward_exc:
                        log.info("Forwarding album failed, trying copy: %s", forward_exc)
                        try:
                            await context.bot.copy_messages(
                                chat_id=int(request["user_id"]), from_chat_id=chat.id,
                                message_ids=ids, disable_notification=False
                            )
                            database.mark_request_replied(request["id"])
                        except TelegramError as exc:
                            log.warning("Could not relay admin media album to requester: %s", exc)
                            await context.bot.send_message(
                                chat_id=int(request["user_id"]),
                                text="The admin replied with an album, but Telegram could not forward or copy it. Please contact the admin."
                            )
                batches.pop(key, None)
            except asyncio.CancelledError:
                return
        batch["task"] = asyncio.create_task(flush_media_batch())
        return

    reply = getattr(message, "reply_to_message", None)
    if not reply:
        return
    request = database.get_request_by_admin_message(chat.id, reply.message_id)
    if not request:
        return
    try:
        await context.bot.forward_message(
            chat_id=int(request["user_id"]), from_chat_id=chat.id,
            message_id=message.message_id
        )
        database.mark_request_replied(request["id"])
        log.info("Admin response to request #%s delivered to user %s", request["id"], request["user_id"])
    except TelegramError as forward_exc:
        log.info("Forwarding admin reply failed, trying copy: %s", forward_exc)
        try:
            await context.bot.copy_message(
                chat_id=int(request["user_id"]), from_chat_id=chat.id,
                message_id=message.message_id
            )
            database.mark_request_replied(request["id"])
        except TelegramError as exc:
            log.warning("Could not relay admin reply to user %s: %s", request["user_id"], exc)
            try:
                await context.bot.send_message(
                    chat_id=int(request["user_id"]),
                    text="The admin replied, but Telegram could not forward or copy that message type. Please contact the admin."
                )
            except TelegramError:
                pass


async def sold_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        if message:
            await message.reply_text("Use /sold as a reply to a listing message inside the configured listing group.")
        return
    if source_chat_id() != chat.id:
        await message.reply_text("This is not the configured listing group.")
        return
    if not await can_manage_source_records(update, context):
        await message.reply_text("Only a configured bot Admin or listing-group administrator can mark an account sold.")
        return
    target = message.reply_to_message
    if not target:
        await message.reply_text(
            "Reply directly to any message/photo in the listing (or album) and send /sold.\n"
            "The entire listing will disappear from /find and future alerts. Use /relist as a reply to restore it."
        )
        return
    item = database.set_listing_availability_by_source_message(chat.id, target.message_id, False)
    if not item:
        await message.reply_text("I couldn't find that post in the index. Index it first with /indexreply, then use /sold.")
        return
    await message.reply_text(
        f"✅ Marked sold/removed: {item.get('title') or 'Untitled listing'}\n"
        f"Source message: #{min(item.get('message_ids') or [target.message_id])}\n"
        "It is now excluded from /find results and new alert deliveries. The history is retained so it can be restored."
    )


async def relist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        if message:
            await message.reply_text("Use /relist as a reply to a listing message inside the configured listing group.")
        return
    if source_chat_id() != chat.id or not await can_manage_source_records(update, context):
        await message.reply_text("Only a configured bot Admin or administrator of the listing group can restore a listing.")
        return
    target = message.reply_to_message
    if not target:
        await message.reply_text("Reply to a previously sold/removed listing message with /relist.")
        return
    item = database.set_listing_availability_by_source_message(chat.id, target.message_id, True)
    if not item:
        await message.reply_text("That post was not found in the index. Use /indexreply first.")
        return
    await message.reply_text(
        f"✅ Restored to search: {item.get('title') or 'Untitled listing'}\n"
        "It can appear in /find results again."
    )


def configured_post_topic_id():
    value = database.get_setting("post_topic_id", "")
    try:
        topic_id = int(value) if value not in (None, "") else 0
        return topic_id if topic_id > 0 else None
    except (TypeError, ValueError):
        return None


async def setposttopic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Select a forum topic for future /add post publications, or show/reset it."""
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat:
        return
    user_id = update.effective_user.id if update.effective_user else 0
    if is_private(update):
        if not (is_superadmin_user_id(user_id) or app_role(user_id) == "ADMIN" or is_saved_owner(user_id)):
            await message.reply_text("🔒 Only configured bot Admins and the Superadmin can manage the publishing topic.")
            return
    elif chat.id != source_chat_id() or chat.type != ChatType.SUPERGROUP or not await can_manage_source_records(update, context):
        await message.reply_text("Run /setposttopic inside the configured listing supergroup as an administrator.")
        return

    args = list(getattr(context, "args", None) or [])
    if args and args[0].lower() in {"general", "default", "0", "off", "none"}:
        database.delete_setting("post_topic_id")
        await message.reply_text(
            "✅ Publishing destination reset to the General/default topic. Future /confirm posts will be sent there."
        )
        return

    topic_id = None
    if args:
        if len(args) != 1 or not args[0].isdigit() or int(args[0]) <= 0:
            await message.reply_text("Usage: /setposttopic — select this topic, /setposttopic 123456 — set topic ID, /setposttopic general — use General.")
            return
        topic_id = int(args[0])
    elif chat.type == ChatType.SUPERGROUP and chat.id == source_chat_id():
        reply = getattr(message, "reply_to_message", None)
        thread_id = getattr(message, "message_thread_id", None) or (getattr(reply, "message_thread_id", None) if reply else None)
        if not thread_id and reply and getattr(reply, "forum_topic_created", None):
            thread_id = reply.message_id
        try:
            topic_id = int(thread_id or 0)
        except (TypeError, ValueError):
            topic_id = 0
        if topic_id <= 0:
            await message.reply_text(
                "I couldn't detect a topic ID. Run this command inside the exact forum topic, "
                "or use /setposttopic 123456 with the real topic ID. Use /setposttopic general for the General topic."
            )
            return
    else:
        current = configured_post_topic_id()
        await message.reply_text(
            "📌 PUBLISHING TOPIC SETTINGS\n\n"
            + (f"Selected forum topic ID: {current}\nFuture /confirm posts go into that topic."
               if current else "No topic is selected. Future /confirm posts go into the General/default topic.")
            + "\n\nUse /setposttopic general to reset to General, or run /setposttopic inside the desired forum topic."
        )
        return

    try:
        source_chat = await context.bot.get_chat(source_chat_id())
    except TelegramError as exc:
        await message.reply_text(f"I couldn't verify the configured source group: {type(exc).__name__}. No topic setting was changed.")
        return
    if getattr(source_chat, "type", None) != ChatType.SUPERGROUP or not getattr(source_chat, "is_forum", False):
        await message.reply_text("The configured listing destination is not a forum-enabled supergroup. Topic routing cannot be configured.")
        return

    database.set_setting("post_topic_id", topic_id)
    await message.reply_text(
        f"✅ PUBLISHING TOPIC SAVED\n\nTopic ID: {topic_id}\n"
        "Future posts created with /add post will be published into this forum topic after /confirm. "
        "Existing posts are not moved."
    )


async def topicid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnose what Telegram identifies as the current forum topic."""
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat:
        return
    if source_chat_id() != chat.id:
        await message.reply_text("Run /topicid inside the configured listing/source group.")
        return
    if not await can_manage_source_records(update, context):
        await message.reply_text("Only a configured bot Admin or source-group administrator can inspect topic labels.")
        return
    thread_id = getattr(message, "message_thread_id", None)
    reply = getattr(message, "reply_to_message", None)
    reply_thread_id = getattr(reply, "message_thread_id", None) if reply else None
    effective_thread = thread_id or reply_thread_id
    if not effective_thread and reply and getattr(reply, "forum_topic_created", None):
        effective_thread = reply.message_id
    await message.reply_text(
        "Topic diagnostic\n"
        f"chat_id: {chat.id}\n"
        f"message_id: {message.message_id}\n"
        f"message_thread_id: {thread_id!r}\n"
        f"reply_to_message_thread_id: {reply_thread_id!r}\n"
        f"resolved_topic_id: {effective_thread!r}\n\n"
        "If resolved_topic_id is a positive number, register the actual topic title with "
        f"/settopicname {effective_thread} Accounts 0-500 (replace both the number and example title).\n"
        "You may also reply to a message inside that topic with /settopicname Accounts 0-500."
    )


async def settopicname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat = update.effective_message, update.effective_chat
    if not message or not chat or source_chat_id() != chat.id:
        if message:
            await message.reply_text("Use this command inside the configured listing/source group.")
        return
    if not await can_manage_source_records(update, context):
        await message.reply_text("Only a configured bot Admin or source-group administrator can set a topic label.")
        return

    args = list(context.args or [])
    reply = getattr(message, "reply_to_message", None)
    thread_id = getattr(message, "message_thread_id", None)
    reply_thread_id = getattr(reply, "message_thread_id", None) if reply else None
    topic_id = int(thread_id or reply_thread_id or 0)
    # Explicit syntax works when Telegram does not attach message_thread_id to a command.
    if len(args) >= 2 and args[0].isdigit():
        topic_id = int(args.pop(0))
    title = " ".join(args).strip()

    if not title:
        await message.reply_text(
            "Enter the topic title after the command.\n"
            "Example inside Accounts 0-500: /settopicname Accounts 0-500\n"
            "Or with a topic ID: /settopicname 123456 Accounts 0-500"
        )
        return
    if topic_id <= 0 and reply:
        # If replying to the actual topic's first/service message, its message ID can be the thread ID.
        if getattr(reply, "forum_topic_created", None):
            topic_id = int(reply.message_id)
    if topic_id <= 0:
        await message.reply_text(
            "❌ I received the title but Telegram did not include a topic ID. Nothing was saved.\n\n"
            "Please reply to a real listing message inside the intended topic with this command:\n"
            f"/settopicname {title}\n\n"
            "If that still fails, run /topicid as a reply to the listing and use the resolved_topic_id it reports:\n"
            f"/settopicname 123456 {title}\n"
            "Replace 123456 with the actual number; do not guess it."
        )
        return

    database.set_topic_title(topic_id, title)
    await message.reply_text(
        f"✅ SAVED TOPIC LABEL\n\nTopic title: {title}\nTopic ID: {topic_id}\n\n"
        "Existing indexed listings in this topic have been refreshed. New listings will use this topic label first when detecting the price band."
    )



async def alertme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not is_private(update):
        await message.reply_text("Please create and manage saved alerts in my private chat.")
        return
    user_id = update.effective_user.id
    query = " ".join(context.args).strip()
    if not query:
        await myalerts(update, context)
        return
    parsed = parse_query(query)
    if not parsed["terms"] and all(parsed["options"].get(k) is None
                                  for k in ("price_min", "price_max", "kingdom_min", "kingdom_max", "posted_within_hours")):
        await message.reply_text("Please include at least one search keyword or explicit filter.")
        return
    active = database.count_user_alerts(user_id)
    if active >= config.MAX_ALERTS_PER_USER:
        await message.reply_text(
            f"You already have {active} active alerts. Maximum: {config.MAX_ALERTS_PER_USER}.\n"
            "Use /cancelalert <number> before adding another."
        )
        return
    alert_id = database.add_alert(user_id, query)
    await message.reply_text(
        f"✅ Alert saved as #{alert_id}. I will notify you when a new listing matches:\n{query}\n\n"
        "Keep this bot running for new posts to be processed. You can view alerts with /myalerts."
    )


async def myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not is_private(update):
        await message.reply_text("Please manage alerts in my private chat.")
        return
    alerts = database.get_user_alerts(update.effective_user.id)
    if not alerts:
        await message.reply_text(
            "You have no active alerts.\nCreate one with /alertme K1700 5champ."
        )
        return
    lines = ["Your active alerts:"]
    for pos, alert in enumerate(alerts, 1):
        lines.append(f"{pos}. {alert['query']}")
    lines.append("\nCancel one with /cancelalert <number>.")
    await message.reply_text("\n".join(lines))


async def cancelalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not is_private(update):
        await message.reply_text("Please manage alerts in my private chat.")
        return
    if not context.args or not context.args[0].isdigit():
        await message.reply_text("Usage: /cancelalert <alert number>")
        return
    ok = database.delete_alert_by_position(update.effective_user.id, int(context.args[0]))
    await message.reply_text("✅ Alert cancelled." if ok else "That alert number does not exist.")


async def check_alerts_for_listing(application: Application, item: dict):
    if not item or not int(item.get("is_available", 1)):
        return
    for alert in database.get_active_alerts():
        if not evaluate_for_alert(item, alert["query"]):
            continue
        user_id = int(alert["user_id"])
        alert_id = int(alert["id"])
        listing_key = str(item["listing_key"])
        # Claim the alert/listing pair before sending so concurrent album/edit
        # events cannot create duplicate DMs. Failed sends release the claim for retry.
        if not database.claim_alert_delivery(alert_id, listing_key):
            continue
        buy_token = ""
        notify_token = ""
        try:
            text = (
                f"{config.ALERT_PREFIX}\n\n"
                f"{item.get('title') or 'Untitled listing'}\n"
                f"Your filter: {alert['query']}\n"
                f"Readable text indexed: {'yes' if item.get('ocr_text') else 'no photo text extracted'}\n"
                f"Amount: {amount_summary(item)}"
            )
            if item.get("link"):
                text += f"\nSource post: {item['link']}"
            buy_token = make_action_token(user_id, "buy_listing", query=alert["query"], listing_key=listing_key)
            notify_token = make_action_token(user_id, "notify_listing", query=alert["query"], listing_key=listing_key)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(config.BUY_BUTTON_TEXT, callback_data=f"act:{buy_token}")],
                [InlineKeyboardButton(config.NOTIFY_BUTTON_TEXT, callback_data=f"act:{notify_token}")],
            ])
            await send_listing_to_user(
                application.bot, user_id, item, summary=text, reply_markup=keyboard
            )
            database.mark_alert_delivered(alert_id, listing_key)
        except TelegramError as exc:
            if buy_token:
                database.delete_callback_action(buy_token)
            if notify_token:
                database.delete_callback_action(notify_token)
            database.mark_alert_delivery_failed(alert_id, listing_key, str(exc))
            log.warning("Alert delivery to %s failed. It can be retried: %s", user_id, exc)
        except Exception as exc:
            if buy_token:
                database.delete_callback_action(buy_token)
            if notify_token:
                database.delete_callback_action(notify_token)
            database.mark_alert_delivery_failed(alert_id, listing_key, str(exc))
            log.exception("Unexpected alert delivery failure for user %s", user_id)


def evaluate_for_alert(item, query):
    return evaluate_listing(item, query, for_alert=True) is not None


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        await update.effective_message.reply_text("Open my private chat to see bot status.")
        return
    status = "configured" if source_chat_id() is not None else "not configured"
    ocr_note = "available" if pytesseract is not None and Image is not None else "not installed"
    await update.effective_message.reply_text(
        f"Source group: {status}\n"
        f"Indexed listings: {database.count_listings()}\n"
        f"Listings with readable photo text: {database.count_ocr_listings()}\n"
        f"Photo OCR: {ocr_note}\n"
        f"Your active alerts: {database.count_user_alerts(update.effective_user.id)}"
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled update error", exc_info=context.error)




# --- Admin-created listing workflow (/add post) ---
ACCOUNT_FORM_FIELDS = [
    ("account_id", "Account/listing number", True, "9814"),
    ("kingdom", "Current kingdom / migration", False, "K1950 or ✈️ Can go to K1950"),
    ("account_type", "Account type", False, "Rally lead / solo trap / filler / leave blank"),
    ("might", "Might", True, "3.4K+mm"),
    ("stats", "Base stats (three values)", True, "1132-1137-1135%"),
    ("price", "Price and currency", False, "$350 / USD 350 / ₹30000 / 350 + MM"),
    ("artifact_blessed", "Artifact stars and blessed count", False, "1100 artifact stars, 30 blessed"),
    ("champions_cups", "Champions and cups", False, "5+1 champs, 2 cups"),
    ("castle_mana", "Castle stars and mana troops", False, "29 castle stars, 3 mana troops"),
    ("familiars_binding", "Familiars and account binding", False, "8 fams, IGG linked"),
    ("extra", "Other searchable features / notes", False, "relink, direct, 11k, restricted, etc."),
]


def _account_field_prompt(index):
    key, label, required, example = ACCOUNT_FORM_FIELDS[index]
    requirement = "Required" if required else "Optional — type skip to leave blank"
    return (
        f"🧾 ACCOUNT DETAILS ({index + 1}/{len(ACCOUNT_FORM_FIELDS)})\n\n"
        f"{label}\n{requirement}\n\n"
        f"Example: {example}\n\n"
        "Reply with this field only. Type back to change the previous field."
    )


def _build_account_caption(fields, user):
    """Build the buyer-facing caption from supplied form values only."""
    account_id = str(fields.get("account_id") or "").strip().lstrip("#")
    first = f"#{account_id}"
    kingdom = str(fields.get("kingdom") or "").strip()
    might = str(fields.get("might") or "").strip()
    if kingdom:
        if re.search(r"\bcan\s+go\s+to\b", kingdom, re.I) or "✈" in kingdom:
            if "✈" not in kingdom:
                kingdom = "✈️ " + kingdom
            first += f" | {kingdom}"
        else:
            first += f" | Kingdom: {kingdom}"
    if might:
        first += f" | {might}"

    lines = [first]
    line_specs = [
        ("account_type", "Type: "),
        ("stats", "Stats: "),
        ("price", "Price: "),
        ("artifact_blessed", ""),
        ("champions_cups", ""),
        ("castle_mana", ""),
        ("familiars_binding", ""),
        ("extra", ""),
    ]
    for key, prefix in line_specs:
        value = str(fields.get(key) or "").strip()
        if value:
            lines.append(prefix + value)

    username = str(getattr(user, "username", "") or "").strip().lstrip("@")
    contact = f"@{username}" if username else str(getattr(user, "id", "") or "")
    if contact:
        lines.append(f"My Telegram id: {contact}")
    return "\n".join(lines)


def _account_field_error(key, value):
    value = str(value or "").strip()
    if not value:
        return "Please enter a value, or type skip for an optional field."
    if key == "account_id" and not re.fullmatch(r"#?[A-Za-z0-9_-]{2,30}", value):
        return "Enter the account/listing number, for example 9814."
    if key == "might" and not re.search(r"\d", value):
        return "Might must include the stated number, for example 3.4K+mm."
    if key == "stats":
        pattern = r"^\s*\d+(?:\.\d+)?\s*[-/]\s*\d+(?:\.\d+)?\s*[-/]\s*\d+(?:\.\d+)?\s*%?\s*$"
        if not re.fullmatch(pattern, value):
            return "Enter the three base-stat values, for example 1132-1137-1135%."
    return ""


async def _advance_account_form(update, context, draft, *, index=None):
    if index is not None:
        draft["field_index"] = int(index)
    current = int(draft.get("field_index", 0))
    if current >= len(ACCOUNT_FORM_FIELDS):
        draft["caption"] = _build_account_caption(draft.get("fields") or {}, update.effective_user)
        if not _caption_fits_telegram(draft["caption"]):
            draft["stage"] = "details"
            draft["field_index"] = len(ACCOUNT_FORM_FIELDS) - 1
            _save_admin_post_draft(update, context, draft)
            await update.effective_message.reply_text(
                "The generated caption is too long for Telegram. Shorten the last optional details field and continue."
            )
            return
        draft["stage"] = "preview_pending"
        _save_admin_post_draft(update, context, draft)
        try:
            await _send_admin_post_preview(context, update.effective_chat.id, draft, "🧾 GENERATED CAPTION PREVIEW")
            draft["stage"] = "preview"
            _save_admin_post_draft(update, context, draft)
        except Exception as exc:
            log.exception("Could not send structured account preview")
            await update.effective_message.reply_text(
                f"⚠️ Caption saved, but preview delivery failed: {exc}. Send /preview to retry."
            )
        return
    _save_admin_post_draft(update, context, draft)
    await update.effective_message.reply_text(_account_field_prompt(current))


async def add_post_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, user = update.effective_message, update.effective_user
    if not message or not user:
        return
    if not is_private(update):
        await message.reply_text("Please continue the listing form in the bot's private chat.")
        return
    if not await can_create_listing(update, context):
        await message.reply_text("⛔ An approved Admin or Merchant account is required.")
        return
    async with _post_workflow_lock(context, user.id):
        draft = _load_admin_post_draft(update, context)
        if not draft or draft.get("stage") != "details":
            await message.reply_text("There is no optional account field waiting to be skipped.")
            return
        index = int(draft.get("field_index", 0))
        key, label, required, _ = ACCOUNT_FORM_FIELDS[index]
        if required:
            await message.reply_text(f"{label} is required; please enter it instead of skipping.")
            return
        draft.setdefault("fields", {})[key] = ""
        await _advance_account_form(update, context, draft, index=index + 1)


async def add_post_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, user = update.effective_message, update.effective_user
    if not message or not user:
        return
    if not is_private(update):
        await message.reply_text("Please continue the listing form in the bot's private chat.")
        return
    if not await can_create_listing(update, context):
        await message.reply_text("⛔ An approved Admin or Merchant account is required.")
        return
    async with _post_workflow_lock(context, user.id):
        draft = _load_admin_post_draft(update, context)
        if not draft or draft.get("stage") != "details":
            await message.reply_text("There is no account field to go back to.")
            return
        index = int(draft.get("field_index", 0))
        if index <= 0:
            await message.reply_text("You are already at the first account field.")
            return
        previous = index - 1
        key = ACCOUNT_FORM_FIELDS[previous][0]
        draft.setdefault("fields", {}).pop(key, None)
        await _advance_account_form(update, context, draft, index=previous)


def _caption_fits_telegram(caption):
    """Be conservative with Telegram's 1,024 UTF-16-unit media-caption limit."""
    try:
        return len(str(caption).encode("utf-16-le")) // 2 <= 1024
    except UnicodeEncodeError:
        return False


def _post_workflow_lock(context, user_id):
    """Serialize one user's draft transitions even when concurrent updates are enabled."""
    application = getattr(context, "application", None)
    if application is None:
        return asyncio.Lock()
    locks = application.bot_data.setdefault("admin_post_locks", {})
    return locks.setdefault(int(user_id), asyncio.Lock())


def _load_admin_post_draft(update, context):
    user = update.effective_user
    if not user:
        return None
    draft = context.user_data.get("new_listing")
    if draft is None:
        draft = database.get_admin_post_draft(user.id)
        if draft is not None:
            context.user_data["new_listing"] = draft
    return draft


def _save_admin_post_draft(update, context, draft):
    user = update.effective_user
    if not user:
        raise RuntimeError("The Telegram user could not be identified for this draft.")
    context.user_data["new_listing"] = draft
    database.save_admin_post_draft(user.id, draft)


def _delete_admin_post_draft(update, context):
    user = update.effective_user
    if user:
        context.user_data.pop("new_listing", None)
        database.delete_admin_post_draft(user.id)


def _serialize_posted_message(message):
    """Persist the minimum Bot API message fields needed for safe index retries."""
    chat = getattr(message, "chat", None)
    if chat is None:
        raise RuntimeError("Telegram returned a published message without chat metadata.")

    def person_data(person):
        if person is None:
            return None
        return {
            "id": int(getattr(person, "id", 0) or 0),
            "first_name": getattr(person, "first_name", "") or "",
            "last_name": getattr(person, "last_name", "") or "",
            "username": getattr(person, "username", "") or "",
            "is_bot": bool(getattr(person, "is_bot", False)),
            "title": getattr(person, "title", "") or "",
        }

    photos = []
    for photo in (getattr(message, "photo", None) or []):
        photos.append({
            "file_id": getattr(photo, "file_id", ""),
            "file_unique_id": getattr(photo, "file_unique_id", ""),
            "width": int(getattr(photo, "width", 0) or 0),
            "height": int(getattr(photo, "height", 0) or 0),
            "file_size": int(getattr(photo, "file_size", 0) or 0),
        })
    sent_date = getattr(message, "date", None) or datetime.now(timezone.utc)
    return {
        "chat": {
            "id": int(chat.id),
            "type": str(getattr(chat, "type", "")),
            "username": getattr(chat, "username", "") or "",
            "title": getattr(chat, "title", "") or "",
        },
        "message_id": int(message.message_id),
        "caption": getattr(message, "caption", None) or "",
        "text": getattr(message, "text", None) or "",
        "media_group_id": getattr(message, "media_group_id", None) or "",
        "message_thread_id": int(getattr(message, "message_thread_id", 0) or 0),
        "date": sent_date.isoformat(),
        "photo": photos,
        "from_user": person_data(getattr(message, "from_user", None)),
        "sender_chat": person_data(getattr(message, "sender_chat", None)),
    }


def _restore_posted_message(data):
    """Rebuild the small Message-like object index_message needs from saved metadata."""
    chat = SimpleNamespace(**(data.get("chat") or {}))
    photo = [SimpleNamespace(**item) for item in data.get("photo", [])]
    from_user_data = data.get("from_user")
    sender_chat_data = data.get("sender_chat")
    return SimpleNamespace(
        chat=chat,
        message_id=int(data["message_id"]),
        caption=data.get("caption", ""),
        text=data.get("text", ""),
        media_group_id=data.get("media_group_id") or None,
        message_thread_id=int(data.get("message_thread_id", 0) or 0),
        date=datetime.fromisoformat(data["date"]),
        photo=photo,
        from_user=SimpleNamespace(**from_user_data) if from_user_data else None,
        sender_chat=SimpleNamespace(**sender_chat_data) if sender_chat_data else None,
    )


async def _send_admin_post_preview(context, chat_id, draft, heading="👀 POST PREVIEW"):
    """Show the actual image set and caption to the admin before publication."""
    from telegram import InputMediaPhoto

    photos = list(draft.get("photos") or [])
    caption = str(draft.get("caption") or "")
    if not photos:
        raise RuntimeError("A post preview requires at least one screenshot.")
    if len(photos) == 1:
        await context.bot.send_photo(chat_id=chat_id, photo=photos[0], caption=caption)
    else:
        media = [
            InputMediaPhoto(media=file_id, caption=caption if index == 0 else None)
            for index, file_id in enumerate(photos)
        ]
        await context.bot.send_media_group(chat_id=chat_id, media=media)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"{heading}\n\n"
            "The screenshots and caption above are the exact post preview.\n"
            "Edit: /editcaption followed by the complete caption (line breaks are preserved).\n"
            "Publish: /confirm\n"
            "Discard: /cancel"
        ),
    )


async def _index_published_admin_post(context, draft):
    snapshots = draft.get("published_messages") or []
    if not snapshots:
        raise RuntimeError("Telegram confirmed publication, but no message metadata was saved for indexing.")
    indexed = []
    for snapshot in snapshots:
        message = _restore_posted_message(snapshot)
        listing_key = await index_message(message, context, allow_bot_post=True)
        if not listing_key:
            raise RuntimeError(
                f"Published message {message.message_id} was not accepted by the configured source index."
            )
        indexed.append(listing_key)
    return indexed


def _freeze_admin_post_destinations(draft, primary_chat_id):
    """Snapshot extra destinations when the creator first approves the post."""
    if "extra_destinations" in draft:
        return
    extras = []
    for item in database.list_publish_destinations():
        chat_id = int(item["chat_id"])
        if chat_id == int(primary_chat_id):
            continue
        extras.append({
            "chat_id": chat_id,
            "title": str(item.get("title") or "Telegram chat"),
            "username": str(item.get("username") or ""),
        })
    draft["extra_destinations"] = extras
    draft["extra_delivery_status"] = {
        str(item["chat_id"]): {"status": "pending"} for item in extras
    }


async def _deliver_admin_post_to_extra_destinations(update, context, draft):
    """Copy the already-published primary listing to each additional destination once."""
    snapshots = draft.get("published_messages") or []
    if not snapshots:
        return "uncertain", "The primary post metadata is missing; additional destinations were not attempted."
    primary_chat_id = int(draft.get("published_chat_id") or snapshots[0]["chat"]["id"])
    source_message_ids = [int(item["message_id"]) for item in snapshots]
    states = draft.setdefault("extra_delivery_status", {})

    for destination in draft.get("extra_destinations") or []:
        destination_id = int(destination["chat_id"])
        state = states.setdefault(str(destination_id), {"status": "pending"})
        current = str(state.get("status") or "pending")
        if current in {"sent", "uncertain"}:
            continue
        if current == "sending":
            # The process may have stopped after Telegram accepted the copy. Never duplicate it.
            state.update(status="uncertain", error="Process stopped during send; check destination manually.")
            _save_admin_post_draft(update, context, draft)
            continue

        state.update(status="sending", error="")
        _save_admin_post_draft(update, context, draft)
        try:
            copied = await context.bot.copy_messages(
                chat_id=destination_id,
                from_chat_id=primary_chat_id,
                message_ids=source_message_ids,
                disable_notification=False,
            )
            copied_ids = [mid for obj in (copied or []) if (mid := _copied_message_id(obj))]
            if len(copied_ids) != len(source_message_ids):
                state.update(
                    status="uncertain",
                    error=f"Telegram returned {len(copied_ids)} copied message IDs for {len(source_message_ids)} source messages.",
                    copied_message_ids=copied_ids,
                )
            else:
                state.update(status="sent", copied_message_ids=copied_ids, error="")
        except (BadRequest, Forbidden) as exc:
            # A definite API rejection is safe to retry after permissions/configuration are fixed.
            state.update(status="failed", error=str(exc)[:500])
            log.warning("Additional listing destination %s rejected a copy: %s", destination_id, exc)
        except Exception as exc:
            # A timeout/connection error may occur after Telegram accepted the copy.
            state.update(status="uncertain", error=str(exc)[:500])
            log.exception("Additional listing destination %s has uncertain delivery status", destination_id)
        _save_admin_post_draft(update, context, draft)

    values = [str(state.get("status") or "pending") for state in states.values()]
    if any(value == "uncertain" for value in values):
        draft["stage"] = "delivery_uncertain"
        _save_admin_post_draft(update, context, draft)
        return "uncertain", (
            "The primary listing is published and indexed, but at least one extra destination has uncertain delivery. "
            "Check those destinations manually. I will not resend uncertain copies. /confirm can retry only definite failures."
        )
    if any(value in {"failed", "pending", "sending"} for value in values):
        draft["stage"] = "delivery_partial"
        _save_admin_post_draft(update, context, draft)
        return "failed", (
            "The primary listing is published and indexed, but one or more extra destinations failed. "
            "Fix the destination's bot permissions, then send /confirm to retry only failed destinations."
        )
    return "sent", ""


async def add_post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not await can_create_listing(update, context):
        await message.reply_text("⛔ An approved Admin or Merchant account is required to create listings.")
        return
    if not is_private(update):
        await message.reply_text("Please use /add post in the bot's private chat.")
        return
    args = [x.lower() for x in (context.args or [])]
    if args and args[0] != "post":
        await message.reply_text("Usage: /add post")
        return

    async with _post_workflow_lock(context, user.id):
        existing = _load_admin_post_draft(update, context)
        if existing:
            stage = existing.get("stage", "")
            if stage in {"published", "index_pending", "publishing", "delivery_uncertain", "delivery_partial"}:
                await message.reply_text(
                    "⚠️ Your previous post is not marked as a normal draft. Use /confirm to retry indexing if it was published, "
                    "or check the destination before taking any further action. I will not replace it or risk a duplicate."
                )
            else:
                await message.reply_text(
                    "You already have an unfinished post draft. Continue the current steps or send /cancel before starting another."
                )
            return
        draft = {"photos": [], "stage": "photos"}
        _save_admin_post_draft(update, context, draft)
        await message.reply_text(
            "📸 CREATE A NEW LISTING\n\n"
            "Send the account screenshots here as Photos (not Files), one or more. When finished, send /done.\n"
            "You can send up to 10 screenshots per post. After /done, I will collect the searchable account fields and generate the caption."
        )


async def add_post_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not await can_create_listing(update, context):
        await message.reply_text("⛔ An approved Admin or Merchant account is required.")
        return
    async with _post_workflow_lock(context, user.id):
        draft = _load_admin_post_draft(update, context)
        if not draft or draft.get("stage") != "photos":
            return
        if not message.photo:
            return
        photos = draft.setdefault("photos", [])
        if len(photos) >= 10:
            await message.reply_text("Maximum 10 screenshots per post. Send /done to continue.")
            return
        photos.append(message.photo[-1].file_id)
        _save_admin_post_draft(update, context, draft)
        await message.reply_text(
            f"✅ Screenshot {len(photos)} added. Send more, or /done when finished."
        )


async def add_post_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not is_private(update):
        await message.reply_text("Please continue the post-creation workflow in the bot's private chat.")
        return
    if not await can_create_listing(update, context):
        await message.reply_text("⛔ An approved Admin or Merchant account is required.")
        return
    async with _post_workflow_lock(context, user.id):
        draft = _load_admin_post_draft(update, context)
        if not draft or draft.get("stage") != "photos":
            await message.reply_text("Start first with /add post, or continue your existing draft.")
            return
        if not draft.get("photos"):
            await message.reply_text("Please upload at least one screenshot before /done.")
            return
        draft["stage"] = "details"
        draft["field_index"] = 0
        draft["fields"] = {}
        _save_admin_post_draft(update, context, draft)
        await message.reply_text(
            "📝 I will collect the searchable account fields one by one. Account number, might and all three base stats are required.\n"
            "Optional fields can be skipped. I will generate the caption from your answers, and you can edit it before publishing."
        )
        await message.reply_text(_account_field_prompt(0))


async def add_post_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    async with _post_workflow_lock(context, user.id):
        draft = _load_admin_post_draft(update, context)
        if not draft or draft.get("stage") != "details":
            return
        if not await can_create_listing(update, context):
            await message.reply_text("⛔ An approved Admin or Merchant account is required. Your draft has not been changed.")
            return
        value = (message.text or "").strip()
        if not value:
            await message.reply_text("Please enter this field, or type skip for an optional field.")
            return
        index = int(draft.get("field_index", 0))
        if index < 0 or index >= len(ACCOUNT_FORM_FIELDS):
            draft["field_index"] = 0
            draft["fields"] = {}
            _save_admin_post_draft(update, context, draft)
            await message.reply_text("The saved account form was out of step. I restarted it at the account number field.")
            await message.reply_text(_account_field_prompt(0))
            return
        field_key, label, required, _ = ACCOUNT_FORM_FIELDS[index]
        if value.lower() in {"back", "/back"}:
            if index <= 0:
                await message.reply_text("You are already at the first account field.")
                return
            previous = index - 1
            draft.setdefault("fields", {}).pop(ACCOUNT_FORM_FIELDS[previous][0], None)
            await _advance_account_form(update, context, draft, index=previous)
            return
        if value.lower() in {"skip", "/skip", "-", "n/a", "na"}:
            if required:
                await message.reply_text(f"{label} is required. Please enter it.")
                return
            draft.setdefault("fields", {})[field_key] = ""
            await _advance_account_form(update, context, draft, index=index + 1)
            return
        validation_error = _account_field_error(field_key, value)
        if validation_error:
            await message.reply_text(validation_error)
            return
        draft.setdefault("fields", {})[field_key] = value
        await _advance_account_form(update, context, draft, index=index + 1)


async def add_post_edit_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not is_private(update):
        await message.reply_text("Please continue the post-creation workflow in the bot's private chat.")
        return
    raw_text = message.text or ""
    match = re.match(r"^/editcaption(?:@[A-Za-z0-9_]+)?\s+(.*)$", raw_text, flags=re.IGNORECASE | re.DOTALL)
    new_caption = (match.group(1) if match else " ".join(context.args or "")).strip()
    async with _post_workflow_lock(context, user.id):
        draft = _load_admin_post_draft(update, context)
        if not draft or draft.get("stage") != "preview":
            await message.reply_text("There is no caption waiting for editing.")
            return
        if not await can_create_listing(update, context):
            await message.reply_text("⛔ An approved Admin or Merchant account is required. Your draft has not been changed.")
            return
        if not new_caption:
            await message.reply_text("Usage: /editcaption Your complete new caption")
            return
        if not _caption_fits_telegram(new_caption):
            await message.reply_text(
                "Telegram media captions are limited to 1024 characters. Shorten it and try again."
            )
            return
        draft["caption"] = new_caption
        draft["stage"] = "preview_pending"
        _save_admin_post_draft(update, context, draft)
        try:
            await _send_admin_post_preview(context, update.effective_chat.id, draft, "✏️ UPDATED POST PREVIEW")
            draft["stage"] = "preview"
            _save_admin_post_draft(update, context, draft)
        except Exception as exc:
            log.exception("Could not refresh admin listing preview")
            await message.reply_text(
                f"⚠️ Caption saved, but I could not refresh the screenshot preview: {exc}\n"
                "Publishing stays locked until you receive the updated preview. Send /preview to try again."
            )


async def add_post_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retry delivery of a saved preview; confirmation stays locked until delivery succeeds."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not is_private(update):
        await message.reply_text("Please continue the post-creation workflow in the bot's private chat.")
        return
    if not await can_create_listing(update, context):
        await message.reply_text("⛔ An approved Admin or Merchant account is required.")
        return
    async with _post_workflow_lock(context, user.id):
        draft = _load_admin_post_draft(update, context)
        if not draft or draft.get("stage") not in {"preview_pending", "preview"}:
            await message.reply_text("There is no saved post preview to show. Complete screenshots and caption first.")
            return
        try:
            await _send_admin_post_preview(context, update.effective_chat.id, draft, "👀 POST PREVIEW")
            draft["stage"] = "preview"
            _save_admin_post_draft(update, context, draft)
            await message.reply_text("✅ Preview delivered. Review it, then send /confirm to publish or /editcaption to change it.")
        except Exception as exc:
            draft["stage"] = "preview_pending"
            _save_admin_post_draft(update, context, draft)
            log.exception("Could not resend admin listing preview")
            await message.reply_text(f"⚠️ Preview could not be delivered: {exc}. Publishing remains locked; try /preview again.")


async def add_post_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not is_private(update):
        await message.reply_text("Please continue the post-creation workflow in the bot's private chat.")
        return
    if not await can_create_listing(update, context):
        await message.reply_text("⛔ An approved Admin or Merchant account is required.")
        return

    async with _post_workflow_lock(context, user.id):
        draft = _load_admin_post_draft(update, context)
        if not draft:
            await message.reply_text("No listing draft is waiting for confirmation.")
            return

        stage = draft.get("stage", "")
        if stage in {"published", "index_pending", "delivery_partial", "delivery_uncertain"} and draft.get("published_messages"):
            # Primary publication is already confirmed. Retry indexing and definite
            # destination failures only; sent/uncertain destinations are never copied again.
            if not draft.get("primary_indexed"):
                try:
                    await _index_published_admin_post(context, draft)
                    draft["primary_indexed"] = True
                    draft["stage"] = "published"
                    _save_admin_post_draft(update, context, draft)
                except Exception as exc:
                    draft["stage"] = "index_pending"
                    draft["index_error"] = str(exc)[:500]
                    _save_admin_post_draft(update, context, draft)
                    log.exception("Retrying index for an already-published admin post failed")
                    await message.reply_text(
                        "⚠️ The primary post is already live, but indexing is still pending. "
                        "Send /confirm again to retry indexing; no destination will be reposted."
                    )
                    return
            delivery_status, delivery_message = await _deliver_admin_post_to_extra_destinations(update, context, draft)
            if delivery_status != "sent":
                await message.reply_text("⚠️ " + delivery_message)
                return
            _delete_admin_post_draft(update, context)
            await message.reply_text(
                "✅ The already-published post is indexed and all configured destinations are complete. "
                "No second post was sent to the primary destination."
            )
            return
        if stage in {"publishing", "delivery_uncertain"}:
            await message.reply_text(
                "⚠️ I cannot verify whether Telegram accepted the previous publish attempt. "
                "Check the configured destination before doing anything else. To prevent duplicate listings, "
                "I will not automatically send it again."
            )
            return
        if stage == "preview_pending":
            await message.reply_text("The preview has not been delivered yet. Send /preview and review it before /confirm.")
            return
        if stage != "preview":
            await message.reply_text("Complete the screenshots and receive the caption preview before sending /confirm.")
            return

        target_id = source_chat_id()
        if not target_id:
            await message.reply_text(
                "⚠️ No listing destination is configured. Ask the Superadmin to configure it with /setsource, then retry /confirm."
            )
            return
        photos = list(draft.get("photos") or [])
        caption = str(draft.get("caption") or "")
        if not photos or not caption:
            await message.reply_text("⚠️ The draft is incomplete. Upload screenshots and add a caption before confirming.")
            return
        if len(photos) > 10 or not _caption_fits_telegram(caption):
            await message.reply_text("⚠️ This draft exceeds Telegram's media/caption limits. Edit it before publishing.")
            return

        _freeze_admin_post_destinations(draft, target_id)
        draft["stage"] = "publishing"
        draft["publish_started_at"] = now_iso()
        _save_admin_post_draft(update, context, draft)
        try:
            topic_id = configured_post_topic_id()
            topic_kwargs = {"message_thread_id": topic_id} if topic_id else {}
            if len(photos) == 1:
                published = [await context.bot.send_photo(
                    chat_id=target_id, photo=photos[0], caption=caption, **topic_kwargs
                )]
            else:
                from telegram import InputMediaPhoto
                media = [
                    InputMediaPhoto(media=file_id, caption=caption if index == 0 else None)
                    for index, file_id in enumerate(photos)
                ]
                published = await context.bot.send_media_group(
                    chat_id=target_id, media=media, **topic_kwargs
                )
            if not published:
                raise RuntimeError("Telegram returned an empty publication result.")
        except (BadRequest, Forbidden) as exc:
            # Telegram returned a definitive API rejection, so no post was accepted.
            draft["stage"] = "preview"
            draft["last_error"] = str(exc)[:500]
            _save_admin_post_draft(update, context, draft)
            log.warning("Telegram rejected admin-created post: %s", exc)
            await message.reply_text(
                f"❌ Telegram rejected the post: {exc}\n"
                "Your draft is preserved. Correct the destination/permissions and send /confirm again."
            )
            return
        except Exception as exc:
            # A timeout/connection failure can happen after Telegram accepted the send.
            # Do not risk duplicate publication by retrying an ambiguous send.
            draft["stage"] = "delivery_uncertain"
            draft["last_error"] = str(exc)[:500]
            _save_admin_post_draft(update, context, draft)
            log.exception("Admin-created post delivery status is uncertain")
            await message.reply_text(
                "⚠️ I could not verify Telegram's delivery response. The post may have been sent. "
                "Check the configured destination before taking further action. I will not resend automatically, "
                "to avoid creating a duplicate."
            )
            return

        # Persist Telegram's accepted result BEFORE indexing. A later indexing failure
        # can then be retried without ever sending a second copy of the post.
        try:
            snapshots = [_serialize_posted_message(sent) for sent in published]
        except Exception as exc:
            draft["stage"] = "published"
            draft["index_error"] = f"Could not save Telegram message metadata: {exc}"[:500]
            _save_admin_post_draft(update, context, draft)
            log.exception("Published post metadata serialization failed")
            await message.reply_text(
                "✅ Telegram accepted the post, but I could not save its indexing metadata. "
                "It will not be sent again. Contact the Superadmin for manual index recovery."
            )
            return

        draft["stage"] = "published"
        draft["published_messages"] = snapshots
        draft["published_chat_id"] = int(target_id)
        draft["published_message_ids"] = [item["message_id"] for item in snapshots]
        _save_admin_post_draft(update, context, draft)

        try:
            await _index_published_admin_post(context, draft)
            draft["primary_indexed"] = True
            _save_admin_post_draft(update, context, draft)
        except Exception as exc:
            draft["stage"] = "index_pending"
            draft["index_error"] = str(exc)[:500]
            _save_admin_post_draft(update, context, draft)
            log.exception("Post published, but local indexing failed")
            await message.reply_text(
                "✅ The post is live in Telegram, but local indexing did not finish. "
                "Send /confirm again to retry indexing only; it will NOT publish a second copy."
            )
            return

        delivery_status, delivery_message = await _deliver_admin_post_to_extra_destinations(update, context, draft)
        if delivery_status != "sent":
            await message.reply_text("⚠️ " + delivery_message)
            return

        first = published[0]
        chat_username = getattr(first.chat, "username", None)
        if chat_username:
            link = f"https://t.me/{chat_username}/{first.message_id}"
        else:
            link = database.make_message_link(first.chat.id, first.message_id)
        _delete_admin_post_draft(update, context)
        await message.reply_text(
            f"✅ Published successfully to the primary destination and {len(draft.get('extra_destinations') or [])} additional destination(s), then submitted to the listing index."
            + (f"\n🔗 {link}" if link else "")
        )


async def add_post_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not is_private(update):
        await message.reply_text("Please continue the post-creation workflow in the bot's private chat.")
        return
    if not await can_create_listing(update, context):
        await message.reply_text("⛔ An approved Admin or Merchant account is required.")
        return
    async with _post_workflow_lock(context, user.id):
        draft = _load_admin_post_draft(update, context)
        if not draft:
            await message.reply_text("There is no active post draft to discard.")
            return
        if draft.get("stage") in {"publishing", "delivery_uncertain", "delivery_partial", "published", "index_pending"}:
            await message.reply_text(
                "⚠️ This draft may already have been published. I will not discard or resend it. "
                "Use /confirm to retry indexing when the post is marked published/index-pending, "
                "or check the destination first if delivery is uncertain."
            )
            return
        _delete_admin_post_draft(update, context)
        await message.reply_text("Draft discarded. No post was published.")


async def post_init(application: Application):
    """Populate Telegram's command menu so users can discover the main controls."""
    commands = [
        BotCommand("start", "Open the account finder homepage"),
        BotCommand("help", "Show search filters and commands"),
        BotCommand("find", "Search listings with filters"),
        BotCommand("filter", "Filter listings by amount or other filters"),
        BotCommand("search", "Alias for /find"),
        BotCommand("alertme", "Save a listing notification filter"),
        BotCommand("wishlist", "Alias for /alertme"),
        BotCommand("myalerts", "View your saved alerts"),
        BotCommand("cancelalert", "Cancel a saved alert by number"),
        BotCommand("stats", "View listing index and alert status"),
        BotCommand("index", "View, export or clear the local listing index"),
        BotCommand("indexreply", "Import an old post you reply to in the listing group"),
        BotCommand("audit", "Check group setup, storage and bot membership"),
        BotCommand("adminpanel", "Open the administrator control panel"),
        BotCommand("delete", "Delete a listing by replying to an admin search result"),
        BotCommand("deleteall", "Superadmin: clear all searchable listings after backup confirmation"),
        BotCommand("setposttopic", "Choose the forum topic used by new listing posts"),
        BotCommand("add", "Create a listing with guided account fields (admin/merchant)"),
        BotCommand("done", "Finish uploading listing screenshots"),
        BotCommand("skip", "Skip an optional account field"),
        BotCommand("back", "Return to the previous account field"),
        BotCommand("editcaption", "Edit the listing caption before publishing"),
        BotCommand("confirm", "Publish or retry indexing the approved post"),
        BotCommand("cancel", "Discard an unpublished listing draft"),
        BotCommand("setsource", "Connect a source group or channel"),
        BotCommand("source", "Show the configured source chat"),
        BotCommand("unsetsource", "Disconnect the source chat (Superadmin)"),
        BotCommand("destinations", "View configured listing publication destinations"),
        BotCommand("adddestination", "Superadmin: add a group/channel publication destination"),
        BotCommand("removedestination", "Superadmin: remove a publication destination"),
        BotCommand("setadmin", "Configure the private admin request group"),
    ]
    try:
        await application.bot.set_my_commands(commands)
    except TelegramError as exc:
        log.warning("Could not update Telegram command menu: %s", exc)


def main():
    token = "8740672029:AAGodg9flHQI3LzO6A7iO2x3BgjrvMZhniY" # os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Missing BOT_TOKEN. In Termux, run: export BOT_TOKEN='8740672029:AAGodg9flHQI3LzO6A7iO2x3BgjrvMZhniY'"
        )
    database.init_db()
    create_automatic_backup("startup")
    if config.SOURCE_CHAT_ID is not None and not database.get_setting("source_chat_id"):
        database.set_setting("source_chat_id", int(config.SOURCE_CHAT_ID))

    # Telegram albums arrive as several ordered updates. Serial update processing
    # prevents /done or /confirm racing ahead of the preceding photo messages.
    app = Application.builder().token(token).concurrent_updates(1).post_init(post_init).build()
    app.add_handler(CommandHandler("add", add_post_command), group=-1)
    app.add_handler(CommandHandler("addpost", add_post_command), group=-1)
    app.add_handler(CommandHandler("done", add_post_done), group=-1)
    app.add_handler(CommandHandler("skip", add_post_skip), group=-1)
    app.add_handler(CommandHandler("back", add_post_back), group=-1)
    app.add_handler(CommandHandler("preview", add_post_preview), group=-1)
    app.add_handler(CommandHandler("editcaption", add_post_edit_caption), group=-1)
    app.add_handler(CommandHandler("confirm", add_post_confirm), group=-1)
    app.add_handler(CommandHandler("cancel", add_post_cancel), group=-1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.PHOTO, add_post_photo), group=-1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, add_post_details), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler(["find", "search", "filter"], find_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("deleteall", delete_all_command))
    app.add_handler(CommandHandler("setposttopic", setposttopic_command))
    app.add_handler(CommandHandler(["alertme", "alerts", "wishlist"], alertme))
    # Like the reference bot, users can type the filter directly without /find.
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_text_router), group=0)
    app.add_handler(CommandHandler("myalerts", myalerts))
    app.add_handler(CommandHandler("cancelalert", cancelalert))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("index", index_command))
    app.add_handler(CommandHandler("audit", audit_command))
    app.add_handler(CommandHandler("adminpanel", adminpanel_command))
    app.add_handler(CommandHandler("setsource", setsource))
    app.add_handler(CommandHandler("adddestination", add_destination_command))
    app.add_handler(CommandHandler("removedestination", remove_destination_command))
    app.add_handler(CommandHandler("destinations", destinations_command))
    app.add_handler(CommandHandler("setadmin", setadmin))
    app.add_handler(CommandHandler("settopicname", settopicname))
    app.add_handler(CommandHandler("topicid", topicid_command))
    app.add_handler(CommandHandler(["sold", "remove_listing", "removelisting"], sold_command))
    app.add_handler(CommandHandler("relist", relist_command))
    app.add_handler(CallbackQueryHandler(callback_action, pattern=r"^(?:act:|home:|soldreq:|index:|admin:|role:|help$|home_help$|resume$|home_resume$|home$)"))
    app.add_handler(CommandHandler("source", source_status))
    app.add_handler(CommandHandler("unsetsource", unsetsource))
    app.add_handler(CommandHandler("indexreply", indexreply))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS, channel_post_command_router), group=0)
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & ~filters.COMMAND, admin_reply_forwarder), group=1)
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, admin_reply_forwarder), group=1)
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & ~filters.COMMAND, ingest_group_message), group=2)
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, ingest_group_message), group=2)
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS, ingest_group_message), group=2)
    app.add_error_handler(error_handler)

    print("Listing finder is running. Keep Termux open for ongoing monitoring.")
    print("First setup: use /setsource in the listing group as its administrator, or send /setsource @channelusername in private chat (bot + Superadmin must be channel admins). Run /setadmin from your personal account inside a separate private admin group.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
