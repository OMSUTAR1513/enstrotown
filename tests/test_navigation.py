"""Regression tests for in-place Telegram menu navigation and callback handling."""
import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_DB_PATH", str(Path(tempfile.gettempdir()) / "abhinazh_v55_navigation_tests.sqlite3"))


def _install_telegram_test_stubs_if_missing():
    try:
        import telegram  # noqa: F401
        return
    except ImportError:
        pass

    telegram = types.ModuleType("telegram")

    class DummyFilter:
        def __and__(self, other):
            return self

        def __invert__(self):
            return self

    class Update:
        ALL_TYPES = []

    class InlineKeyboardButton:
        def __init__(self, text, **kwargs):
            self.text = text
            self.kwargs = kwargs

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard

    class BotCommand:
        def __init__(self, command, description):
            self.command = command
            self.description = description

    class InputMediaPhoto:
        def __init__(self, media, caption=None):
            self.media = media
            self.caption = caption

    telegram.InputMediaPhoto = InputMediaPhoto
    telegram.Update = Update
    telegram.InlineKeyboardButton = InlineKeyboardButton
    telegram.InlineKeyboardMarkup = InlineKeyboardMarkup
    telegram.BotCommand = BotCommand
    sys.modules["telegram"] = telegram

    constants = types.ModuleType("telegram.constants")
    constants.ChatType = SimpleNamespace(PRIVATE="private", GROUP="group", SUPERGROUP="supergroup", CHANNEL="channel")
    sys.modules["telegram.constants"] = constants

    errors = types.ModuleType("telegram.error")

    class TelegramError(Exception):
        pass

    class BadRequest(TelegramError):
        pass

    class Forbidden(TelegramError):
        pass

    errors.TelegramError = TelegramError
    errors.BadRequest = BadRequest
    errors.Forbidden = Forbidden
    sys.modules["telegram.error"] = errors

    ext = types.ModuleType("telegram.ext")

    class DummyHandler:
        def __init__(self, *args, **kwargs):
            pass

    class ContextTypes:
        DEFAULT_TYPE = object

    ext.Application = type("Application", (), {})
    ext.CallbackQueryHandler = DummyHandler
    ext.CommandHandler = DummyHandler
    ext.ContextTypes = ContextTypes
    ext.MessageHandler = DummyHandler
    ext.filters = SimpleNamespace(
        ChatType=SimpleNamespace(PRIVATE=DummyFilter()),
        TEXT=DummyFilter(),
        COMMAND=DummyFilter(),
        UpdateType=SimpleNamespace(MESSAGE=DummyFilter(), EDITED_MESSAGE=DummyFilter(), CHANNEL_POST=DummyFilter(), CHANNEL_POSTS=DummyFilter()),
    )
    sys.modules["telegram.ext"] = ext


_install_telegram_test_stubs_if_missing()
import database  # noqa: E402
import bot  # noqa: E402

database.init_db()


class FakeMessage:
    def __init__(self, chat_id=9001, text="Menu"):
        self.chat = SimpleNamespace(type="private", id=chat_id)
        self.message_id = 77
        self.text = text
        self.caption = None
        self.photo = None
        self.reply_calls = []

    async def reply_text(self, *args, **kwargs):
        self.reply_calls.append((args, kwargs))
        raise AssertionError("A menu callback must not send a replacement message")


class FakeQuery:
    def __init__(self, data, user_id=123456):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, username="test_user", full_name="Test User")
        self.message = FakeMessage()
        self.answers = []
        self.edits = []
        self.caption_edits = []
        self.answer_error = None
        self.edit_error = None

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))
        if self.answer_error:
            raise self.answer_error

    async def edit_message_text(self, *args, **kwargs):
        self.edits.append((args, kwargs))
        if self.edit_error:
            error, self.edit_error = self.edit_error, None
            raise error

    async def edit_message_caption(self, *args, **kwargs):
        self.caption_edits.append((args, kwargs))
        if self.edit_error:
            error, self.edit_error = self.edit_error, None
            raise error

    async def edit_message_reply_markup(self, *args, **kwargs):
        self.markup_edits = getattr(self, "markup_edits", [])
        self.markup_edits.append((args, kwargs))


