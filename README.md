# Abhinazh Bot V5.8.5

A Telegram account-finder bot for a private Lords Mobile listing group. It automatically indexes new source-group posts, helps buyers search by text and filters, stores alerts, and sends Buy/Notify requests to a private admin group.

## What this release changes

- Home, Help, Filter, Alerts, Admin, role-management and index-confirmation buttons update the existing bot message. They do not send a second navigation message and do not delete the original message.
- Buy/Notify results update the listing card that was clicked. For text cards the bot edits the text; for media cards it edits the caption. A request ticket is still sent to the separate private admin group because that is the actual request destination.
- One-use Buy/Notify tokens are consumed in one SQLite transaction. A rapid second tap cannot create another ticket from the same button.
- Only Telegram ID `1485615852` is the Superadmin. The username is shown as a label only; authorization uses the numeric ID.
- Only the Superadmin can clear the full index or reset operational data. A verified backup is required before those actions.
- Only the Superadmin or an app Admin who is also a native Telegram group administrator can change source/admin group routing. Only the Superadmin can set or repair the saved owner identity.
- If local-media cleanup fails, the result screen reports the failure instead of claiming that every file was removed.

## Important message behavior

A button used for navigation edits the current bot message. The bot does not delete that message and send a replacement.

Some new messages are normal and necessary: a `/find` search creates search results; a new matching alert is delivered as a new notification; and a Buy/Notify action creates a request ticket in the private admin group. Those are new events, not duplicate menu screens.

## Current features

- Automatic indexing of new messages, captions, photos, albums and edits from the configured source group.
- OCR-assisted image text extraction when Pillow and Tesseract are available.
- Search by account keywords, kingdom, price/amount, features and recency, with sorting.
- Saved alerts for future indexed matches, with delivery de-duplication.
- Buy and Notify request tickets to the private admin group.
- Admin replies routed to the correct requester.
- Mark sold / relist tools, topic labels and old-post import by replying with `/indexreply`.
- Superadmin role management with an audit history.
- SQLite + local-media backup, index export/import and a configuration audit.

## Roles

- `SUPERADMIN`: fixed Telegram ID `1485615852`; can promote/demote other application roles, view role history, back up, and perform confirmed full-index/reset actions.
- `ADMIN`: application administrator assigned by the Superadmin; can use supported administration tools and can configure groups only when also a Telegram administrator of that group.
- `MERCHANT`: approved account creator. Can create and publish listings through the private `/add post` workflow, but cannot use the admin panel, manage roles, or configure source/publication destinations.
- `BUYER`, `USER`: buyer and default application roles.

The Superadmin cannot be assigned from the role menu or demoted through the bot.

## Commands

### Buyers

- `/start` — open the home screen.
- `/help` — show search, filters and usage guidance.
- `/find <filters>` — search indexed listings.
- `/search <filters>` — alias for `/find`.
- `/delete` — app Admin/Superadmin: reply to the result summary or copied account image from your own `/find` result to permanently remove that listing from the local searchable database. Its original Telegram post is not deleted.
- `/deleteall` — immutable Superadmin only; run it in the bot private chat. It asks for confirmation, creates and verifies a backup, then clears all searchable listing records. Historical request tickets are retained but detached from deleted listing IDs. It preserves bot settings, roles, saved alerts and original Telegram posts; it is not a full SQLite wipe.
- `/setposttopic` — app Admin/Superadmin: when run inside the desired forum topic in the configured source supergroup, saves that topic for all future `/add post` publications. `/setposttopic general` clears the topic setting and publishes to General. `/setposttopic 123456` sets a known topic ID. Running it privately shows the current setting.
- `/filter <filters>` — alias for the same search/filter handler.
- `/alertme <filters>` — save an alert for future matching listings.
- `/alerts <filters>` and `/wishlist <filters>` — aliases for `/alertme`.
- `/myalerts` — list the current user's active alerts.
- `/cancelalert N` — cancel one of the user's alerts by its displayed number.
- `/stats` — show index, OCR and the user's alert counts in private chat.

### Admin and group setup

