# Abhinazh Bot — Version History

## V5.8.4 — reply-to-delete, delete-all shortcut and forum topic selection

- Added a private mapping from authorized admin `/find` result summaries and copied screenshots to the listing key. An app Admin/Superadmin can reply `/delete` to either the result summary or any copied screenshot to permanently remove the listing from the searchable index, source-message index, alert-delivery state, search-result mappings and local listing media archive. The original Telegram source post is left untouched.
- Hard deletion now detaches historical admin request tickets from the removed listing without deleting the request history.
- Added `/deleteall` as a fixed-Superadmin-only shortcut to the existing confirmed and verified-backup-protected local index clear. It removes searchable account listings but deliberately preserves bot settings, roles, alerts, request tickets and original Telegram messages; historical tickets are detached from deleted listing IDs. It is not a full database wipe.
- Added `/setposttopic`: select the current topic in the configured forum supergroup, set a numeric topic ID, inspect the saved setting in private chat, or reset to General with `/setposttopic general`. Confirmed single-photo and media-album posts now pass the saved Telegram `message_thread_id`.
- No saved topic means new posts use the supergroup's General/default topic. Switching the configured source destination or disconnecting it clears the previous topic ID to prevent sending a new listing into the wrong group.
- Added regression tests for summary/media reply mapping, individual deletion, bulk mapping cleanup, ticket detachment, Superadmin-only `/deleteall`, topic selection/reset, source-change cleanup, and topic routing for both single photos and media albums.
- Verification: 114 offline tests passed; Python compilation passed. The project container does not have `python-telegram-bot` installed and no live bot token/group is available, so Telegram runtime and live forum-topic delivery still need deployment smoke testing. The python-telegram-bot v22.7 docs confirm `message_thread_id` is supported by both `send_photo` and `send_media_group`.

## Critical live-button fix

- Fixed `ZoneInfoNotFoundError` on Termux systems without the IANA timezone database. Request timestamps now fall back to India Standard Time (UTC+05:30).
- Added `tzdata` to requirements for installations where the timezone database package can be installed. The fallback means current installs do not have to reinstall dependencies just for this fix.
- Added two regression tests: normal India-time formatting and missing timezone database fallback.

## Preserved V5 features

- Buy requests route to the configured admin group and keep request/user mapping in local SQLite.
- Notify actions save eligible filter alerts and send a request ticket to the admin group. Future matching listings notify the user when indexed.
- Persistent local SQLite index, group setup, homepage, OCR-assisted indexing, `/audit`, `/index status`, `/index export`, `/index import`, `/index delete`, and sold workflows are unchanged.

## Verified here

- Syntax check passed for `bot.py`, `config.py`, `database.py`, `amounts.py`, `search_engine.py`, and `time_utils.py`.
- Offline tests passed: 26 tests, including the missing-timezone regression test.
- Live group delivery still must be verified from your Telegram bot after installation; this environment cannot access your private Telegram groups.


## V5.2 maintenance fixes

- Made `/indexreply` usage explicit for standalone/private invocations, and added guarded error logging plus visible feedback for failed imports.
- Added `/indexreply` to Telegram's command menu.
- Added currency-aware amount extraction for ₹ € £ ¥ $, INR/USD/EUR/GBP/JPY and common currency words, including ranges such as `₹500-780` and `¥680`.
- Added migrated `amount_currency` display metadata while preserving existing SQLite data.
- Added `lowest price`, `cheapest price`, `filter lowest`, and `filter cheapest` natural-language sort forms.
- Added a restart loop to `run_bot.sh` for unexpected non-zero bot exits.
- Fixed SQLite connection context handling so each `with connect()` block closes its connection instead of keeping handles open during long-running sessions.

## V5.2 verification on this build

- Python syntax check passed for all top-level Python modules.
- Offline unit tests passed: 26 tests, including currency symbols/ranges, currency filter parsing, `filter lowest` sort syntax, persisted currency marker, database connection closure, legacy-schema migration, local media metadata, admin reset preservation and amount-source priority.
- Shell syntax check passed for `run_bot.sh`.
- The live Telegram token, private group permissions and live Buy/Notify delivery are not testable from this build environment; use the live smoke test in Guide 1 after installing the ZIP.


## V5.2 feature update - amount, storage and admin controls

