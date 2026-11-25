# Abhinazh Bot V5.7 — Help/Resume Button Audit

**Release scope:** Targeted correction for the reported Help and Resume callback errors, based on the current project source and the user's instruction that navigation must update the existing bot message without creating/deleting replacement messages.

## Findings and changes

### 1. Help button callback compatibility

- The current home keyboard emits `home:help`; this callback is registered and routed.
- Older inline messages may still carry simpler callback data. The handler now also accepts `help` and `home_help`.
- `/help` and the Help button now use one shared `help_screen_text()` builder. Optional group/owner details are bounded so the result stays within Telegram's text limit.

### 2. Correct edit method for media messages

- Text messages are updated with `editMessageText`.
- Photo/video/document/audio messages are updated with `editMessageCaption`.
- Media Help uses a concise Help summary at or below 1,024 characters; text Help remains below 4,096 characters.
- If Telegram rejects an optional inline keyboard, the editor retries the same message without the keyboard. It does not send a new menu or delete the old one.

Telegram documents separate editing methods for message text and media captions. Text is limited to 1–4,096 characters and captions to 0–1,024 characters after entity parsing. Sources: official [Telegram Bot API](https://core.telegram.org/bots/api) and [python-telegram-bot v22.7 Bot API wrapper documentation](https://docs.python-telegram-bot.org/en/v22.7/telegram.bot.html).

### 3. Resume callback interpretation

- No Resume button, `resume` command or pause/resume indexing feature existed in the inspected V5.2, V5.3, V5.4 and V5.6 project sources.
- To avoid old inline buttons hanging, legacy callback values `resume`, `home_resume`, `home` and `home:resume` are accepted as return-to-home aliases.
- These aliases do **not** restart the source-group indexer or change group configuration. A real pause/resume indexing feature is not part of this release.

### 4. Message and data safety

- Home/Help/Resume navigation edits the currently clicked bot message.
- No `deleteMessage` or `Message.delete()` call is used by navigation.
- No new replacement menu is sent by the callback path.
- Normal `/find` results, real alert deliveries and Buy/Notify tickets in the separate private admin group remain new messages because they are distinct result/notification/request events.

## Automated verification

| Check | Result |
|---|---|
| `python -m py_compile *.py tests/*.py` | PASS |
| Offline unit/regression suite | PASS — 70 tests |
| `ResourceWarning` treated as error | PASS |
| Text Help <= 4,096 characters | PASS |
| Media Help caption <= 1,024 characters | PASS |
| Current and legacy Help callbacks | PASS |
| Current and legacy Resume aliases | PASS |
| Media screen uses caption edit method | PASS |
| Navigation callback does not send replacement messages | PASS |
| Navigation uses no Telegram message-delete method | PASS |
| `bash -n run_bot.sh install_termux.sh` | PASS |

## Verification limitations

- The test suite uses local Telegram object stubs/mocks. It verifies which PTB edit methods the application calls, but it does not contact Telegram.
- `python-telegram-bot==22.7` could not be installed in this environment because package-network access was unavailable. Its documented edit methods/signatures were checked against the official v22.7 documentation.
- The live Termux traceback and live bot token were not available. Therefore the precise BadRequest from the user's deployment cannot be certified as resolved until the V5.7 package is installed and the affected buttons are tested in Telegram.
- If the user's intended “Resume” action is to resume paused indexing rather than return to Home, that feature is absent and needs an explicit product definition; V5.7 does not pretend otherwise.

## Release decision

**Local verification: PASS. Live Telegram verification: NOT PERFORMED.** Do not describe this as production-certified until the Home, Help, legacy Resume, and Back buttons are tapped on the live bot and the Termux log remains clear of Telegram errors.