- `/adminpanel` — open the admin panel. The Superadmin sees root role-management and destructive controls; other authorized admins see only their permitted controls.
- `/setsource` — configure the primary source/indexing and publication chat from inside it as the Superadmin/app Admin who is also a Telegram group administrator; for a channel, send `/setsource @channelusername` in the bot's private chat. The human operator and bot must both be channel administrators.
- `/destinations` — view the primary source and additional publication groups/channels.
- `/adddestination @channelusername` or `/adddestination -100...` — Superadmin only; add an additional publication group/channel. The Superadmin must be a Telegram administrator there; the bot must be a member of groups or an administrator with posting permission in channels.
- `/removedestination @username` or `/removedestination -100...` — Superadmin only; stop sending future listings to an additional destination. This does not remove the primary indexing source or delete old posts.
- `/setadmin` — configure the private request group. Run it in that group with the same app-role and Telegram-admin requirements.
- `/source` — show the configured source chat and listing-index status.
- `/unsetsource` — disconnect the source chat. The fixed Superadmin can do this in private chat; group-source configuration roles can also disconnect from the configured group.
- `/settopicname [topic ID] <label>` — save a forum-topic label, such as `/settopicname Accounts 0-500` while inside that topic, or `/settopicname 123456 Accounts 0-500` by explicit topic ID.
- `/topicid` — show the current/replied-to forum topic ID.
- `/index status` — show local index counts.
- `/index export` — create and send a JSON index snapshot.
- `/index import` — reply to an uploaded JSON index snapshot with this command to merge it. The snapshot must belong to the configured source group.
- `/index delete` — Superadmin only; opens a confirmation. The bot verifies a backup before deleting local index data. `/deleteall` is the shorter command alias for this operation.
- `/indexreply` — reply to an old source-group post with this command to index it. Telegram bots cannot fetch arbitrary old group history without an update or a post reference.
- `/sold` — reply to a source post to mark its listing unavailable in search and future alerts.
- `/remove_listing` and `/removelisting` — aliases for `/sold`.
- `/relist` — reply to a previously sold/unavailable source post to restore it.
- `/audit` — check group configuration, bot membership, index counts, alert counts and OCR availability.

## Setup on Termux

### First installation

1. Install Termux packages and Python using the Termux app.
2. Extract the project folder and enter it.
3. Run:

```bash
bash install_termux.sh
export BOT_TOKEN='YOUR_BOTFATHER_TOKEN'
bash run_bot.sh
```

The install script creates `.venv`, installs requirements and uses Termux packages for Python/Pillow/Tesseract.

### Updating an existing installation

1. Stop the old bot with `Ctrl+C`.
2. Make a copy of `bot_data.sqlite3`, `local_storage/`, `backups/` and your token/configuration before replacing files.
3. Extract the new ZIP into the existing project folder. Do not overwrite your database or token file.
4. Run:

```bash
cd ~/abhinazh_bot
source .venv/bin/activate
python -m pip install -r requirements.txt --upgrade
python -m py_compile bot.py config.py database.py amounts.py search_engine.py time_utils.py
python -m unittest discover -s tests -v
bash -n run_bot.sh install_termux.sh
```

5. Start the bot with the same token and command you used before:

```bash
export BOT_TOKEN='YOUR_BOTFATHER_TOKEN'
bash run_bot.sh
```

The distributable ZIP does not contain a database, token, virtual environment, exports, backups or local media. Pending admin post drafts are stored in the existing SQLite database, which must be preserved during upgrades. SQLite schema changes are additive. Keep your existing `bot_data.sqlite3` and `local_storage/`.

## Amount review

If a new source listing has no detectable price, the bot posts an amount-review notice in the private admin group. Do not reply to that notice: the reply is not connected to the listing. Instead, edit the original source post caption/text to include the amount and currency, or update the forum topic title with `/settopicname`. The bot re-indexes edited source messages and topic-title updates.

## Group setup

The Superadmin or an app Admin must also be a Telegram administrator in each group they configure. The bot must be a member of both groups and have permission to read relevant messages and send messages in the admin group.

In the private listing/source group:

```text
/setsource https://t.me/+YOUR_INVITE_LINK
```

In the private admin/request group:

```text
/setadmin
```

Then, in private chat with the bot, run `/audit` and read each result. The bot must be allowed to receive group messages; check Telegram's privacy-mode setting with BotFather if indexing does not start.

## Button and message rules

- Tapping Search, Filter, My alerts or Help edits the home message.
- Tapping Back/Home edits the current panel message.
- Role lookups and confirmations update the existing Superadmin screen.
- Buy/Notify updates the clicked listing card and sends the new request ticket only to the separate admin group.
- The bot contains no `deleteMessage` call for normal menu navigation.
- If Telegram refuses an in-place edit (for example, a message is too old, inaccessible or no longer editable), the bot logs the exact error and attempts to remove the stale keyboard. It does not send a replacement menu.