- Amount priority is now explicitly **topic -> written caption/text -> admin review**.
- Currency recognition accepts symbols, many currency names, and 3-letter currency codes beside numbers.
- Added `/filter` as a user-facing alias for `/find`; amount examples include `/filter amount 500-2000`, `under 1000`, `over 5000`, and currency-marked ranges.
- The homepage now has a dedicated **Filter by amount** button plus a simpler search/alerts/help layout.
- Indexed source media and caption/text are archived locally per listing under `local_storage/`.
- Every startup creates a SQLite-safe automatic backup containing the database plus the local listing archive.
- Added `/adminpanel` with confirmed **Delete all lists / index**, **Reset bot operational data**, and **Create backup now** controls.
- Destructive actions create a backup first.
- Added 26 offline tests covering currency priority, admin fallback behavior, amount filtering and reset persistence.


## V5.3 — Superadmin and application roles

- Fixed Superadmin: Telegram numeric ID `1485615852`; username is display-only and is never used for authorization.
- Added persistent `user_roles` and `role_audit_events` tables using additive SQLite schema creation.
- Added private-chat `/adminpanel` for the Superadmin with role management, role list, role audit history, backup, local index deletion and operational reset.
- Added role assignment for `ADMIN`, `MERCHANT`, `BUYER`, and `USER`; each change requires confirmation and is written to the audit log.
- Prevented the configured Superadmin ID from being changed or demoted through the bot interface or protected database setter call.
- Added server-side authorization for every role-management callback.
- Added application-Admin permissions for supported index and listing-status operations, while keeping Telegram native group-admin checks for source/admin group configuration.
- Restricted private admin-group reply forwarding to the configured Superadmin, app Admins, or native administrators of that group.
- Added regression tests for default roles, profile preservation, role auditing, duplicate role changes, protected Superadmin ID, and role/audit persistence after operational resets.
- Bumped `python-telegram-bot` minimum version to 22.7 for supported inline button styles.

## V5.3 verification

- Python compilation passed for all project Python files.
- Offline tests: 31 passed.
- `bash -n run_bot.sh` passed.
- Live Telegram callbacks, group permissions, and Buy/Notify delivery still require deployment-time testing with the actual bot and private groups.


## V5.4 — Home-button BadRequest diagnostics and fallback

- Improved home-menu callback handling so a failed callback acknowledgement is logged separately and does not automatically prevent the requested screen from being sent.
- When an optional inline keyboard is rejected by Telegram, the bot retries the home/help text without the keyboard so the requested guidance can still be delivered.
- Improved unexpected callback error messages: the exact short Telegram/API error detail is now shown to the user and the logs include callback data and user ID for diagnosis.
- Updated the displayed bot version to V5.4.
- Confirmed all 31 existing offline logic tests still pass and Python syntax compilation passes.
- The screenshot did not include the exact Termux traceback, so the original BadRequest root cause cannot be confirmed from the screenshot alone. The new diagnostics make the specific API rejection visible if it happens again.


## V5.5 — In-place navigation and safety audit

- Home menu buttons (Search, Filter by amount, My alerts, Help, Back to Home) now edit the existing bot menu message in place. They do not create replacement menu messages or delete prior chat messages.
- Admin-panel navigation, confirmation, cancel and completion screens now edit the existing panel message.
- Index-clear confirmation and completion screens update the same message.
- Callback exceptions are logged with stack traces and reported as an alert; the generic callback error handler no longer creates an extra chat message.
- Optional Telegram links are validated before being added to inline keyboards.
- “Message is not modified” is handled as a harmless no-op; keyboard-specific rejection can retry the same edit without the keyboard.
- Destructive index-clear, delete-all and reset operations now stop if the pre-action backup fails.
- Fixed literal `\\n` text in the admin-panel headings.
- Replaced imprecise Help wording and corrected the admin-panel access instructions.

## V5.5 audit status

- Static Python compilation, offline unit tests, callback-flow tests and shell syntax checks are run for this package.
- Live Telegram API, real callback delivery, Termux storage permissions and actual private group permissions are not verifiable in this build environment.


## V5.6 — Full callback/navigation and permission audit

