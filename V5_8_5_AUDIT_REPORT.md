# Abhinazh Bot V5.8.5 — focused implementation audit

## Scope

Implemented the requested changes against the supplied V5.8.4 project archive. Existing search, alert, Buy/Notify, role-management, source-indexing, forum-topic, deletion and backup behavior was retained.

## Changes implemented

### 1. Guided account-field collection and caption generation

After `/add post` screenshots are uploaded and `/done` is sent, the bot now collects these fields in sequence:

1. Account/listing number (required)
2. Current kingdom or migration destination
3. Account type
4. Might (required)
5. Three base-stat values (required)
6. Price/currency
7. Artifact stars and blessed count
8. Champions and cups
9. Castle stars and mana troops
10. Familiars and account binding (for example, IGG)
11. Extra searchable features/notes

- Optional fields accept `/skip`; `/back` returns to the previous field.
- Required account number, might and three-value base-stat formats are validated before continuing.
- A caption is generated only from the submitted form values. The creator's Telegram username is included as the contact line when available; otherwise the numeric Telegram ID is shown.
- The existing `/editcaption`, photo/album preview, `/preview`, `/confirm` and `/cancel` stages remain.
- The price line uses an explicit `Price:` label so the existing currency-aware amount parser can extract an amount from values such as `USD 350`, `$350` or `350 + MM`.

### 2. Merchant posting permission

- The existing Superadmin role manager already supported assigning `MERCHANT`; V5.8.5 now grants approved Merchants access to the private listing-creation workflow.
- Merchants can upload screenshots, complete the guided form, edit their own caption, preview, confirm and discard their own draft.
- Drafts remain keyed by Telegram user ID.
- Merchant permission does not grant access to `/adminpanel`, role management, `/setsource`, additional destination management, deletion or reset controls.
- Destination configuration remains Superadmin-only; admins can view the configured destination list.

### 3. Additional group/channel publication destinations

Added persistent `publish_destinations` storage with additive startup schema creation, plus commands:

- `/adddestination @channelusername` or numeric chat ID — Superadmin only. Verifies the Superadmin's native Telegram administrator status and the bot's membership/posting capability.
- `/removedestination @username` or numeric chat ID — Superadmin only. Removes an additional target without changing the primary `/setsource` destination or deleting historical posts.
- `/destinations` — Superadmin and app Admin read-only view of the primary index target and saved additional publication targets.

The configured `/setsource` group/channel remains the primary publishing and local-index destination. New approved listings publish there first, are indexed there, and are then copied to the additional targets captured at approval time.

### 4. Retry and duplicate protection

- Each additional destination has a persisted delivery state: pending, sending, sent, failed or uncertain.
- A successful copy is never sent again by `/confirm`.
- A definite Telegram `BadRequest`/`Forbidden` rejection is marked failed and may be retried after permissions are fixed.
- A timeout, unexpected send exception, short copy result or interrupted in-progress send is marked uncertain. The bot does not automatically resend an uncertain copy because Telegram may already have accepted it. The admin must inspect that destination manually before deciding how to resolve it; no automatic force-resend command is provided.
- Primary indexing and extra-destination delivery are separate; a successful primary post is not republished while indexing or extra-target failures are retried.

## Verification performed

- `python -m py_compile bot.py database.py config.py search_engine.py tests/test_logic.py tests/test_navigation.py tests/test_channel_support.py` — passed.
- `python -W error::ResourceWarning -m unittest discover -s tests` — **119 tests passed**.
- `bash -n run_bot.sh install_termux.sh` — passed.

Regression tests cover merchant permission boundaries, database persistence for additional destinations, generated account captions and currency-aware amount extraction, retry of definite additional-destination failures, and suppression of duplicate sends after successful or uncertain delivery.

## Live-test boundary

The build environment does not have `python-telegram-bot` installed, and no live bot token, real merchant account, destination group/channel or Android Termux runtime is available. The offline suite uses Telegram test stubs/mocks. Therefore Python compilation, local SQLite behavior, state transitions and mocked API call behavior are verified; real Telegram authorization checks, `copyMessages`, channel posting permissions, and actual Termux deployment still require a smoke test on the running bot.

## Upgrade notes

1. Stop the existing bot process.
2. Back up the current bot folder, `bot_data.sqlite3` and `local_storage/`.
3. Replace the project files with V5.8.5; keep the existing SQLite database and local media folder.
4. Start the bot. The `publish_destinations` table is created automatically without deleting existing listings, users, roles or settings.
5. Superadmin assigns Merchant through the existing role-management screen. The approved Merchant opens the bot privately and runs `/add post`.
6. Superadmin uses `/adddestination @username` or a numeric chat ID to add extra groups/channels. Run `/destinations` to confirm configuration.
7. Test one staged listing end to end, including form skips/back, caption editing, primary indexing and delivery to each additional destination.