## Data safety

- Before full-index deletion or operational reset, the bot creates a SQLite + local-media backup. Operational reset also clears saved admin-post drafts and their publication/index-recovery records; check any uncertain publication at its destination before resetting. The backup is checked using SQLite `PRAGMA integrity_check` and ZIP CRC validation before the action proceeds.
- If backup creation fails, the destructive action is cancelled.
- If local files cannot be removed after a successful database clear, the result screen reports the number of failed archive items and the Termux log records their paths.
- Index export includes listing text/OCR, seller labels and source Telegram IDs. Keep it private.

## Remaining marketplace-specification gaps

V5.8.3 introduced the original admin screenshot-to-caption publisher. V5.8.5 extends it with guided searchable account fields, approved Merchant listing creation and additional group/channel publication destinations. The creator still supplies and checks all game statistics; the bot does not infer values from screenshots. The existing Buy/Notify request routing remains separate from listing publication. See `PDF_SPEC_GAP_REPORT.md` for broader marketplace-specification gaps.

## V5.7 Help and old-button compatibility

- The Help button edits the currently displayed bot message; it never creates a replacement screen or deletes the previous one.
- Help navigation supports current callback data (`home:help`) and older standalone values (`help`, `home_help`).
- A legacy `resume` callback is accepted as a shortcut back to the home screen. It does not restart indexing: this project has no pause/resume indexing feature or Resume button in its current menus.
- When the current menu message contains media, the bot uses Telegram's caption-edit method rather than trying to edit a photo/video/document message as plain text. The media Help summary is kept within Telegram's 1,024-character caption limit.
- Text-message Help remains within Telegram's 4,096-character text limit, including optional source-group and owner details.

## Verification boundary

The local source compiles, shell scripts pass syntax checks, and all 103 offline regression tests pass with `ResourceWarning` treated as an error. These checks use local Telegram test doubles because python-telegram-bot could not be installed in this offline build environment. They do not contact Telegram. Live behavior still depends on a valid token, the installed python-telegram-bot version, bot membership/permissions, Termux storage and the actual message type/state. Use `/audit` and test the listed buttons with a staging bot before treating a deployment as production-verified.

## V5.8: Source channel support

The source can now be a Telegram group/supergroup OR a Telegram channel.

### Connect a source channel

1. Add the bot to the target channel as an administrator. It must be a channel member so Telegram can deliver channel posts to it.
2. The configured Superadmin or an app Admin must also be an administrator of that channel.
3. Open the bot's private chat using the personal Telegram account (not a channel identity).
4. Open the bot's private chat and send `/setsource @channelusername` (for example, `/setsource @estrownf`). A public `https://t.me/channelusername` link or the numeric chat ID also works. Do not post `/setsource` as the channel identity.
5. Run `/source` and `/audit` in the private bot chat to inspect the saved source and bot membership.
6. Publish a new text/photo post in the channel and confirm that the listing count increases. Edit its caption and confirm that the indexed record updates.

Private invite links such as `https://t.me/+...` cannot be resolved by `getChat`; use the channel's numeric `-100...` ID instead after adding the bot.

### Configure the private admin request group

`/setadmin` is different from `/setsource`. Run it from the Superadmin's personal Telegram account inside a separate private group that contains the bot. Do not send `/setadmin` as a channel post. The bot's privacy mode is a group setting; changing it does not make `CommandHandler` process channel posts.

### Why V5.7 did not respond in the screenshot

The screenshot's chat header says "3 subscribers" and the composer says "Broadcast", so it is a Telegram channel rather than a group. In python-telegram-bot v22.7, `CommandHandler` does not handle channel posts. The V5.7 code registered `/setsource` and `/setadmin` only as `CommandHandler`s, and its indexer only registered normal message/edited-message updates. V5.8 adds channel-post routing and `filters.UpdateType.CHANNEL_POSTS` indexing. Channel commands are routed to the configured Superadmin by private message rather than replied to in the channel, because the bot may not have the channel's `can_post_messages` right. Sources: Telegram Bot API update types; python-telegram-bot v22.7 CommandHandler and filters documentation.


## V5.8.1 channel command routing correction