- Buy/Notify result, unavailable-listing, missing-admin-group and delivery-failure states now edit the clicked listing text/caption in place. They no longer post replacement result messages into the buyer's chat.
- Text cards use Telegram's editMessageText operation; copied photo/video/document/audio cards use editMessageCaption. The original listing text/caption is retained where Telegram's 4,096-character message and 1,024-character caption limits allow, and the action keyboard is removed after the action.
- Buy/Notify callback tokens are consumed atomically in SQLite for the owning user only. This prevents rapid repeat taps from generating duplicate request tickets.
- Bulk index deletion and operational reset controls are restricted server-side to the configured Superadmin. Non-root Admins do not see those controls.
- `/index delete` and its confirmation callback are Superadmin-only.
- Rechecked callback handlers for replacement chat messages and deletion calls. No callback navigation handler uses `reply_text`, `send_message`, `delete_message` or a Message `.delete()` method. The Buy/Notify handler sends a necessary request ticket to the separate admin group; search commands intentionally send search result messages.
- Updated user-facing help/installation copy to match the permission and navigation behavior.

## V5.6 verification boundary

- Python compilation, all offline unit tests, database token-consumption tests, callback edit-in-place tests and shell syntax checks are run before packaging.
- Tests use local mocks/stubs for Telegram callback methods because the live Telegram token, Android Termux environment and private groups are not available in this build session. No live Telegram API test is claimed.
- The exact root cause of the original `BadRequest` in the supplied screenshot remains unconfirmed without its corresponding Termux traceback.


## V5.6 — Full navigation and security audit (final audit pass)

- All home, role, admin, index, sold-ticket and Buy/Notify callbacks edit the message that was clicked; the bot contains no Telegram delete-message API call for navigation.
- Buy/Notify cards show the result in the same text or media-caption message. The request ticket is delivered separately to the configured private admin group.
- Atomic one-use callback-token consumption prevents rapid repeat taps from creating duplicate Buy/Notify requests.
- Alert/listing delivery pairs are claimed transactionally before a DM is sent, preventing normal concurrent duplicate sends. A crash between Telegram accepting a DM and the local success record remains an at-least-once edge case because Telegram does not offer an idempotency key for `sendMessage`.
- Fixed owner-identity takeover: native group-admin status alone cannot claim the saved application owner. Group routing can be changed only by the fixed Superadmin or an app Admin who is also a native admin in that group. Only the Superadmin can set/repair the saved owner identity.
- Full index clear and operational reset remain Superadmin-only and require a verified SQLite + media ZIP backup.
- Local archive-cleanup failures are reported rather than described as full success.
- Corrected the amount-review notice: it now directs admins to edit the original source post or forum topic title; it no longer requests a reply to an unhandled notice.
- Fixed a ResourceWarning in the legacy-schema test fixture by explicitly closing the temporary SQLite connection.
- Final verification: Python compilation and all 62 offline tests pass, including with `ResourceWarning` treated as an error. See `V5_6_AUDIT_REPORT.md` for exact coverage and live-test limits.


## V5.7 — Help and Resume callback compatibility

- Reworked the screen editor to inspect the clicked Telegram message. Text messages are edited with `editMessageText`; photo/video/document/audio messages are edited with `editMessageCaption`.
- The Help screen on media-backed menus uses a compact caption-safe summary within Telegram's 1,024-character caption limit. Text-backed Help remains bounded by Telegram's 4,096-character message limit.
- Centralized Help rendering so `/help` and the Help button share one bounded output, including optional source-group/owner details without exceeding Telegram's message limit.
- Added legacy callback compatibility for `help`, `home_help`, `resume`, `home_resume`, `home`, `home:resume`. Legacy Resume callbacks return to the home screen only; the account-finder has no pause/resume indexing feature.
- Added regression tests for media-caption Help, text Help, oversized optional Help details, legacy Help/Resume callbacks and callback-handler registration.
- Verification: 70 offline tests passed with `ResourceWarning` treated as an error; Python compile checks passed. Live Telegram API/Termux tests remain outstanding because no bot token or live deployment was available and the build environment could not reach PyPI to install python-telegram-bot.

## V5.8 — Telegram channel-source support and channel command routing

- Added channel source setup from the Superadmin/app Admin's private bot chat using `/setsource @publicchannel` or a numeric chat ID.
- Before saving a channel, the bot verifies that the acting human is a Telegram channel administrator and that the bot is a channel administrator.
- Added a dedicated `MessageHandler` for new and edited channel-post commands. This is required because python-telegram-bot's `CommandHandler` does not handle channel posts. Channel command guidance is sent by private DM to the configured Superadmin; the bot does not try to post a reply into the channel.
- Added channel post and edited-channel-post ingestion into the existing source indexer.
- Preserved channel sender-chat identity in indexed records when Telegram does not provide a human `from_user`.
- Kept `/setadmin` restricted to a separate private admin group and required the person to send it from their own Telegram account, not as a channel.
- Added `/source` and the source setup commands to Telegram's command menu.
- Added channel/source-specific regression checks; the complete offline suite was later expanded and currently passes 91 tests (see `V5_8_CHANNEL_SUPPORT_AUDIT.md`).
- Live Telegram delivery and channel permission checks remain unverified until installed on the real bot.


