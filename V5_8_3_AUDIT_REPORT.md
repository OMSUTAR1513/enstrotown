# V5.8.3 — Deep audit report

## Scope

Audited the V5.8.2 working tree against the requested workflow from the supplied Telegram screenshot: an authorized admin creates a post with `/add post`, supplies screenshots and account details, reviews/edits the caption, confirms publication, and has the new Telegram messages added to the local listing index. Existing search/index/alerts/Buy-Notify/channel configuration behavior was regression-tested.

## Defects found and corrected

1. **Bot-authored posts were skipped by the indexer.** `index_message` intentionally ignores bot-authored messages during ordinary update ingestion. The admin publisher called that same function, so a bot-published post in a group could remain unindexed. The indexer now has an explicit `allow_bot_post=True` path used only for messages returned by the confirmed publisher. Normal ingestion still ignores bot messages.
2. **Publish retry could create a duplicate.** The old handler published first, indexed second, and retained the same preview draft on any exception. If Telegram accepted the post but local indexing then failed, another `/confirm` sent the post again. Publication is now persisted before indexing; retries for a published/index-pending draft only retry indexing.
3. **A timeout could cause duplicate delivery.** Telegram send operations do not offer a client idempotency key. If a request times out after Telegram accepted it, the caller may not know whether it was sent. The bot now records `delivery_uncertain` and blocks automatic resend pending a destination check.
4. **Drafts were memory-only.** Restarting the bot lost the staged screenshots and caption. Draft state is now stored in SQLite and restored on the next command/update.
5. **Admin preview omitted the screenshots.** The old preview displayed only the caption text. The bot now sends the actual photo/album with the caption to the admin before approval.
6. **Caption edit flattened line breaks.** `/editcaption` used command arguments joined with spaces. It now parses the raw command message and preserves internal newlines.
7. **Workflow commands could be continued from another chat.** Lifecycle commands now require the private bot chat.
8. **Concurrent updates could race draft state and reorder screenshots.** Per-user async locks serialize post-workflow transitions, and the application now processes updates sequentially so album photos are handled before a following `/done`. Authorization is rechecked at each operation.
9. **A failed preview could still leave publication available.** If the admin's private preview could not be delivered, the draft previously remained in a confirmable state. The bot now marks it `preview_pending`, blocks `/confirm`, and provides `/preview` to resend it. Confirmation is unlocked only after preview delivery succeeds.
10. **Operational reset did not identify its new draft cleanup behavior.** It now reports that saved post drafts/recovery records are also cleared, the pre-reset backup captures them, and the reset confirmation/result warns the Superadmin about uncertain publications.
11. **Caption limits needed Unicode-safe counting.** Caption validation now conservatively counts UTF-16 units to avoid accepting emoji-heavy text that could exceed Telegram limits.
12. **A latent timezone helper had missing imports.** The unused-until-now `india_timezone()` helper now imports its required `zoneinfo` and `timedelta` types and has a regression test.

## Workflow now

1. Admin sends `/add post` privately.
2. Admin uploads 1–10 Telegram photos.
3. Admin sends `/done`.
4. Admin sends the exact account details/caption text.
5. Bot sends the image set and caption as a private preview.
6. Admin may send `/editcaption` followed by replacement caption text; line breaks are preserved and a refreshed preview is sent.
7. If preview delivery fails, `/preview` retries it. `/confirm` remains blocked until the preview has been delivered.
8. `/confirm` publishes to the configured `/setsource` chat and immediately indexes the Telegram-returned messages. `/cancel` discards drafts only before publication begins.

The admin supplies the account statistics. OCR may exist for search indexing, but this workflow does not fabricate, validate, or silently fill missing game values.

## Verification performed

- `python -m py_compile bot.py config.py database.py amounts.py search_engine.py time_utils.py source_chat_utils.py` — passed.
- `python -W error::ResourceWarning -m unittest discover -s tests -v` — **103 tests passed**. This includes the prior 91 tests plus new admin post workflow regression tests.
- `bash -n run_bot.sh install_termux.sh` — passed.
- Added tests cover durable draft read/write/delete, explicit indexing of bot-authored confirmed posts, caption newline preservation, UTF-16 caption limits, ordered update processing, private-chat-only workflow enforcement, failed-preview publication lock and recovery, operational-reset draft cleanup, timezone initialization, and retrying local indexing without republishing.

## Verification limitations

- The container could not reach PyPI, so `python-telegram-bot>=22.7,<23.0` and `pyflakes` could not be installed for a real-library import/lint pass. Offline tests use Telegram stubs. Syntax, database operations and mocked workflow branches are verified; PTB runtime compatibility still needs a real Termux install.
- No bot token, live destination chat or Android Termux session was available. Real send permissions, photo album delivery, Telegram caption acceptance and channel/group indexing have not been live-tested.
- No code change can guarantee exactly-once sends across an ambiguous network failure because Telegram's send API does not provide an idempotency key. The implemented safeguard chooses to block a potentially duplicate resend and asks the admin to inspect the destination.

## Deployment notes

Keep the existing `bot_data.sqlite3` and `local_storage/` during upgrade. The new draft table is created automatically at startup; existing listing data is preserved. Stop the old process before replacing source files, install `requirements.txt`, run the test commands in the README, then perform a controlled test post in a staging destination before public use.