Channel command posts (including edited channel posts) are routed with `filters.UpdateType.CHANNEL_POSTS`. The bot does not try to reply inside the channel; it privately sends setup instructions to the fixed Superadmin. Before testing, the Superadmin must open the bot DM and press Start. To connect the screenshot channel, add the bot as channel administrator, confirm the Superadmin is also a channel administrator, then privately send `/setsource @estrownf` (if `estrownf` is the channel username). `/setadmin` must still be sent from the Superadmin's personal account inside a separate private group. See `V5_8_1_CHANNEL_COMMAND_FIX.md`.


## Admin-created posts (V5.8.5)

Authorized Admins, the Superadmin, and approved Merchants can create a listing directly from the bot without manually publishing it first. Merchant permissions are limited to their own private listing-creation workflow; Merchants do not receive admin-panel, role-management, source-configuration or destination-management permissions.

1. In the bot's private chat, send `/add post`.
2. Upload 1–10 account screenshots as Telegram photos.
3. Send `/done` after the final screenshot.
4. Answer the guided searchable account fields: account number, kingdom/migration, account type, might, base stats, price, artifact/blessed counts, champions/cups, castle/mana details, familiars/account binding and extra searchable features. Account number, might and all three base stats are required; optional fields accept `/skip`. Use `/back` to correct the previous answer.
5. The bot generates a caption from the supplied fields and sends the actual screenshot album/caption as a preview.
6. Use `/editcaption` followed by the complete new caption to revise it. Line breaks are preserved.
7. If the preview does not arrive, use `/preview` to retry it. Publishing stays locked until the preview is delivered.
8. Send `/confirm` to publish to the primary source/index destination and every additional destination configured by the Superadmin, or `/cancel` to discard before publication.

The configured `/setsource` chat remains the primary publishing destination and the chat that receives the new listing in the local index. The Superadmin can add further publication destinations without changing the primary index source. The bot needs permission to send photos/media to the primary chat, and copy permissions in every additional destination. Captions must fit Telegram's 1,024-character media-caption limit. Account statistics are supplied by the Admin/Merchant; the bot checks required fields but does not infer values from screenshots.

Drafts are saved in SQLite, so an unfinished draft survives a bot restart. Primary publishing, indexing and additional destination delivery are tracked separately. Successful additional copies are never sent again on retry; `/confirm` retries only destinations that received a definite rejection. If Telegram's response is ambiguous because of a timeout/network error, the bot holds that destination for manual checking instead of risking a duplicate post.


## V5.8.4 changes — admin listing deletion and forum-topic publishing

- Admin `/find` results now store a private mapping from the bot's result summary and copied account messages back to the indexed listing. Reply to either the summary or any copied screenshot with `/delete` to permanently remove that listing from search and future alert delivery. Original Telegram source messages are not deleted.
- `/deleteall` is a Superadmin-only shortcut for clearing every searchable account listing. It uses the existing confirmation callback and verified-backup gate. Historical request tickets are retained with their deleted listing references detached. It deliberately does not erase the entire SQLite database or remove bot settings, roles or alerts.
- `/setposttopic` saves the forum topic used by future `/add post` confirmations. Run it inside the desired topic of the configured forum supergroup; `/setposttopic general` resets publishing to the General/default topic. New posts are routed using Telegram `message_thread_id`; existing messages are not moved.
- If no post topic is configured, new publications go to the supergroup's General/default topic.


## V5.8.5 changes — structured listing forms, merchant posting and multiple destinations

- Added a guided account-field form after screenshot upload. The caption is generated from supplied searchable values, with required account number, might and three base-stat values; optional fields can be skipped and the previous field can be revisited.
- Preserved `/editcaption`, preview delivery, explicit `/confirm`, cancellation before publication and persistent drafts. The creator's Telegram username is added as the contact line when available.
- Approved `MERCHANT` users can create listings from their own private chat. Merchant access does not grant admin-panel, role-management, source-chat or destination-management rights.
- Added Superadmin `/adddestination`, `/removedestination` and `/destinations` commands for additional Telegram groups/channels. The existing `/setsource` chat remains the primary indexing destination.
- Approved posts are copied to the saved additional destinations. Each destination has independent persisted state; a definite Telegram rejection may be retried, while uncertain sends are held for manual checking to prevent duplicates.
- Added offline regression coverage for merchant permissions, structured captions and price extraction, destination persistence, retry behavior and no-duplicate delivery.