## V5.8.1 — Channel command feedback and setup-copy correction

- Channel command routing now listens to both `channel_post` and `edited_channel_post` updates via `filters.UpdateType.CHANNEL_POSTS`.
- `/setsource`, `/setadmin`, `/unsetsource`, `/source`, and `/audit` channel-post notices are sent privately to the configured Superadmin. The handler does not try to reply in the channel, avoiding failures when the bot lacks the channel's `can_post_messages` right.
- Updated Help, README and START_HERE instructions to distinguish group setup from channel setup and stop telling users to run channel setup commands as channel posts.
- Added regression tests for private Superadmin notification, edited channel-post routing, and bot-addressed commands.
- Offline verification: 91 tests pass; Python compile and shell syntax checks pass. Live Telegram/Termux behavior is still unverified.


## V5.8.2
- Added admin `/add post` screenshot-to-caption publishing flow with preview, caption editing, confirmation, cancellation and immediate local indexing.


## V5.8.3 — Admin screenshot post builder audit and reliability fixes

- Corrected direct indexing of bot-authored posts: normal bot-authored updates remain ignored, while messages explicitly returned by the confirmed publisher are indexed.
- Added durable SQLite storage for in-progress admin post drafts, including photos, caption and publication/indexing state. Existing databases receive the new table through the normal additive startup schema.
- Changed the admin preview to show the actual uploaded photo or album with the caption before publication.
- Preserved caption line breaks when using `/editcaption`.
- Added per-admin async locks around draft transitions and sequential update processing so media-album photos cannot race ahead of `/done`.
- Added publication-state tracking. Once Telegram confirms the send, a later `/confirm` retries indexing only; it cannot create a second post. Ambiguous network outcomes are held for a manual destination check rather than automatically resent.
- Added `/preview` recovery and a `preview_pending` state. A draft cannot be published unless the admin's private screenshot/caption preview was successfully delivered.
- Rechecked admin authorization at each draft operation, restricted workflow commands to the private bot chat, capped uploads at 10 screenshots, and included the screenshot/caption workflow in Help and setup documentation.
- Added regression coverage for persisted drafts, bot-authored post indexing, caption line breaks/UTF-16 limits, serialized album updates, timezone initialization and index retry without duplicate publication.
- Fixed missing `zoneinfo`/`timedelta` imports in the existing India-time helper.
- Verification in this build environment: 103 offline tests pass; Python compilation and shell syntax checks are run. The container could not reach PyPI, so the declared python-telegram-bot runtime package could not be installed here; real Telegram/Termux publishing remains an external acceptance test.


## V5.8.5 — structured account entry, merchant posting and multiple destinations

- Replaced the single free-text details prompt in `/add post` with a guided form collecting account number, kingdom/migration, account type, might, three base stats, price, artifact/blessed, champion/cup, castle/mana, familiars/binding and extra searchable terms. Account number, might and three-stat values are required; optional fields support `/skip` and `/back`.
- Generated the buyer caption from submitted values; preserved `/editcaption`, actual media preview, `/preview`, `/confirm` and `/cancel`. The seller contact line uses the creator's Telegram username when available.
- Approved `MERCHANT` users can create and publish their own private-chat listing drafts. Merchant permission does not extend to admin-panel operations, role management, source setup or destination management.
- Added a persistent `publish_destinations` table and Superadmin `/adddestination`, `/removedestination` and `/destinations` commands. The existing `/setsource` chat remains the primary index/publish target.
- Added per-destination publication state. The primary post is indexed once; extra targets receive copies of the already-published media. Successful copies are skipped on retry, definitive Telegram rejections can be retried, and ambiguous sends are held for manual checking.
- Added tests for merchant permission boundaries, form caption generation and currency-aware amount extraction, destination persistence and no-duplicate retry logic.
- Local verification: Python compilation passed, shell syntax checks passed, and 118 offline tests passed with `ResourceWarning` treated as an error. This environment does not have `python-telegram-bot` installed and no live bot token/destination is available, so Telegram API/Termux behavior remains a required deployment smoke test.
