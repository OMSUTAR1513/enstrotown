# Abhinazh Bot V5.8 — Channel and Group Setup Audit

## 1. Root cause confirmed from the screenshot and source

The screenshot shows `ESTROTOWN TEST`, `3 subscribers`, and a `Broadcast` composer. Those are channel indicators, not a group discussion view.

The V5.7 source had two separate channel-related gaps:

1. All setup commands (`/setsource`, `/setadmin`, etc.) were registered as `CommandHandler`s. The python-telegram-bot v22.7 documentation explicitly states that `CommandHandler` does not handle channel posts. Consequently, those commands were never dispatched from a channel post, even if the bot received the channel update.
2. The source indexer only registered normal messages and edited messages and rejected chat types other than `group` and `supergroup`. It did not process `channel_post` or `edited_channel_post`.

A third setup/security distinction also matters: channel posts identify the channel as `sender_chat`; the human admin's user ID is not supplied. A command posted as the channel cannot securely prove that the Superadmin issued it.

Sources:
- Telegram Bot API Update object: `channel_post` and `edited_channel_post` are separate update fields: https://core.telegram.org/bots/api
- Telegram Bots FAQ: bots receive messages from channels where they are members; group privacy mode governs group-message visibility: https://core.telegram.org/bots/faq
- python-telegram-bot v22.7 CommandHandler: channel posts are not handled by CommandHandler: https://docs.python-telegram-bot.org/en/v22.7/telegram.ext.commandhandler.html
- python-telegram-bot v22.7 filters: CHANNEL_POST / CHANNEL_POSTS filters: https://docs.python-telegram-bot.org/en/v22.7/telegram.ext.filters.html

## 2. V5.8 changes

- Added `source_chat_utils.py` for source reference normalization, source-chat matching, and sender metadata.
- Added `/setsource @channelusername` in the Superadmin/app Admin private bot chat.
- Verifies the human user is a Telegram administrator of the target channel/group before saving it.
- Verifies the bot is a channel administrator before saving a channel source; verifies the bot has joined a group source.
- Added `channel_post_command_router` via `MessageHandler(filters.UpdateType.CHANNEL_POSTS, ...)` to give channel publishers an understandable setup response.
- Added `MessageHandler(filters.UpdateType.CHANNEL_POSTS, ingest_group_message)` so both new and edited channel posts are indexed.
- Updated the common source ingester to accept `group`, `supergroup`, and `channel`, but only when the chat ID equals the configured source.
- Preserves channel sender metadata from `sender_chat` when no human `from_user` is present.
- Prevents slash commands in either message text or media caption from being indexed as listings.
- `/setadmin` remains restricted to a separate private admin-only group and requires a command sent from the person's own Telegram account. It must not be run as a channel post.
- `/source`, `/audit`, and source setup commands are now listed in the bot command menu.

## 3. Correct setup procedure

### Source channel

1. Add the bot to the target channel as an administrator.
2. Ensure the configured Superadmin (ID `1485615852`) is also an administrator of that channel.
3. Open the bot's private chat using the Superadmin's personal account.
4. Send `/setsource @channelusername` (for the screenshot, this would be the channel's public username if it is indeed `estrownf`).
5. The bot calls `getChat`, then `getChatMember` for the human operator and itself. It saves the channel ID only after both checks pass.
6. Publish a new text or media post in the channel. Confirm it appears in the index.
7. Edit a post caption. Confirm the existing source record is updated.
8. Run `/source` and `/audit` in private chat.

If the channel is private and has no public username, use its numeric `-100...` chat ID after the bot has been added. An invite link such as `t.me/+...` is not a `getChat` identifier and is rejected rather than guessed.

### Source group

1. Add the bot to the source group.
2. For automatic indexing of ordinary group posts, disable Group Privacy Mode in BotFather; otherwise Telegram restricts messages to commands, mentions, replies, and messages sent via the bot.
3. Send `/setsource` as a user in the group. Do not send as the group/channel identity or anonymously, because the bot cannot verify the human's ID.

### Private admin request group

1. Use a separate private group, not the public source channel.
2. Add the bot.
3. Send `/setadmin` from the Superadmin's personal account inside that group.
4. The bot validates the user's app role and Telegram group-admin status.

## 4. Tests

- Original V5.7 suite: 70 tests.
- Added channel-source tests for public username/link parsing, numeric IDs, invalid invite links, allowed chat matching, sender metadata, channel-post registration, channel setup authorization, bot-admin requirement, channel command routing, and caption-command exclusion.
- Current offline suite: 87 tests, passing.
- Python compilation: passing.
- ResourceWarning-as-error test run: passing.
- Shell syntax checks (`run_bot.sh`, `install_termux.sh`): passing.
- ZIP integrity: passed (all archive entries validated).

## 5. V5.8.1 follow-up correction

The first channel router attempted `message.reply_text()` in the channel. That can fail when the bot is a channel administrator but does not have permission to publish messages. The follow-up changes the router to send setup guidance as a private DM to the configured Superadmin, and registers `CHANNEL_POSTS` (new and edited channel posts) rather than only `CHANNEL_POST` for this command router. User-facing Help and setup guides now instruct channel setup from the human Superadmin's private chat.

## 6. Live verification boundary

This build has not been connected to the user's live Telegram bot, channel or Termux instance. Therefore live `channel_post` delivery, channel permissions, and source indexing must be confirmed after installation. The code now contains the required handlers and authorization path, but no offline test can prove Telegram has actually added the bot as channel administrator, that the Superadmin has started the bot in private chat, or that the live polling process is using this version.

Do not delete or overwrite the existing SQLite database. Back up the database and local media folder before updating.
