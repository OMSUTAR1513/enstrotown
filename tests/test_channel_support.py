"""Regression tests for Telegram group/channel source-chat handling."""
import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from source_chat_utils import (  # noqa: E402
    normalize_source_chat_reference,
    source_chat_matches,
    source_sender_details,
)


class ChannelSourceTests(unittest.TestCase):
    def test_normalizes_channel_username_and_public_link(self):
        self.assertEqual(normalize_source_chat_reference("@estrownf"), "@estrownf")
        self.assertEqual(normalize_source_chat_reference("estrownf"), "@estrownf")
        self.assertEqual(normalize_source_chat_reference("https://t.me/estrownf"), "@estrownf")
        self.assertEqual(normalize_source_chat_reference("t.me/estrownf"), "@estrownf")

    def test_accepts_numeric_private_chat_id(self):
        self.assertEqual(normalize_source_chat_reference("-1001234567890"), -1001234567890)

    def test_rejects_private_invite_link_as_get_chat_reference(self):
        with self.assertRaises(ValueError):
            normalize_source_chat_reference("https://t.me/+privateInviteHash")

    def test_rejects_untrusted_external_link(self):
        with self.assertRaises(ValueError):
            normalize_source_chat_reference("https://example.com/channel")

    def test_only_matching_supported_chat_is_indexed(self):
        self.assertTrue(source_chat_matches("channel", -1001, -1001))
        self.assertTrue(source_chat_matches("supergroup", -1001, -1001))
        self.assertTrue(source_chat_matches("group", -1001, -1001))
        self.assertFalse(source_chat_matches("private", -1001, -1001))
        self.assertFalse(source_chat_matches("channel", -1002, -1001))
        self.assertFalse(source_chat_matches("channel", -1001, None))

    def test_channel_sender_metadata_is_preserved(self):
        sender_chat = SimpleNamespace(id=-1001, title="ESTROTOWN TEST", username="estrownf")
        self.assertEqual(
            source_sender_details(None, sender_chat),
            (-1001, "ESTROTOWN TEST", "estrownf"),
        )

    def test_user_sender_metadata_takes_precedence(self):
        user = SimpleNamespace(id=1485615852, first_name="Mandar", last_name="", username="Mandar2004")
        sender_chat = SimpleNamespace(id=-1001, title="Group", username="group")
        self.assertEqual(
            source_sender_details(user, sender_chat),
            (1485615852, "Mandar", "Mandar2004"),
        )

    def test_channel_post_filter_is_registered_in_bot_application(self):
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
        found = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_handler"
            and any(
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Name)
                and arg.func.id == "MessageHandler"
                and len(arg.args) >= 1
                and isinstance(arg.args[0], ast.Attribute)
                and arg.args[0].attr == "CHANNEL_POSTS"
                for arg in node.args
            )
            for node in ast.walk(tree)
        )
        self.assertTrue(found, "The application must register channel_post and edited_channel_post updates")

    def test_private_channel_setup_is_present_and_group_admin_setup_remains_separate(self):
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("if chat.type == ChatType.PRIVATE:", source[source.index("async def setsource"):source.index("async def source_status")])
        self.assertIn("human_member.status not in (\"administrator\", \"creator\")", source)
        self.assertIn("bot_member.status not in (\"administrator\", \"creator\")", source)
        self.assertIn("async def setadmin", source)
        self.assertIn("Run /setadmin from your personal Telegram account inside your private admin-only group.", source)

    def test_channel_command_router_is_registered_for_new_and_edited_channel_posts(self):
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
        found = False
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_handler"):
                continue
            if not node.args:
                continue
            handler = node.args[0]
            if (
                isinstance(handler, ast.Call)
                and isinstance(handler.func, ast.Name)
                and handler.func.id == "MessageHandler"
                and len(handler.args) >= 2
                and isinstance(handler.args[0], ast.Attribute)
                and handler.args[0].attr == "CHANNEL_POSTS"
                and isinstance(handler.args[1], ast.Name)
                and handler.args[1].id == "channel_post_command_router"
            ):
                found = True
                break
        self.assertTrue(found, "Channel command routing must receive both new and edited channel posts")

    def test_user_help_explains_private_dm_channel_setup(self):
        config_source = (ROOT / "config.py").read_text(encoding="utf-8")
        self.assertIn("DM /setsource @channelusername", config_source)
        self.assertNotIn("/setsource [private invite link] — run in the listing group", config_source)


if __name__ == "__main__":
    unittest.main()