class NavigationTests(unittest.TestCase):
    def run_async(self, awaitable):
        return asyncio.run(awaitable)

    def make_update_context(self, query, user_data=None):
        update = SimpleNamespace(
            callback_query=query,
            effective_user=query.from_user,
            effective_chat=query.message.chat,
            effective_message=query.message,
        )
        context = SimpleNamespace(user_data=user_data if user_data is not None else {}, bot=SimpleNamespace())
        return update, context

    def _post_update_context(self, user_id=1485615852, text="/confirm", user_data=None):
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private", id=user_id),
            message_id=1001, text=text, caption=None, photo=None, reply_calls=[],
        )

        async def reply_text(*args, **kwargs):
            message.reply_calls.append((args, kwargs))

        message.reply_text = reply_text
        user = SimpleNamespace(id=user_id, username="admin", full_name="Admin")
        update = SimpleNamespace(
            effective_user=user, effective_chat=message.chat, effective_message=message,
        )
        context = SimpleNamespace(
            user_data=user_data if user_data is not None else {},
            application=SimpleNamespace(bot_data={}),
            bot=None, args=[],
        )
        return update, context, message

    def test_admin_search_delivery_registers_summary_and_copied_images_for_delete(self):
        listing_key = f"search-map-{os.getpid()}"
        fake_bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=801)))
        item = {"listing_key": listing_key}
        reply_markup = object()
        with patch.object(bot, "copy_listing_messages", new=AsyncMock(return_value=([802, 803], []))), \
             patch.object(bot, "attach_actions_to_copied_listing", new=AsyncMock(return_value=True)):
            result = self.run_async(bot.send_listing_to_user(
                fake_bot, 1485615852, item, summary="ACCOUNT RESULT", reply_markup=reply_markup,
                track_for_admin_delete=True,
            ))
        self.assertTrue(result)
        for message_id in (801, 802, 803):
            self.assertEqual(
                database.get_listing_key_by_search_reference(1485615852, message_id), listing_key
            )

    def test_admin_can_reply_delete_to_search_result_summary(self):
        key = database.upsert_source_part(
            chat_id=-10012345001, message_id=7123, media_group_id=None,
            message_text="K1700 admin delete command test", ocr_text="",
            created_at="2026-11-24T10:00:00+00:00", topic_id=0, topic_title="",
        )
        database.add_listing_search_reference(1485615852, 701, key)
        reply_target = SimpleNamespace(message_id=701)
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private", id=1485615852),
            message_id=702, text="/delete", reply_to_message=reply_target,
            reply_calls=[],
        )
        async def reply_text(*args, **kwargs):
            message.reply_calls.append((args, kwargs))
        message.reply_text = reply_text
        user = SimpleNamespace(id=1485615852, username="root", full_name="Root")
        update = SimpleNamespace(effective_user=user, effective_chat=message.chat, effective_message=message)
        context = SimpleNamespace()
        with patch.object(bot, "remove_listing_local_storage"):
            self.run_async(bot.delete_command(update, context))
        self.assertIsNone(database.get_listing(key))
        self.assertIn("ACCOUNT DELETED FROM SEARCH DATABASE", message.reply_calls[-1][0][0])

    def test_delete_command_rejects_unmapped_reply_without_mutating_index(self):
        key = database.upsert_source_part(
            chat_id=-10012345001, message_id=7124, media_group_id=None,
            message_text="K1700 no delete mapping", ocr_text="",
            created_at="2026-11-24T10:01:00+00:00", topic_id=0, topic_title="",
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private", id=1485615852),
            message_id=703, text="/delete", reply_to_message=SimpleNamespace(message_id=799),
            reply_calls=[],
        )
        async def reply_text(*args, **kwargs):
            message.reply_calls.append((args, kwargs))
        message.reply_text = reply_text
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1485615852), effective_chat=message.chat,
            effective_message=message,
        )
        with patch.object(bot, "remove_listing_local_storage"), patch.object(bot, "can_access_admin_panel", new=AsyncMock(return_value=True)):
            self.run_async(bot.delete_command(update, SimpleNamespace()))
        self.assertIsNotNone(database.get_listing(key))
        self.assertIn("couldn't match", message.reply_calls[-1][0][0])

    def test_setposttopic_saves_detected_forum_topic_id(self):
        database.delete_setting("post_topic_id")
        message = SimpleNamespace(
            chat=SimpleNamespace(type="supergroup", id=-10012345009),
            message_id=704, message_thread_id=456789, reply_to_message=None,
            reply_calls=[],
        )
        async def reply_text(*args, **kwargs):
            message.reply_calls.append((args, kwargs))
        message.reply_text = reply_text
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1485615852), effective_chat=message.chat,
            effective_message=message,
        )
        context = SimpleNamespace(args=[], bot=SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(type="supergroup", is_forum=True))))
        with patch.object(bot, "source_chat_id", return_value=-10012345009), \
             patch.object(bot, "can_manage_source_records", new=AsyncMock(return_value=True)):
            self.run_async(bot.setposttopic_command(update, context))
        self.assertEqual(bot.configured_post_topic_id(), 456789)
        self.assertIn("PUBLISHING TOPIC SAVED", message.reply_calls[-1][0][0])
        database.delete_setting("post_topic_id")

    def test_source_destination_change_clears_old_forum_topic(self):
        database.set_setting("post_topic_id", 444555)
        update = SimpleNamespace(effective_user=SimpleNamespace(
            id=1485615852, full_name="Root Admin", username="root"
        ))
        target = SimpleNamespace(
            id=-10012345010, title="New Listings", full_name="", username=None
        )
        with patch.object(bot, "source_chat_id", return_value=-10012345009):
            self.run_async(bot._save_source_chat(update, SimpleNamespace(), target))
        self.assertIsNone(database.get_setting("post_topic_id"))
        database.delete_setting("source_chat_id")
        database.delete_setting("source_chat_title")
        database.delete_setting("source_group_invite_link")

    def test_setposttopic_general_resets_to_default_destination(self):
        database.set_setting("post_topic_id", 456789)
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private", id=1485615852),
            message_id=705, message_thread_id=None, reply_to_message=None,
            reply_calls=[],
        )
        async def reply_text(*args, **kwargs):
            message.reply_calls.append((args, kwargs))
        message.reply_text = reply_text
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1485615852), effective_chat=message.chat,
            effective_message=message,
        )
        context = SimpleNamespace(args=["general"])
        self.run_async(bot.setposttopic_command(update, context))
        self.assertIsNone(bot.configured_post_topic_id())
        self.assertIn("General/default topic", message.reply_calls[-1][0][0])

    def test_delete_all_command_is_superadmin_only_and_prompts_confirmation(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private", id=1485615852),
            message_id=706, reply_calls=[],
        )
        async def reply_text(*args, **kwargs):
            message.reply_calls.append((args, kwargs))
        message.reply_text = reply_text
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1485615852), effective_chat=message.chat,
            effective_message=message,
        )
        with patch.object(bot, "user_can_manage_index", new=AsyncMock(return_value=True)):
            self.run_async(bot.delete_all_command(update, SimpleNamespace()))
        self.assertIn("DELETE ALL ACCOUNT LISTINGS", message.reply_calls[-1][0][0])
        buttons = message.reply_calls[-1][1]["reply_markup"].inline_keyboard
        callbacks = [button.kwargs.get("callback_data") for row in buttons for button in row]
        self.assertIn("index:delete-confirm", callbacks)

    def test_new_admin_commands_are_registered(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn('CommandHandler("delete", delete_command)', source)
        self.assertIn('CommandHandler("deleteall", delete_all_command)', source)
        self.assertIn('CommandHandler("setposttopic", setposttopic_command)', source)
        self.assertIn('CommandHandler("adddestination", add_destination_command)', source)
        self.assertIn('CommandHandler("removedestination", remove_destination_command)', source)

    def test_post_workflow_commands_are_private_chat_only(self):
        user_id = 880400000 + os.getpid()
        update, context, message = self._post_update_context(user_id=user_id, text="/confirm")
        update.effective_chat.type = "group"
        with patch.object(bot, "can_access_admin_panel", new=AsyncMock(return_value=True)):
            self.run_async(bot.add_post_confirm(update, context))
        self.assertIn("private chat", message.reply_calls[-1][0][0])
        self.assertIsNone(database.get_admin_post_draft(user_id))

    def test_admin_post_drafts_persist_and_restore_from_database(self):
        user_id = 880000000 + os.getpid()
        draft = {"stage": "preview", "photos": ["photo-file-id"], "caption": "#6558\nbase stats"}
        database.save_admin_post_draft(user_id, draft)
        update, context, _ = self._post_update_context(user_id=user_id)
        self.assertEqual(bot._load_admin_post_draft(update, context), draft)
        self.assertEqual(context.user_data["new_listing"], draft)
        bot._delete_admin_post_draft(update, context)
        self.assertIsNone(database.get_admin_post_draft(user_id))

    def test_approved_merchant_can_create_listing_but_is_not_an_admin(self):
        user_id = 879900000 + os.getpid()
        database.set_user_role(user_id, "MERCHANT", 1485615852)
        update, context, message = self._post_update_context(user_id=user_id, text="/add post")
        context.args = ["post"]
        self.assertFalse(self.run_async(bot.can_access_admin_panel(update, context)))
        self.assertTrue(self.run_async(bot.can_create_listing(update, context)))
        self.run_async(bot.add_post_command(update, context))
        self.assertEqual(database.get_admin_post_draft(user_id)["stage"], "photos")
        database.delete_admin_post_draft(user_id)

    def test_additional_destination_delivery_is_not_duplicated_on_retry(self):
        user_id = 879800000 + os.getpid()
        update, context, _ = self._post_update_context(user_id=user_id)
        primary_id = -100878700
        extra_id = -100878701
        context.bot = SimpleNamespace(
            copy_messages=AsyncMock(return_value=[SimpleNamespace(message_id=908)]),
        )
        draft = {
            "published_chat_id": primary_id,
            "published_messages": [{"chat": {"id": primary_id}, "message_id": 501}],
            "extra_destinations": [{"chat_id": extra_id, "title": "Extra channel"}],
            "extra_delivery_status": {str(extra_id): {"status": "pending"}},
        }
        status, _ = self.run_async(bot._deliver_admin_post_to_extra_destinations(update, context, draft))
        self.assertEqual(status, "sent")
        self.assertEqual(draft["extra_delivery_status"][str(extra_id)]["status"], "sent")
        status, _ = self.run_async(bot._deliver_admin_post_to_extra_destinations(update, context, draft))
        self.assertEqual(status, "sent")
        context.bot.copy_messages.assert_awaited_once()
        database.delete_admin_post_draft(user_id)

    def test_superadmin_can_add_and_remove_verified_channel_destination(self):
        destination_id = -100879600 - os.getpid()
        update, context, message = self._post_update_context(user_id=1485615852)
        target = SimpleNamespace(id=destination_id, type="channel", title="Sales Channel", username="sales_channel")
        admin_member = SimpleNamespace(status="administrator", can_post_messages=True)
        context.args = ["@sales_channel"]
        context.bot = SimpleNamespace(
            id=999,
            get_chat=AsyncMock(return_value=target),
            get_chat_member=AsyncMock(side_effect=[admin_member, admin_member]),
        )
        with patch.object(bot, "source_chat_id", return_value=-100700001):
            self.run_async(bot.add_destination_command(update, context))
        self.assertEqual(database.get_publish_destination(destination_id)["username"], "sales_channel")
        context.args = ["@sales_channel"]
        self.run_async(bot.remove_destination_command(update, context))
        self.assertIsNone(database.get_publish_destination(destination_id))

    def test_additional_destination_retries_definite_rejection_but_not_uncertain_send(self):
        user_id = 879700000 + os.getpid()
        update, context, _ = self._post_update_context(user_id=user_id)
        primary_id = -100878600
        extra_id = -100878601
        context.bot = SimpleNamespace(
            copy_messages=AsyncMock(side_effect=[bot.BadRequest("not enough permissions"), [SimpleNamespace(message_id=909)]]),
        )
        draft = {
            "published_chat_id": primary_id,
            "published_messages": [{"chat": {"id": primary_id}, "message_id": 502}],
            "extra_destinations": [{"chat_id": extra_id, "title": "Extra channel"}],
            "extra_delivery_status": {str(extra_id): {"status": "pending"}},
        }
        status, _ = self.run_async(bot._deliver_admin_post_to_extra_destinations(update, context, draft))
        self.assertEqual(status, "failed")
        status, _ = self.run_async(bot._deliver_admin_post_to_extra_destinations(update, context, draft))
        self.assertEqual(status, "sent")
        self.assertEqual(context.bot.copy_messages.await_count, 2)

        uncertain_draft = {
            "published_chat_id": primary_id,
            "published_messages": [{"chat": {"id": primary_id}, "message_id": 503}],
            "extra_destinations": [{"chat_id": extra_id, "title": "Extra channel"}],
            "extra_delivery_status": {str(extra_id): {"status": "pending"}},
        }
        context.bot.copy_messages = AsyncMock(side_effect=TimeoutError("network timeout"))
        status, _ = self.run_async(bot._deliver_admin_post_to_extra_destinations(update, context, uncertain_draft))
        self.assertEqual(status, "uncertain")
        self.run_async(bot._deliver_admin_post_to_extra_destinations(update, context, uncertain_draft))
        context.bot.copy_messages.assert_awaited_once()

    def test_published_bot_authored_group_post_can_be_indexed_explicitly(self):
        from datetime import datetime, timezone
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-10012345, type="supergroup", username="", title="Listings"),
            message_id=91, from_user=SimpleNamespace(id=999, is_bot=True),
            sender_chat=None, caption="#6558 | 30k+mm", text=None, date=datetime.now(timezone.utc),
            message_thread_id=0, media_group_id=None, photo=None,
        )
        with patch.object(bot, "source_chat_id", return_value=-10012345), \
             patch.object(bot, "get_message_media", return_value=None), \
             patch.object(bot, "save_message_media_locally", new=AsyncMock(return_value={})), \
             patch.object(database, "upsert_source_part", return_value="message:-10012345:91") as upsert, \
             patch.object(database, "get_listing", return_value=None), \
             patch.object(database, "get_latest_listing", return_value=None), \
             patch.object(bot, "schedule_alert_check"), \
             patch.object(bot, "schedule_amount_admin_check"):
            context = SimpleNamespace(application=SimpleNamespace())
            self.assertIsNone(self.run_async(bot.index_message(message, context)))
            upsert.assert_not_called()
            result = self.run_async(bot.index_message(message, context, allow_bot_post=True))
        self.assertEqual(result, "message:-10012345:91")
        upsert.assert_called_once()

    def test_confirm_indexes_after_publish_and_never_resends_on_index_retry(self):
        from datetime import datetime, timezone
        user_id = 880100000 + os.getpid()
        target_id = -100778899
        database.save_admin_post_draft(user_id, {
            "stage": "preview", "photos": ["upload-file-id"], "caption": "#6558 | 30.5k+mm\nbase stats: 868-861-866%"
        })
        database.set_setting("post_topic_id", 123456)
        update, context, message = self._post_update_context(user_id=user_id)
        sent_message = SimpleNamespace(
            chat=SimpleNamespace(id=target_id, type="supergroup", username="", title="Listings"),
            message_id=501, caption="#6558 | 30.5k+mm\nbase stats: 868-861-866%", text=None,
            date=datetime.now(timezone.utc), media_group_id=None, message_thread_id=0,
            photo=[SimpleNamespace(file_id="published-file-id", file_unique_id="unique", width=1280, height=720, file_size=8000)],
            from_user=SimpleNamespace(id=999, first_name="Bot", last_name="", username="testbot", is_bot=True),
            sender_chat=None,
        )
        context.bot = SimpleNamespace(
            send_photo=AsyncMock(return_value=sent_message),
            send_media_group=AsyncMock(),
            send_message=AsyncMock(),
        )
        indexer = AsyncMock(side_effect=[RuntimeError("temporary DB failure"), "listing-key"])
        with patch.object(bot, "can_access_admin_panel", new=AsyncMock(return_value=True)), \
             patch.object(bot, "source_chat_id", return_value=target_id), \
             patch.object(bot, "index_message", new=indexer):
            self.run_async(bot.add_post_confirm(update, context))
            saved = database.get_admin_post_draft(user_id)
            self.assertEqual(saved["stage"], "index_pending")
            self.assertIn("published_messages", saved)
            self.run_async(bot.add_post_confirm(update, context))

        context.bot.send_photo.assert_awaited_once()
        self.assertEqual(context.bot.send_photo.await_args.kwargs.get("message_thread_id"), 123456)
        database.delete_setting("post_topic_id")
        self.assertEqual(indexer.await_count, 2)
        self.assertTrue(all(call.kwargs.get("allow_bot_post") is True for call in indexer.await_args_list))
        self.assertIsNone(database.get_admin_post_draft(user_id))
        self.assertIn("No second post was sent", message.reply_calls[-1][0][0])

    def test_full_admin_post_workflow_previews_album_then_publishes_and_indexes(self):
        from datetime import datetime, timezone
        user_id = 880200000 + os.getpid()
        target_id = -100889900
        update, context, message = self._post_update_context(user_id=user_id, text="/add post")
        context.args = ["post"]
        database.set_setting("post_topic_id", 789012)
        sent_album = [
            SimpleNamespace(
                chat=SimpleNamespace(id=target_id, type="supergroup", username="", title="Listings"),
                message_id=601 + index, caption="#6558 | 30.5k+mm\nbase stats: 868-861-866%" if index == 0 else None,
                text=None, date=datetime.now(timezone.utc), media_group_id="album-601",
                message_thread_id=0,
                photo=[SimpleNamespace(file_id=f"published-{index}", file_unique_id=f"u-{index}", width=1280, height=720, file_size=9000)],
                from_user=SimpleNamespace(id=999, first_name="Bot", last_name="", username="testbot", is_bot=True),
                sender_chat=None,
            )
            for index in range(2)
        ]
        preview_media_calls = []
        publish_calls = []

        async def send_media_group(*, chat_id, media, message_thread_id=None):
            if int(chat_id) == user_id:
                preview_media_calls.append((media, message_thread_id))
                return tuple()
            publish_calls.append((media, message_thread_id))
            return tuple(sent_album)

        context.bot = SimpleNamespace(
            send_photo=AsyncMock(),
            send_media_group=AsyncMock(side_effect=send_media_group),
            send_message=AsyncMock(),
        )
        with patch.object(bot, "can_access_admin_panel", new=AsyncMock(return_value=True)), \
             patch.object(bot, "source_chat_id", return_value=target_id), \
             patch.object(bot, "index_message", new=AsyncMock(side_effect=["listing-key", "listing-key"])) as indexer:
            self.run_async(bot.add_post_command(update, context))

            for idx in range(2):
                message.text = None
                message.photo = [SimpleNamespace(file_id=f"upload-{idx}")]
                self.run_async(bot.add_post_photo(update, context))

            message.text = "/done"
            message.photo = None
            self.run_async(bot.add_post_done(update, context))
            self.assertEqual(database.get_admin_post_draft(user_id)["stage"], "details")

            structured_answers = [
                "6558", "Can go to K1950", "Rally lead", "30.5k+mm", "868-861-866%",
                "USD 350", "1100 artifact stars, 203 blessed", "5+1 champs, 2 cups",
                "29 castle stars, 3 mana troops", "8 fams, IGG linked", "relink, 3 11k",
            ]
            for answer in structured_answers:
                message.text = answer
                self.run_async(bot.add_post_details(update, context))
            self.assertEqual(database.get_admin_post_draft(user_id)["stage"], "preview")
            generated_caption = database.get_admin_post_draft(user_id)["caption"]
            self.assertIn("#6558 | ✈️ Can go to K1950 | 30.5k+mm", generated_caption)
            self.assertIn("Stats: 868-861-866%", generated_caption)
            self.assertIn("Price: USD 350", generated_caption)
            self.assertIn("My Telegram id: @admin", generated_caption)
            from amounts import extract_listing_amount
            self.assertEqual(extract_listing_amount("", generated_caption), (350.0, 350.0, "message"))

            message.text = "/editcaption #6558 | 30.5k+mm\nbase stats: 868-861-866%\n1100 artifact stars, 203 blessed"
            self.run_async(bot.add_post_edit_caption(update, context))
            self.assertIn("203 blessed", database.get_admin_post_draft(user_id)["caption"])

            message.text = "/confirm"
            self.run_async(bot.add_post_confirm(update, context))

        self.assertEqual(len(preview_media_calls), 2)
        self.assertEqual(len(preview_media_calls[0][0]), 2)
        self.assertIsNone(preview_media_calls[0][1])  # Private preview does not use the public topic ID.
        self.assertIn("#6558 | ✈️ Can go to K1950 | 30.5k+mm", preview_media_calls[0][0][0].caption)
        self.assertIn("Stats: 868-861-866%", preview_media_calls[0][0][0].caption)
        self.assertIsNone(preview_media_calls[0][0][1].caption)
        self.assertEqual(len(publish_calls), 1)
        self.assertEqual(publish_calls[0][1], 789012)
        self.assertEqual(publish_calls[0][0][0].caption, "#6558 | 30.5k+mm\nbase stats: 868-861-866%\n1100 artifact stars, 203 blessed")
        self.assertIsNone(publish_calls[0][0][1].caption)
        database.delete_setting("post_topic_id")
        self.assertEqual(indexer.await_count, 2)
        self.assertTrue(all(call.kwargs.get("allow_bot_post") for call in indexer.await_args_list))
        self.assertIsNone(database.get_admin_post_draft(user_id))
        self.assertIn("Published successfully", message.reply_calls[-1][0][0])

    def test_confirm_is_blocked_until_preview_is_delivered_and_preview_can_be_retried(self):
        user_id = 880350000 + os.getpid()
        database.save_admin_post_draft(user_id, {
            "stage": "preview_pending", "photos": ["upload-file-id"], "caption": "#6558\nbase stats"
        })
        update, context, message = self._post_update_context(user_id=user_id, text="/confirm")
        context.bot = SimpleNamespace(
            send_photo=AsyncMock(return_value=SimpleNamespace(message_id=1)),
            send_media_group=AsyncMock(),
            send_message=AsyncMock(),
            send_photo_to_destination=AsyncMock(),
        )
        with patch.object(bot, "can_access_admin_panel", new=AsyncMock(return_value=True)), \
             patch.object(bot, "source_chat_id", return_value=-100778899):
            self.run_async(bot.add_post_confirm(update, context))
            context.bot.send_photo.assert_not_awaited()
            self.assertEqual(database.get_admin_post_draft(user_id)["stage"], "preview_pending")

            message.text = "/preview"
            self.run_async(bot.add_post_preview(update, context))
            self.assertEqual(database.get_admin_post_draft(user_id)["stage"], "preview")
            context.bot.send_photo.assert_awaited_once()
            context.bot.send_message.assert_awaited_once()

        database.delete_admin_post_draft(user_id)

    def test_ambiguous_telegram_send_is_not_automatically_retried(self):
        user_id = 880300000 + os.getpid()
        database.save_admin_post_draft(user_id, {
            "stage": "preview", "photos": ["upload-file-id"], "caption": "#6558 details"
        })
        update, context, message = self._post_update_context(user_id=user_id)
        context.bot = SimpleNamespace(
            send_photo=AsyncMock(side_effect=TimeoutError("network timed out")),
            send_media_group=AsyncMock(),
            send_message=AsyncMock(),
        )
        with patch.object(bot, "can_access_admin_panel", new=AsyncMock(return_value=True)), \
             patch.object(bot, "source_chat_id", return_value=-100778899):
            self.run_async(bot.add_post_confirm(update, context))
            self.run_async(bot.add_post_confirm(update, context))
        saved = database.get_admin_post_draft(user_id)
        self.assertEqual(saved["stage"], "delivery_uncertain")
        context.bot.send_photo.assert_awaited_once()
        self.assertIn("will not automatically send", message.reply_calls[-1][0][0])
        database.delete_admin_post_draft(user_id)

    def test_application_serializes_updates_for_ordered_photo_album_workflow(self):
        tree = __import__("ast").parse((ROOT / "bot.py").read_text(encoding="utf-8"))
        values = [
            node.args[0].value
            for node in __import__("ast").walk(tree)
            if isinstance(node, __import__("ast").Call)
            and isinstance(node.func, __import__("ast").Attribute)
            and node.func.attr == "concurrent_updates"
            and node.args
            and isinstance(node.args[0], __import__("ast").Constant)
        ]
        self.assertIn(1, values, "The application must process photo album updates sequentially")

    def test_india_timezone_helper_has_a_valid_timezone(self):
        from datetime import datetime
        tz = bot.india_timezone()
        offset = datetime(2026, 1, 1, tzinfo=tz).utcoffset()
        self.assertIsNotNone(offset)
        self.assertEqual(offset.total_seconds(), 19800)

    def test_caption_limit_counts_utf16_units(self):
        self.assertTrue(bot._caption_fits_telegram("a" * 1024))
        self.assertFalse(bot._caption_fits_telegram("😀" * 513))

    def test_caption_edit_preserves_line_breaks(self):
        message = SimpleNamespace(text="/editcaption #6558 | 30.5k+mm\nbase stats: 868-861-866%\n1100 artifact stars")
        match = __import__("re").match(
            r"^/editcaption(?:@[A-Za-z0-9_]+)?\s+(.*)$", message.text, flags=__import__("re").IGNORECASE | __import__("re").DOTALL
        )
        self.assertEqual(match.group(1).strip(), "#6558 | 30.5k+mm\nbase stats: 868-861-866%\n1100 artifact stars")

    def test_callback_keyboard_data_and_labels_fit_telegram_limits(self):
        keyboards = [
            bot.homepage_keyboard(),
            bot.home_navigation_keyboard(),
            bot.superadmin_panel_keyboard(),
            bot.role_management_keyboard(),
            bot.role_target_keyboard(9223372036854775807, "USER"),
            bot.admin_panel_keyboard(1485615852),
            bot.admin_result_keyboard(1485615852),
            bot.confirmation_keyboard("admin:delete-all-confirm"),
        ]
        allowed_styles = {None, "primary", "success", "danger"}
        for markup in keyboards:
            for row in markup.inline_keyboard:
                for button in row:
                    self.assertGreaterEqual(len(button.text), 1)
                    self.assertLessEqual(len(button.text), 64)
                    callback_data = button.kwargs.get("callback_data")
                    if callback_data is not None:
                        self.assertGreaterEqual(len(callback_data.encode("utf-8")), 1)
                        self.assertLessEqual(len(callback_data.encode("utf-8")), 64)
                    self.assertIn(button.kwargs.get("style"), allowed_styles)

    def test_home_and_help_screens_fit_telegram_text_limit(self):
        self.assertLessEqual(len(bot.welcome_screen_text()), 4096)
        self.assertLessEqual(len(bot.config.HELP_TEXT), 4096)
        self.assertLessEqual(len(bot.help_screen_text()), 4096)
        self.assertLessEqual(len(bot.admin_panel_text(1485615852)), 4096)

    def test_help_screen_remains_under_limit_with_long_configured_details(self):
        original = {
            "source_group_invite_link": database.get_setting("source_group_invite_link"),
            "source_chat_title": database.get_setting("source_chat_title"),
            "owner_username": database.get_setting("owner_username"),
            "owner_name": database.get_setting("owner_name"),
        }
        try:
            database.set_setting("source_group_invite_link", "https://t.me/+" + "A" * 100)
            database.set_setting("source_chat_title", "L" * 250)
            database.set_setting("owner_username", "owner_name_12345678901234567890")
            database.set_setting("owner_name", "N" * 200)
            self.assertLessEqual(len(bot.help_screen_text()), 4096)
        finally:
            for key, value in original.items():
                if value is None:
                    database.delete_setting(key)
                else:
                    database.set_setting(key, value)

    def test_screen_edit_removes_keyboard_if_both_text_edit_attempts_fail(self):
        query = FakeQuery("home:search")
        query.edit_message_text = AsyncMock(side_effect=[
            bot.BadRequest("Bad Request: BUTTON_DATA_INVALID"),
            bot.BadRequest("Bad Request: BUTTON_DATA_INVALID"),
        ])
        query.edit_message_reply_markup = AsyncMock()
        markup = object()
        result = self.run_async(bot.edit_callback_screen(query, "Search screen", reply_markup=markup))
        self.assertFalse(result)
        self.assertEqual(query.edit_message_text.await_count, 2)
        query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
        self.assertEqual(len(query.message.reply_calls), 0)

    def test_home_buttons_edit_existing_message_in_place(self):
        for action in ("home:search", "home:amount", "home:alerts", "home:help", "home:root", "help", "resume", "home_resume", "home:resume"):
            with self.subTest(action=action):
                query = FakeQuery(action)
                update, context = self.make_update_context(query)
                self.run_async(bot._callback_action_impl(update, context))
                self.assertEqual(len(query.edits), 1)
                self.assertEqual(len(query.message.reply_calls), 0)

    def test_help_button_on_media_message_edits_caption_not_text(self):
        query = FakeQuery("home:help")
        query.message.photo = [object()]
        update, context = self.make_update_context(query)
        self.run_async(bot._callback_action_impl(update, context))
        self.assertEqual(len(query.edits), 0)
        self.assertEqual(len(query.caption_edits), 1)
        caption = query.caption_edits[0][1]["caption"]
        self.assertLessEqual(len(caption), 1024)
        self.assertIn("HELP", caption)
        self.assertEqual(len(query.message.reply_calls), 0)

    def test_legacy_help_callback_is_supported(self):
        query = FakeQuery("help")
        update, context = self.make_update_context(query)
        self.run_async(bot._callback_action_impl(update, context))
        self.assertEqual(len(query.edits), 1)
        self.assertIn("LORDS MOBILE ACCOUNT SEARCH", query.edits[0][1]["text"])
        self.assertEqual(len(query.message.reply_calls), 0)

    def test_legacy_resume_callback_returns_to_home_without_new_message(self):
        query = FakeQuery("resume")
        update, context = self.make_update_context(query)
        self.run_async(bot._callback_action_impl(update, context))
        self.assertEqual(len(query.edits), 1)
        self.assertIn("Welcome to", query.edits[0][1]["text"])
        self.assertEqual(len(query.message.reply_calls), 0)

    def test_callback_handler_pattern_accepts_legacy_help_and_resume(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("help$", source)
        self.assertIn("resume$", source)
        self.assertIn("home:resume", source)

    def test_home_callback_still_edits_when_callback_ack_is_stale(self):
        query = FakeQuery("home:search")
        query.answer_error = bot.TelegramError("query is too old")
        update, context = self.make_update_context(query)
        self.run_async(bot._callback_action_impl(update, context))
        self.assertEqual(len(query.edits), 1)
        self.assertEqual(len(query.message.reply_calls), 0)

    def test_admin_confirmation_and_cancel_edit_same_panel_message(self):
        for action in ("admin:delete-all", "admin:reset", "admin:cancel", "admin:panel", "admin:home"):
            with self.subTest(action=action):
                query = FakeQuery(action, user_id=1485615852)
                update, context = self.make_update_context(query)
                self.run_async(bot._callback_action_impl(update, context))
                self.assertEqual(len(query.edits), 1)
                self.assertEqual(len(query.message.reply_calls), 0)

    def test_role_menu_and_pagination_edit_same_message(self):
        for action in ("role:panel", "role:menu", "role:list:0", "role:audit:0"):
            with self.subTest(action=action):
                query = FakeQuery(action, user_id=1485615852)
                update, context = self.make_update_context(query)
                self.run_async(bot._callback_action_impl(update, context))
                self.assertEqual(len(query.edits), 1)
                self.assertEqual(len(query.message.reply_calls), 0)

    def test_non_superadmin_cannot_open_destructive_admin_confirmation(self):
        query = FakeQuery("admin:reset", user_id=999001)
        update, context = self.make_update_context(query)
        with patch.object(bot, "can_access_admin_panel", new=AsyncMock(return_value=True)):
            self.run_async(bot._callback_action_impl(update, context))
        self.assertEqual(len(query.edits), 0)
        self.assertEqual(len(query.message.reply_calls), 0)
        self.assertTrue(query.answers)
        self.assertTrue(query.answers[0][1].get("show_alert"))

    def test_callback_navigation_handlers_do_not_send_replacement_chat_messages(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        start = source.index("async def _callback_action_impl(")
        end = source.index("async def setadmin(", start)
        callback_source = source[start:end]
        self.assertNotIn("query_obj.message.reply_text(", callback_source)
        self.assertNotIn("query_obj.message.reply_document(", callback_source)
        # New messages in this callback are only request tickets/warnings in the
        # separate admin group, never replacement messages in the buyer's chat.
        self.assertEqual(callback_source.count("context.bot.send_message("), 3)
        self.assertNotIn("chat_id=query_obj.message.chat_id", callback_source)

    def test_source_group_admin_cannot_overwrite_saved_owner_identity(self):
        setting_keys = (
            "owner_user_id", "owner_name", "owner_username", "source_chat_id",
            "source_chat_title", "source_group_invite_link",
        )
        original = {key: database.get_setting(key) for key in setting_keys}
        try:
            database.set_setting("owner_user_id", 1485615852)
            database.set_setting("owner_name", "Configured Superadmin")
            database.set_setting("owner_username", "Mandar2004")
            update = SimpleNamespace(
                effective_message=SimpleNamespace(reply_text=AsyncMock()),
                effective_chat=SimpleNamespace(
                    id=-1009001, type="group", title="Source Group", username="source_group_test"
                ),
                effective_user=SimpleNamespace(id=999001, username="other_admin", full_name="Other Admin"),
            )
            with patch.object(bot, "user_is_chat_admin", new=AsyncMock(return_value=True)), \
                 patch.object(bot, "app_role", return_value="ADMIN"):
                self.run_async(bot.setsource(update, SimpleNamespace(args=[])))
            self.assertEqual(database.get_setting("owner_user_id"), "1485615852")
            self.assertEqual(database.get_setting("owner_name"), "Configured Superadmin")
            self.assertEqual(database.get_setting("owner_username"), "Mandar2004")
            update.effective_message.reply_text.assert_awaited_once()
        finally:
            for key, value in original.items():
                if value is None:
                    database.delete_setting(key)
                else:
                    database.set_setting(key, value)

    def test_non_superadmin_cannot_claim_saved_owner_identity_via_setadmin(self):
        setting_keys = ("owner_user_id", "owner_name", "owner_username", "admin_chat_id", "admin_chat_title")
        original = {key: database.get_setting(key) for key in setting_keys}
        try:
            database.delete_setting("owner_user_id")
            update = SimpleNamespace(
                effective_message=SimpleNamespace(reply_text=AsyncMock()),
                effective_chat=SimpleNamespace(id=-1009003, type="group", title="Admin Group"),
                effective_user=SimpleNamespace(id=999003, username="not_root", full_name="Not Root"),
            )
            with patch.object(bot, "user_is_chat_admin", new=AsyncMock(return_value=True)):
                self.run_async(bot.setadmin(update, SimpleNamespace(args=[])))
            self.assertIsNone(database.get_setting("owner_user_id"))
            self.assertIsNone(database.get_setting("admin_chat_id"))
            update.effective_message.reply_text.assert_awaited_once()
        finally:
            for key, value in original.items():
                if value is None:
                    database.delete_setting(key)
                else:
                    database.set_setting(key, value)

    def test_superadmin_can_set_legacy_owner_identity_via_setadmin(self):
        setting_keys = ("owner_user_id", "owner_name", "owner_username", "admin_chat_id", "admin_chat_title")
        original = {key: database.get_setting(key) for key in setting_keys}
        try:
            database.delete_setting("owner_user_id")
            update = SimpleNamespace(
                effective_message=SimpleNamespace(reply_text=AsyncMock()),
                effective_chat=SimpleNamespace(id=-1009004, type="group", title="Root Admin Group"),
                effective_user=SimpleNamespace(id=1485615852, username="Mandar2004", full_name="Mandar"),
            )
            with patch.object(bot, "user_is_chat_admin", new=AsyncMock(return_value=True)):
                self.run_async(bot.setadmin(update, SimpleNamespace(args=[])))
            self.assertEqual(database.get_setting("owner_user_id"), "1485615852")
            self.assertEqual(database.get_setting("owner_name"), "Mandar")
        finally:
            for key, value in original.items():
                if value is None:
                    database.delete_setting(key)
                else:
                    database.set_setting(key, value)

    def test_app_admin_cannot_claim_legacy_owner_identity_via_setsource(self):
        setting_keys = ("owner_user_id", "owner_name", "owner_username", "source_chat_id", "source_chat_title", "source_group_invite_link")
        original = {key: database.get_setting(key) for key in setting_keys}
        try:
            database.delete_setting("owner_user_id")
            update = SimpleNamespace(
                effective_message=SimpleNamespace(reply_text=AsyncMock()),
                effective_chat=SimpleNamespace(
                    id=-1009005, type="group", title="Source Group", username="source_group_root_test"
                ),
                effective_user=SimpleNamespace(id=999005, username="app_admin", full_name="App Admin"),
            )
            with patch.object(bot, "user_is_chat_admin", new=AsyncMock(return_value=True)), \
                 patch.object(bot, "app_role", return_value="ADMIN"):
                self.run_async(bot.setsource(update, SimpleNamespace(args=[])))
            self.assertIsNone(database.get_setting("owner_user_id"))
        finally:
            for key, value in original.items():
                if value is None:
                    database.delete_setting(key)
                else:
                    database.set_setting(key, value)


    def test_channel_setsource_command_notifies_superadmin_privately(self):
        message = SimpleNamespace(
            text="/setsource", caption=None,
            chat=SimpleNamespace(type="channel", id=-100987, title="Source channel"),
        )
        update = SimpleNamespace(channel_post=message, edited_channel_post=None)
        fake_bot = SimpleNamespace(send_message=AsyncMock())
        with patch.object(bot, "configured_superadmin_id", return_value=1485615852):
            self.run_async(bot.channel_post_command_router(update, SimpleNamespace(bot=fake_bot)))
        fake_bot.send_message.assert_awaited_once()
        kwargs = fake_bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 1485615852)
        self.assertIn("private chat", kwargs["text"])

    def test_channel_router_handles_superadmin_dm_failure_without_crashing(self):
        message = SimpleNamespace(
            text="/setsource", caption=None,
            chat=SimpleNamespace(type="channel", id=-100991, title="Source channel"),
        )
        update = SimpleNamespace(channel_post=message, edited_channel_post=None)
        fake_bot = SimpleNamespace(send_message=AsyncMock(side_effect=bot.TelegramError("Forbidden: bot was blocked")))
        with patch.object(bot, "configured_superadmin_id", return_value=1485615852):
            self.run_async(bot.channel_post_command_router(update, SimpleNamespace(bot=fake_bot)))
        fake_bot.send_message.assert_awaited_once()

    def test_channel_setadmin_command_notifies_superadmin_about_private_group(self):
        message = SimpleNamespace(
            text="/setadmin", caption=None,
            chat=SimpleNamespace(type="channel", id=-100988, title="Source channel"),
        )
        update = SimpleNamespace(channel_post=message, edited_channel_post=None)
        fake_bot = SimpleNamespace(send_message=AsyncMock())
        with patch.object(bot, "configured_superadmin_id", return_value=1485615852):
            self.run_async(bot.channel_post_command_router(update, SimpleNamespace(bot=fake_bot)))
        self.assertIn("private group", fake_bot.send_message.await_args.kwargs["text"])

    def test_channel_command_router_handles_edited_channel_posts(self):
        message = SimpleNamespace(
            text="/setsource", caption=None,
            chat=SimpleNamespace(type="channel", id=-100989, title="Source channel"),
        )
        update = SimpleNamespace(channel_post=None, edited_channel_post=message)
        fake_bot = SimpleNamespace(send_message=AsyncMock())
        with patch.object(bot, "configured_superadmin_id", return_value=1485615852):
            self.run_async(bot.channel_post_command_router(update, SimpleNamespace(bot=fake_bot)))
        fake_bot.send_message.assert_awaited_once()

    def test_channel_source_configuration_verifies_human_and_bot_admin(self):
        keys = ("source_chat_id", "source_chat_title", "source_group_invite_link", "owner_user_id", "owner_name", "owner_username")
        original = {key: database.get_setting(key) for key in keys}
        target = SimpleNamespace(
            id=-1001234567890, type="channel", title="ESTROTOWN TEST",
            full_name="ESTROTOWN TEST", username="estrownf",
        )
        fake_bot = SimpleNamespace(
            id=999,
            get_chat=AsyncMock(return_value=target),
            get_chat_member=AsyncMock(side_effect=[
                SimpleNamespace(status="administrator"),
                SimpleNamespace(status="administrator"),
            ]),
        )
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type="private", id=1485615852),
            effective_user=SimpleNamespace(id=1485615852, username="Mandar2004", full_name="Mandar"),
        )
        try:
            with patch.object(bot, "is_superadmin_user_id", return_value=True):
                self.run_async(bot.setsource(update, SimpleNamespace(args=["@estrownf"], bot=fake_bot)))
            self.assertEqual(database.get_setting("source_chat_id"), str(target.id))
            self.assertEqual(database.get_setting("source_chat_title"), "ESTROTOWN TEST")
            self.assertEqual(database.get_setting("source_group_invite_link"), "https://t.me/estrownf")
            message.reply_text.assert_awaited_once()
            self.assertEqual(fake_bot.get_chat_member.await_count, 2)
        finally:
            for key, value in original.items():
                if value is None:
                    database.delete_setting(key)
                else:
                    database.set_setting(key, value)

    def test_channel_source_configuration_rejects_bot_without_admin_rights(self):
        keys = ("source_chat_id", "source_chat_title", "source_group_invite_link")
        original = {key: database.get_setting(key) for key in keys}
        database.delete_setting("source_chat_id")
        database.delete_setting("source_chat_title")
        database.delete_setting("source_group_invite_link")
        target = SimpleNamespace(id=-1001234567891, type="channel", title="No Bot Rights", full_name="No Bot Rights", username="nobotrights")
        fake_bot = SimpleNamespace(
            id=999,
            get_chat=AsyncMock(return_value=target),
            get_chat_member=AsyncMock(side_effect=[
                SimpleNamespace(status="administrator"),
                SimpleNamespace(status="member"),
            ]),
        )
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type="private", id=1485615852),
            effective_user=SimpleNamespace(id=1485615852, username="Mandar2004", full_name="Mandar"),
        )
        try:
            with patch.object(bot, "is_superadmin_user_id", return_value=True):
                self.run_async(bot.setsource(update, SimpleNamespace(args=["@nobotrights"], bot=fake_bot)))
            self.assertIsNone(database.get_setting("source_chat_id"))
            self.assertIn("Add the bot as a channel administrator", message.reply_text.await_args.args[0])
        finally:
            for key, value in original.items():
                if value is None:
                    database.delete_setting(key)
                else:
                    database.set_setting(key, value)

    def test_source_indexer_accepts_only_configured_channel(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(type="channel", id=-1001234567892),
            text="A listing post", caption=None, message_id=11,
            forum_topic_created=None, forum_topic_edited=None,
        )
        update = SimpleNamespace(effective_message=message, effective_chat=message.chat)
        context = SimpleNamespace()
        with patch.object(bot, "source_chat_id", return_value=-1001234567892), \
             patch.object(bot, "index_message", new=AsyncMock(return_value="channel-listing")) as index_mock:
            self.run_async(bot.ingest_group_message(update, context))
        index_mock.assert_awaited_once_with(message, context)


    def test_channel_command_router_recognizes_bot_addressed_command_with_arguments(self):
        message = SimpleNamespace(
            text="/setsource@AbhinazhTestBot @estrownf", caption=None,
            chat=SimpleNamespace(type="channel", id=-100990, title="Source channel"),
        )
        update = SimpleNamespace(channel_post=message, edited_channel_post=None)
        fake_bot = SimpleNamespace(send_message=AsyncMock())
        with patch.object(bot, "configured_superadmin_id", return_value=1485615852):
            self.run_async(bot.channel_post_command_router(update, SimpleNamespace(bot=fake_bot)))
        fake_bot.send_message.assert_awaited_once()
        self.assertIn("/setsource @channelusername", fake_bot.send_message.await_args.kwargs["text"])

    def test_setadmin_rejects_anonymous_group_identity_with_clear_instruction(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(id=-1009010, type="supergroup", title="Admin group"),
            effective_user=None,
        )
        self.run_async(bot.setadmin(update, SimpleNamespace(args=[])))
        self.assertIn("anonymous/group identity", message.reply_text.await_args.args[0])


    def test_channel_photo_command_caption_is_not_indexed_as_listing(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(type="channel", id=-1001234567893),
            text=None, caption="/setsource @estrownf", message_id=22,
            forum_topic_created=None, forum_topic_edited=None,
        )
        update = SimpleNamespace(effective_message=message, effective_chat=message.chat)
        context = SimpleNamespace()
        with patch.object(bot, "source_chat_id", return_value=-1001234567893), \
             patch.object(bot, "index_message", new=AsyncMock()) as index_mock:
            self.run_async(bot.ingest_group_message(update, context))
        index_mock.assert_not_awaited()

    def test_local_archive_cleanup_reports_failed_items(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "keepme").mkdir()
            (root / "keepme" / "media.jpg").write_bytes(b"x")
            with patch.object(bot, "local_storage_root", return_value=root), \
                 patch.object(bot.shutil, "rmtree", side_effect=OSError("permission denied")):
                failures = bot.clear_local_media_archive()
            self.assertEqual(failures, 1)
            self.assertTrue((root / "keepme" / "media.jpg").exists())

    def test_index_cancel_edits_confirmation_message(self):
        query = FakeQuery("index:delete-cancel", user_id=1485615852)
        update, context = self.make_update_context(query)
        self.run_async(bot._callback_action_impl(update, context))
        self.assertEqual(len(query.edits), 1)
        self.assertEqual(len(query.message.reply_calls), 0)

    def test_failed_backup_blocks_index_deletion(self):
        query = FakeQuery("index:delete-confirm", user_id=1485615852)
        update, context = self.make_update_context(query)
        with patch.object(bot, "run_backup", new=AsyncMock(return_value=None)), \
             patch.object(database, "clear_index", side_effect=AssertionError("must not delete without backup")):
            self.run_async(bot._callback_action_impl(update, context))
        self.assertEqual(len(query.edits), 1)
        self.assertEqual(len(query.message.reply_calls), 0)

    def test_callback_error_does_not_send_a_second_chat_message(self):
        query = FakeQuery("home:search")
        update, context = self.make_update_context(query)
        with patch.object(bot, "_callback_action_impl", new=AsyncMock(side_effect=ValueError("test error"))):
            self.run_async(bot.callback_action(update, context))
        self.assertEqual(len(query.message.reply_calls), 0)
        self.assertEqual(len(query.answers), 1)

    def test_screen_edit_retries_without_keyboard_after_bad_request(self):
        query = FakeQuery("home:help")
        query.edit_error = bot.BadRequest("Bad Request: BUTTON_DATA_INVALID")
        result = self.run_async(bot.edit_callback_screen(
            query, "Help screen", reply_markup=bot.home_navigation_keyboard()
        ))
        self.assertTrue(result)
        self.assertEqual(len(query.edits), 2)
        self.assertIsNone(query.edits[1][1]["reply_markup"] if "reply_markup" in query.edits[1][1] else query.edits[1][1].get("reply_markup"))
        self.assertEqual(len(query.message.reply_calls), 0)

    def test_message_not_modified_is_not_reported_as_failure(self):
        query = FakeQuery("home:search")
        query.edit_error = bot.BadRequest("Message is not modified")
        ok = self.run_async(bot.edit_callback_screen(query, "same text"))
        self.assertTrue(ok)
        self.assertEqual(len(query.message.reply_calls), 0)

    def test_buy_notify_result_edits_existing_text_message_not_new_message(self):
        query = FakeQuery("act:testtoken")
        update, context = self.make_update_context(query)
        context.bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=501)),
            edit_message_text=AsyncMock(),
        )
        action = {"user_id": 123456, "action_type": "buy_query", "query": "rally lead K1700", "listing_key": ""}
        with patch.object(database, "consume_callback_action", return_value=(action, "ok")), \
             patch.object(bot, "admin_chat_id", return_value=-1009876), \
             patch.object(database, "add_bot_request", return_value=501), \
             patch.object(bot, "render_request_text", return_value="Admin request ticket"), \
             patch.object(database, "delete_callback_action"):
            self.run_async(bot._callback_action_impl(update, context))
        self.assertEqual(len(query.edits), 1)
        self.assertEqual(len(query.message.reply_calls), 0)
        self.assertIn("request has been sent", query.edits[0][1]["text"])
        self.assertIsNone(query.edits[0][1]["reply_markup"])

    def test_buy_notify_result_edits_existing_media_caption_not_new_message(self):
        query = FakeQuery("act:testtoken")
        query.message.caption = "Rally lead listing caption"
        query.message.photo = [object()]
        update, context = self.make_update_context(query)
        context.bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=502)),
            edit_message_text=AsyncMock(),
        )
        action = {"user_id": 123456, "action_type": "buy_listing", "query": "rally lead", "listing_key": "listing-1"}
        item = {"listing_key": "listing-1", "is_available": 1, "message_ids": [], "title": "Rally lead"}
        with patch.object(database, "consume_callback_action", return_value=(action, "ok")), \
             patch.object(bot, "admin_chat_id", return_value=-1009876), \
             patch.object(database, "get_listing", return_value=item), \
             patch.object(database, "add_bot_request", return_value=502), \
             patch.object(bot, "render_request_text", return_value="Admin request ticket"), \
             patch.object(database, "delete_callback_action"):
            self.run_async(bot._callback_action_impl(update, context))
        self.assertEqual(len(query.caption_edits), 1)
        self.assertEqual(len(query.edits), 0)
        self.assertEqual(len(query.message.reply_calls), 0)
        caption = query.caption_edits[0][1]["caption"]
        self.assertIn("Rally lead listing caption", caption)
        self.assertIn("request has been sent", caption)
        self.assertIsNone(query.caption_edits[0][1]["reply_markup"])

    def test_unconfigured_admin_group_edits_current_message(self):
        query = FakeQuery("act:testtoken")
        update, context = self.make_update_context(query)
        action = {"user_id": 123456, "action_type": "buy_query", "query": "trap", "listing_key": ""}
        with patch.object(database, "consume_callback_action", return_value=(action, "ok")), \
             patch.object(bot, "admin_chat_id", return_value=None):
            self.run_async(bot._callback_action_impl(update, context))
        self.assertEqual(len(query.edits), 1)
        self.assertEqual(len(query.message.reply_calls), 0)
        self.assertIn("not configured", query.edits[0][1]["text"])

    def test_failed_backup_blocks_admin_destructive_actions(self):
        for action, db_function in (("admin:delete-all-confirm", "clear_index"),
                                    ("admin:reset-confirm", "clear_operational_data")):
            with self.subTest(action=action):
                query = FakeQuery(action, user_id=1485615852)
                update, context = self.make_update_context(query)
                with patch.object(bot, "run_backup", new=AsyncMock(return_value=None)), \
                     patch.object(database, db_function, side_effect=AssertionError("must not mutate without backup")):
                    self.run_async(bot._callback_action_impl(update, context))
                self.assertEqual(len(query.edits), 1)
                self.assertEqual(len(query.message.reply_calls), 0)
                self.assertIn("BACKUP FAILED", query.edits[0][1]["text"])

    def test_failed_status_edit_removes_stale_keyboard_without_new_message(self):
        query = FakeQuery("act:testtoken")
        query.edit_error = bot.BadRequest("cannot edit message")
        ok = self.run_async(bot.edit_callback_status(query, "Request sent"))
        self.assertFalse(ok)
        self.assertEqual(len(query.message.reply_calls), 0)
        self.assertEqual(len(query.markup_edits), 1)
        self.assertIsNone(query.markup_edits[0][1]["reply_markup"])

    def test_backup_is_created_and_integrity_verified_before_success(self):
        import zipfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_dir = root / "backups"
            media_dir = root / "local_storage"
            backup_dir.mkdir()
            media_dir.mkdir()
            (media_dir / "sample.txt").write_text("sample media metadata", encoding="utf-8")
            with patch.object(bot, "backup_root", return_value=backup_dir), \
                 patch.object(bot, "local_storage_root", return_value=media_dir):
                backup = bot.create_automatic_backup("audit test")
            self.assertIsNotNone(backup)
            self.assertTrue(backup.exists())
            with zipfile.ZipFile(backup, "r") as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("bot_data.sqlite3", archive.namelist())
                self.assertIn("local_storage/sample.txt", archive.namelist())

    def test_backup_failure_does_not_leave_success_named_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup_dir = root / "backups"
            media_dir = root / "local_storage"
            backup_dir.mkdir()
            media_dir.mkdir()
            with patch.object(bot, "backup_root", return_value=backup_dir), \
                 patch.object(bot, "local_storage_root", return_value=media_dir), \
                 patch.object(database, "connect", side_effect=RuntimeError("database unavailable")):
                backup = bot.create_automatic_backup("expected failure")
            self.assertIsNone(backup)
            self.assertEqual(list(backup_dir.iterdir()), [])

    def test_amount_admin_notice_points_to_supported_source_edits(self):
        text = bot.build_amount_admin_notice_text({
            "title": "Rally lead",
            "topic_title": "Accounts 500-2000",
            "link": "https://t.me/example/12",
        })
        self.assertIn("edit the original source post's caption/text", text)
        self.assertIn("forum topic title", text)
        self.assertIn("re-index", text)
        self.assertIn("do not reply to this notice", text.lower())
        self.assertNotIn("reply to this message with the correct amount", text.lower())

    def test_admin_panel_text_has_real_line_breaks(self):
        text = bot.admin_panel_text(1485615852)
        self.assertIn("\n", text)
        self.assertNotIn("\\n", text)

    def test_keyboard_urls_reject_malformed_external_links(self):
        self.assertEqual(bot.safe_telegram_url("http://t.me/examplechannel"), "")
        self.assertEqual(bot.safe_telegram_url("https://evil.example/example"), "")
        self.assertEqual(bot.safe_telegram_url("https://t.me/example channel"), "")
        self.assertEqual(bot.safe_telegram_url("https://t.me/examplechannel"), "https://t.me/examplechannel")

    def test_no_message_delete_api_is_present_in_bot_source(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertNotIn("delete_message(", source)
        self.assertNotIn(".delete()", source)
        self.assertNotIn("deleteMessage(", source)


if __name__ == "__main__":
    unittest.main()
