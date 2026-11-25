"""Offline tests for topic-first amount parsing, matching, and request storage."""
import os
import sys
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DB = Path(tempfile.gettempdir()) / f"listing_bot_test_{os.getpid()}.sqlite3"
os.environ["BOT_DB_PATH"] = str(TEST_DB)

import database  # noqa: E402
from amounts import extract_listing_amount, extract_listing_currency  # noqa: E402
from search_engine import evaluate_listing, parse_query  # noqa: E402
import time_utils  # noqa: E402


class ListingLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def setUp(self):
        # Each test uses unique message/listing IDs and its own database path.
        pass

    def test_callback_action_token_can_only_be_consumed_once_by_owner(self):
        token = f"consume-once-{os.getpid()}"
        database.add_callback_action(token, 87654321, "buy_query", "trap", "")
        action, status = database.consume_callback_action(token, 111111)
        self.assertIsNone(action)
        self.assertEqual(status, "forbidden")
        action, status = database.consume_callback_action(token, 87654321)
        self.assertEqual(status, "ok")
        self.assertEqual(action["action_type"], "buy_query")
        action, status = database.consume_callback_action(token, 87654321)
        self.assertIsNone(action)
        self.assertEqual(status, "missing")

    def test_alert_delivery_claim_is_atomic_and_blocks_duplicate_notifications(self):
        alert_id = database.add_alert(880000000 + os.getpid(), "K1700 5champ")
        listing_key = f"alert-claim-{os.getpid()}"
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _: database.claim_alert_delivery(alert_id, listing_key),
                range(8),
            ))
        self.assertEqual(sum(results), 1)
        self.assertTrue(database.mark_alert_delivered(alert_id, listing_key))
        self.assertFalse(database.claim_alert_delivery(alert_id, listing_key))
        with database.connect() as conn:
            row = conn.execute(
                "SELECT status FROM alert_deliveries WHERE alert_id=? AND listing_key=?",
                (alert_id, listing_key),
            ).fetchone()
        self.assertEqual(row["status"], "sent")

    def test_failed_alert_delivery_can_be_retried(self):
        alert_id = database.add_alert(881000000 + os.getpid(), "emperor")
        listing_key = f"alert-retry-{os.getpid()}"
        self.assertTrue(database.claim_alert_delivery(alert_id, listing_key))
        database.mark_alert_delivery_failed(alert_id, listing_key, "blocked by user")
        self.assertTrue(database.claim_alert_delivery(alert_id, listing_key))
        self.assertTrue(database.mark_alert_delivered(alert_id, listing_key))
        self.assertFalse(database.claim_alert_delivery(alert_id, listing_key))

    def test_legacy_alert_delivery_table_migrates_without_losing_sent_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy_path = Path(folder) / "legacy.sqlite3"
            conn = sqlite3.connect(legacy_path)
            try:
                conn.execute("""CREATE TABLE alert_deliveries (
                    alert_id INTEGER NOT NULL,
                    listing_key TEXT NOT NULL,
                    delivered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(alert_id, listing_key)
                )""")
                conn.execute(
                    "INSERT INTO alert_deliveries(alert_id, listing_key) VALUES(?, ?)",
                    (444, "legacy-sent"),
                )
                conn.commit()
            finally:
                conn.close()
            with patch.object(database, "DB_PATH", legacy_path):
                database.init_db()
                with database.connect() as conn:
                    columns = {row[1] for row in conn.execute("PRAGMA table_info(alert_deliveries)")}
                    row = conn.execute(
                        "SELECT status FROM alert_deliveries WHERE alert_id=? AND listing_key=?",
                        (444, "legacy-sent"),
                    ).fetchone()
                self.assertTrue({"status", "claimed_at", "last_error"}.issubset(columns))
                self.assertEqual(row["status"], "sent")

    def test_currency_filter_parser_accepts_symbols_codes_and_range_words(self):
        cases = {
            "₹500-780": (500.0, 780.0),
            "¥680": (680.0, 680.0),
            "£100": (100.0, 100.0),
            "500-780 rupees": (500.0, 780.0),
            "price 100-500 GBP": (100.0, 500.0),
            "price under ¥680": (None, 680.0),
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                opts = parse_query(query)["options"]
                self.assertEqual((opts["price_min"], opts["price_max"]), expected)

    def test_index_saves_detected_currency_without_losing_existing_index_fields(self):
        key = database.upsert_source_part(
            chat_id=-1001999990001, message_id=31, media_group_id=None,
            message_text="Rally lead account Price: ₹500-780", ocr_text="",
            created_at="2026-10-24T00:01:00+00:00", topic_id=0, topic_title="",
        )
        item = database.get_listing(key)
        self.assertEqual(item["amount_min"], 500.0)
        self.assertEqual(item["amount_max"], 780.0)
        self.assertEqual(item["amount_currency"], "₹")
        self.assertIn("Rally lead account", item["body"])

    def test_topic_amount_takes_priority_over_caption_amount(self):
        self.assertEqual(
            extract_listing_amount("Accounts 2k-5k", "Price: $900"),
            (2000.0, 5000.0, "topic"),
        )

    def test_explicit_caption_amount_is_used_without_topic_amount(self):
        self.assertEqual(
            extract_listing_amount("Restricted kd1234-kd1769", "Price: $900"),
            (900.0, 900.0, "message"),
        )

    def test_kingdom_range_is_not_mistaken_for_amount(self):
        self.assertEqual(
            extract_listing_amount("Restricted kd1234-kd1769", "No listed price"),
            (None, None, "missing"),
        )

    def test_topic_band_price_filter_uses_intersection(self):
        key = database.upsert_source_part(
            chat_id=-1001999990001, message_id=11, media_group_id=None,
            message_text="K1700, 5 champs, price: $900", ocr_text="Rally Lead",
            created_at="2026-10-23T10:00:00+00:00", topic_id=100,
            topic_title="Accounts 2k-5k",
        )
        item = database.get_listing(key)
        self.assertEqual(item["amount_min"], 2000.0)
        self.assertEqual(item["amount_max"], 5000.0)
        self.assertIsNotNone(evaluate_listing(item, "price:3000-4000"))
        self.assertIsNone(evaluate_listing(item, "price:6000-7000"))
        self.assertIsNotNone(evaluate_listing(item, "K1700 5champ"))

    def test_topic_label_backfills_indexed_messages(self):
        key = database.upsert_source_part(
            chat_id=-1001999990001, message_id=12, media_group_id=None,
            message_text="K1500 account", ocr_text="", created_at="2026-10-23T10:01:00+00:00",
            topic_id=101, topic_title="",
        )
        database.set_topic_title(101, "Accounts 0-500")
        item = database.get_listing(key)
        self.assertEqual(item["topic_title"], "Accounts 0-500")
        self.assertEqual(item["amount_min"], 0.0)
        self.assertEqual(item["amount_max"], 500.0)

    def test_ocr_currency_looking_game_text_is_not_a_price(self):
        self.assertEqual(
            extract_listing_amount("Restricted kd1234-kd1769", "7 champ mythic", "€6:26.9K\n15:03:18"),
            (None, None, "missing"),
        )

    def test_photo_ocr_amount_requires_explicit_label(self):
        self.assertEqual(extract_listing_amount("Restricted", "", "Price: 750"), (None, None, "missing"))
        self.assertEqual(extract_listing_amount("Restricted", "", "€6:26.9K"), (None, None, "missing"))

    def test_original_bot_style_filters(self):
        parsed = parse_query("K1500 K1700 5champ 7d cheapest")
        self.assertEqual(parsed["options"]["kingdom_min"], 1500)
        self.assertEqual(parsed["options"]["kingdom_max"], 1700)
        self.assertEqual(parsed["options"]["posted_within_hours"], 168)
        self.assertEqual(parsed["options"]["sort"], "cheapest")
        price = parse_query("price 30-50 cheapest")
        self.assertEqual(price["options"]["price_min"], 30.0)
        self.assertEqual(price["options"]["price_max"], 50.0)


    def test_currency_symbols_and_ranges_are_amounts(self):
        cases = [
            ("Restricted", "Selling for ₹500-780", (500.0, 780.0, "message")),
            ("Restricted", "Price: ¥680", (680.0, 680.0, "message")),
            ("Restricted", "£1,200 account", (1200.0, 1200.0, "message")),
            ("Restricted", "$500-$780", (500.0, 780.0, "message")),
            ("Restricted", "780 yen", (780.0, 780.0, "message")),
            ("Restricted", "500-780 rupees", (500.0, 780.0, "message")),
        ]
        for topic, text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(extract_listing_amount(topic, text), expected)

    def test_currency_marker_is_saved_for_display(self):
        self.assertEqual(extract_listing_currency("", "Price: ₹500-780", "", source="message"), "₹")
        self.assertEqual(extract_listing_currency("", "asking 680 yen", "", source="message"), "JPY")

    def test_cheapest_natural_language_filters(self):
        for query in ("filter lowest", "filter cheapest", "lowest price", "cheapest price"):
            with self.subTest(query=query):
                parsed = parse_query(query)
                self.assertEqual(parsed["options"]["sort"], "cheapest")
                self.assertEqual(parsed["terms"], [])

    def test_startup_recalculates_legacy_false_ocr_amount(self):
        key = database.upsert_source_part(
            chat_id=-1001999990001, message_id=14, media_group_id=None,
            message_text="2000+m.m\n7 champ mythic", ocr_text="€6:26.9K\n15:03:18",
            created_at="2026-10-23T10:03:00+00:00", topic_id=103,
            topic_title="Restricted kd1234-kd1769",
        )
        with database.connect() as conn:
            conn.execute(
                "UPDATE source_listings SET amount_min=6, amount_max=6, amount_source='ocr' WHERE listing_key=?",
                (key,),
            )
        database.init_db()
        item = database.get_listing(key)
        self.assertIsNone(item["amount_min"])
        self.assertEqual(item["amount_source"], "missing")

    def test_sold_listing_disappears_from_search_index(self):
        key = database.upsert_source_part(
            chat_id=-1001999990001, message_id=13, media_group_id=None,
            message_text="K1700 5 champ listing", ocr_text="", created_at="2026-10-23T10:02:00+00:00",
            topic_id=102, topic_title="Accounts 2k-5k",
        )
        self.assertIn(key, {item["listing_key"] for item in database.get_listings()})
        item = database.set_listing_availability_by_key(key, False)
        self.assertIsNotNone(item)
        self.assertNotIn(key, {row["listing_key"] for row in database.get_listings()})

    def test_database_connection_context_closes_handle(self):
        with database.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_admin_reply_mapping_storage(self):
        request_id = database.add_bot_request(
            user_id=123, username="sample_user", first_name="Sample User",
            request_type="notify_query", requirement="K1700 5champ", listing_key="",
            admin_chat_id=-1001999990002, admin_message_id=88,
        )
        row = database.get_request_by_admin_message(-1001999990002, 88)
        self.assertEqual(row["id"], request_id)
        self.assertEqual(row["username"], "sample_user")
        self.assertEqual(row["user_id"], 123)

    def test_index_export_snapshot_contains_listing_and_source_part(self):
        key = database.upsert_source_part(
            chat_id=-1001999990001, message_id=21, media_group_id=None,
            message_text="K1700 5 champ export test", ocr_text="", 
            created_at="2026-10-23T10:10:00+00:00", topic_id=0, topic_title="",
        )
        snapshot = database.export_index_snapshot()
        self.assertIn(key, {row["listing_key"] for row in snapshot["listings"]})
        self.assertIn(21, {row["message_id"] for row in snapshot["source_parts"]})

    def test_hard_delete_removes_listing_and_message_parts(self):
        key = database.upsert_source_part(
            chat_id=-1001999990001, message_id=22, media_group_id=None,
            message_text="K1700 sold hard delete test", ocr_text="",
            created_at="2026-10-23T10:11:00+00:00", topic_id=0, topic_title="",
        )
        self.assertTrue(database.delete_listing_by_key(key))
        self.assertIsNone(database.get_listing(key))
        with database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM source_parts WHERE listing_key=?", (key,)).fetchone()[0], 0)

    def test_admin_search_message_reference_resolves_and_is_removed_with_listing(self):
        key = database.upsert_source_part(
            chat_id=-1001999990001, message_id=2201, media_group_id=None,
            message_text="K1700 searchable delete mapping", ocr_text="",
            created_at="2026-10-23T10:11:00+00:00", topic_id=0, topic_title="",
        )
        database.add_listing_search_reference(1485615852, 9901, key)
        self.assertEqual(database.get_listing_key_by_search_reference(1485615852, 9901), key)
        self.assertTrue(database.delete_listing_by_key(key))
        self.assertIsNone(database.get_listing_key_by_search_reference(1485615852, 9901))
        self.assertIsNone(database.get_listing(key))

    def test_clear_index_removes_admin_search_reply_mappings(self):
        key = database.upsert_source_part(
            chat_id=-1001999990001, message_id=2202, media_group_id=None,
            message_text="K1700 delete all mapping", ocr_text="",
            created_at="2026-10-23T10:12:00+00:00", topic_id=0, topic_title="",
        )
        database.add_listing_search_reference(1485615852, 9902, key)
        database.clear_index()
        self.assertIsNone(database.get_listing_key_by_search_reference(1485615852, 9902))

    def test_request_history_is_detached_when_listing_is_hard_deleted(self):
        key = database.upsert_source_part(
            chat_id=-1001999990001, message_id=2203, media_group_id=None,
            message_text="K1700 request detach", ocr_text="",
            created_at="2026-10-23T10:13:00+00:00", topic_id=0, topic_title="",
        )
        request_id = database.add_bot_request(
            user_id=111222333, username="delete_test", first_name="Delete Test",
            request_type="buy_listing", requirement="K1700", listing_key=key,
            admin_chat_id=-1001999990002, admin_message_id=9903,
        )
        self.assertTrue(database.delete_listing_by_key(key))
        self.assertEqual(database.get_request_by_id(request_id)["listing_key"], "")

    def test_request_time_formats_to_india_time(self):
        value = time_utils.format_created_at("2026-10-24T00:00:00+00:00")
        self.assertIn("05:30:00 AM IST", value)

    def test_request_time_falls_back_when_termux_has_no_tzdata(self):
        with patch.object(time_utils, "ZoneInfo", side_effect=time_utils.ZoneInfoNotFoundError("Asia/Kolkata")):
            value = time_utils.format_created_at("2026-10-24T00:00:00+00:00")
        self.assertIn("05:30:00 AM IST", value)

    def test_clear_index_preserves_settings_and_alerts(self):
        database.set_setting("test_preserved_setting", "yes")
        alert_id = database.add_alert(555001, "K1700 5champ")
        key = database.upsert_source_part(
            chat_id=-1001999990001, message_id=23, media_group_id=None,
            message_text="K1700 clear index test", ocr_text="",
            created_at="2026-10-23T10:12:00+00:00", topic_id=0, topic_title="",
        )
        request_id = database.add_bot_request(
            user_id=555002, username="history_test", first_name="History Test",
            request_type="buy_listing", requirement="K1700", listing_key=key,
            admin_chat_id=-1001999990002, admin_message_id=9904,
        )
        counts = database.clear_index()
        self.assertGreaterEqual(counts["listings"], 1)
        self.assertEqual(database.count_all_listings(), 0)
        self.assertEqual(database.count_source_parts(), 0)
        self.assertEqual(database.get_setting("test_preserved_setting"), "yes")
        self.assertIn(alert_id, {row["id"] for row in database.get_active_alerts()})
        self.assertEqual(database.get_request_by_id(request_id)["listing_key"], "")


    def test_any_currency_code_and_name_in_topic_has_priority(self):
        self.assertEqual(extract_listing_amount("USD 250", "Price: ₹999"), (250.0, 250.0, "topic"))
        self.assertEqual(extract_listing_amount("250 dollars", "Price: ₹999"), (250.0, 250.0, "topic"))
        self.assertEqual(extract_listing_amount("₩3000", "Price: ₹999"), (3000.0, 3000.0, "topic"))

    def test_caption_amount_used_when_topic_has_no_amount(self):
        self.assertEqual(extract_listing_amount("Rally Lead", "500 CHF"), (500.0, 500.0, "message"))
        self.assertEqual(extract_listing_amount("Rally Lead", "AED 750-900"), (750.0, 900.0, "message"))

    def test_ocr_does_not_override_admin_fallback(self):
        self.assertEqual(extract_listing_amount("Rally Lead", "No listed price", "Price: 750"), (None, None, "missing"))

    def test_operational_reset_also_clears_and_reports_saved_post_drafts(self):
        user_id = 990000000 + os.getpid()
        database.save_admin_post_draft(user_id, {
            "stage": "preview", "photos": ["file-id"], "caption": "#6558"
        })
        counts = database.clear_operational_data()
        self.assertEqual(counts["post_drafts"], 1)
        self.assertIsNone(database.get_admin_post_draft(user_id))

    def test_clear_operational_data_preserves_settings(self):
        database.set_setting("source_chat_id", "-1001")
        database.add_alert(555002, "K1700")
        database.upsert_source_part(
            chat_id=-1001999990002, message_id=24, media_group_id=None,
            message_text="K1700", caption_text="", ocr_text="",
            created_at="2026-10-24T01:00:00+00:00", topic_id=0, topic_title="",
        )
        counts = database.clear_operational_data()
        self.assertGreaterEqual(counts["listings"], 1)
        self.assertEqual(database.get_setting("source_chat_id"), "-1001")
        self.assertEqual(database.count_all_listings(), 0)


    def test_new_users_default_to_user_role_and_profile_updates_do_not_reset_role(self):
        target_id = 710000000 + os.getpid()
        database.upsert_user_profile(target_id, username="first_name", display_name="First Name")
        self.assertEqual(database.get_user_role(target_id), "USER")
        old, new, changed = database.set_user_role(target_id, "MERCHANT", 1485615852)
        self.assertEqual((old, new, changed), ("USER", "MERCHANT", True))
        database.upsert_user_profile(target_id, username="updated_name", display_name="Updated Name")
        self.assertEqual(database.get_user_role(target_id), "MERCHANT")
        record = database.get_user_role_record(target_id)
        self.assertEqual(record["username"], "updated_name")
        self.assertEqual(record["display_name"], "Updated Name")

    def test_publish_destinations_are_persisted_and_can_be_removed(self):
        chat_id = -1005000000000 - os.getpid()
        database.add_publish_destination(chat_id, "Marketplace Channel", "channel", "marketplace", 1485615852)
        item = database.get_publish_destination(chat_id)
        self.assertEqual(item["title"], "Marketplace Channel")
        self.assertEqual(item["chat_type"], "channel")
        self.assertEqual(item["username"], "marketplace")
        database.add_publish_destination(chat_id, "Updated Name", "supergroup", "marketplace", 1485615852)
        self.assertEqual(database.get_publish_destination(chat_id)["title"], "Updated Name")
        self.assertTrue(database.remove_publish_destination(chat_id))
        self.assertIsNone(database.get_publish_destination(chat_id))

    def test_role_change_writes_audit_event(self):
        target_id = 720000000 + os.getpid()
        old, new, changed = database.set_user_role(target_id, "ADMIN", 1485615852)
        self.assertEqual((old, new, changed), ("USER", "ADMIN", True))
        events, total = database.list_role_audit_events(limit=10, offset=0)
        self.assertGreaterEqual(total, 1)
        self.assertTrue(any(
            row["target_user_id"] == target_id
            and row["old_role"] == "USER"
            and row["new_role"] == "ADMIN"
            for row in events
        ))

    def test_same_role_does_not_create_duplicate_audit_event(self):
        target_id = 730000000 + os.getpid()
        database.set_user_role(target_id, "BUYER", 1485615852)
        _, _, changed = database.set_user_role(target_id, "BUYER", 1485615852)
        self.assertFalse(changed)
        events, _ = database.list_role_audit_events(limit=50, offset=0)
        target_events = [row for row in events if row["target_user_id"] == target_id]
        self.assertEqual(len(target_events), 1)

    def test_operational_reset_preserves_roles_and_role_audit_history(self):
        target_id = 750000000 + os.getpid()
        database.set_user_role(target_id, "ADMIN", 1485615852)
        before, before_total = database.list_role_audit_events(limit=50, offset=0)
        counts = database.clear_operational_data()
        self.assertEqual(database.get_user_role(target_id), "ADMIN")
        after, after_total = database.list_role_audit_events(limit=50, offset=0)
        self.assertGreaterEqual(before_total, 1)
        self.assertEqual(after_total, before_total)
        self.assertEqual(
            sum(1 for row in after if row["target_user_id"] == target_id),
            1,
        )

    def test_role_change_rejects_superadmin_and_unknown_roles(self):
        with self.assertRaises(ValueError):
            database.set_user_role(1485615852, "USER", 1485615852, protected_user_id=1485615852)
        with self.assertRaises(ValueError):
            database.set_user_role(740000000 + os.getpid(), "SUPERADMIN", 1485615852)

if __name__ == "__main__":
    unittest.main(verbosity=2)
