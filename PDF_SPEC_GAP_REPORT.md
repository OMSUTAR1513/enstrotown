# Marketplace Specification — V5.8.1 Gap Report

**Source baseline:** *Lords Mobile Marketplace Bot — Developer Requirements & Implementation Handoff*, Version 1.0, 23 November 2026 (12 pages).

**Important product-owner clarification:** In the chat, the product owner clarified that existing source-group posts should continue to be collected automatically. “Manual” refers to being able to edit the generated caption; the bot itself should publish the approved post automatically. This clarification supersedes the PDF's bot-only manual-entry / no automatic source-collection wording in Section 2. The rest of the PDF remains the specification unless separately clarified.

## 1. Confirmed intended workflow after clarification

1. The bot automatically detects and indexes new source-group posts.
2. The bot generates a structured listing caption from the available listing details.
3. An authorized admin/merchant can manually edit the generated caption.
4. The bot shows the final caption and media preview.
5. The creator explicitly approves using Publish.
6. The bot automatically publishes the approved caption/media to the selected configured destinations.
7. Every destination has its own delivery state; a retry does not repost to a destination that already succeeded.

“Automatic publishing” means the bot sends the post after the creator's approval; the user does not manually copy/paste into groups. The PDF's explicit approval step is retained.

## 2. What V5.8.1 actually is

V5.8.1 is a maintenance release for the existing Termux-based account finder. It preserves automatic source-group indexing, local SQLite storage, search, alerts, Buy/Notify request tickets and admin reply routing. It adds Superadmin/role controls and audited in-place button navigation. It is not the complete new marketplace post-builder/publisher.

## 3. Requirement traceability

| PDF section | Requirement | V5.6 status |
|---|---|---|
| §1 | One canonical marketplace for listing/posting/search/alerts | **Partial:** one local listing index feeds search/alerts; full publisher lifecycle is absent |
| §2 | Roles, listing creation and product decisions | **Partial / clarified:** automatic source-group indexing is preserved per owner's later clarification; caption editing and automatic publishing are not yet implemented |
| §3 | Admin, merchant, buyer and unapproved-user roles; server checks | **Partial:** Superadmin/Admin/Merchant/Buyer/User role records exist; Superadmin and Admin checks protect current controls; full merchant approval and publishing scopes are absent |
| §4 | Caption builder, edit details/caption, exact preview, explicit approval snapshot | **Not implemented:** V5.6 does not yet have the caption editor, preview or publish approval workflow |
| §5 | Multi-destination publisher, per-destination tracking and idempotent retries | **Not implemented:** Buy/Notify tickets are not listing publication |
| §6 | Central records, source provenance, lifecycle and audit | **Partial:** indexed source messages/listings, alerts, request records and role audit exist; full lifecycle/media/delivery schema is absent |
| §7–8 | Full search token catalogue, combined filter semantics, explanations, stable pagination | **Partial:** text search, amount/kingdom/recency/features and sorting are present; not all PDF fields/tokens or formal matching rules are implemented |
| §9 | User-selected alert duration, individual DMs, expiry and durable retries | **Partial:** individual alerts and delivery de-duplication are implemented; alert duration is not user-selectable and there is no durable task queue |
| §10 | Merchant/destination moderation, sold records and reports | **Partial:** admin requests, sold/relist/index controls and role audit exist; destination management and full CSV/PDF reporting are absent |
| §11 | Railway, PostgreSQL, durable media and background jobs | **Not implemented:** current runtime is Python on Termux with SQLite/local media |
| §12 | Modular architecture | **Partial:** bot, database, search, amount and time modules are separated; it is not yet the specified production modular marketplace architecture |
| §13 | Secrets, validation, authorization, rate limits, audit and backups | **Partial:** token environment variable, numeric Superadmin ID, server-side role checks, audit log, backup verification and error logging; rate limiting and broader audit coverage remain |
| §14 | Acceptance tests | **Partial:** local offline tests cover data, role, navigation, callback de-duplication and backup protections; real Telegram/publishing acceptance tests are not run |
| §15 | Product-owner questions | **Open:** the implementation does not silently resolve unspecified business rules |
| §16–17 | Repository, migrations, lockfile, staging, ops guide and priorities | **Partial:** source, dependencies, setup notes and offline tests supplied; no PostgreSQL migration framework, object store, Railway deployment or publisher staging |

## 4. Important remaining work before calling the marketplace complete

### Posting and publishing

- Caption template and account fields.
- Generated caption with manual edit controls.
- Preview of exact caption and media.
- Approval snapshot (caption/version, media, destinations, actor and time).
- Multi-destination send with separate delivery records.
- Safe retry of failed destinations only.

### Production foundation

- PostgreSQL schema and versioned migrations.
- Durable object storage for media.
- Durable publication/notification tasks and restart recovery.
- Railway deployment and health checks.
- Rate limits and broader admin/activity/error reporting.

### Unresolved product decisions from PDF §15

The PDF still leaves the following questions open: exact account form fields/units; USD-only or multi-currency; meaning of “highest”; whether and how to implement “direct”; semantics of 11K/2x11K/Ursa/P2P/mana/artifact/blessed/cups; HP/levels/artifacts/blasts units; whether account type is exclusive; publication/search eligibility after partial delivery; sold listing behavior; cancelled-draft retention; merchant destination permissions; exact caption template; image count/type/order; alert duration choices and maximum; whether new alerts should match existing listings; search access policy; edits after publication; and buyer/seller contact or transaction process.

## 5. Navigation and reliability changes in V5.8

- Home, Help, Search menu, Filter menu, My Alerts, Superadmin, role, admin, index-confirmation and sold-ticket button paths edit the clicked bot message. Help now chooses `editMessageText` for text messages and `editMessageCaption` for media messages; a compact Help caption is used when needed to satisfy Telegram’s 1,024-character caption limit.
- Buy/Notify edits the clicked result card's text/caption. The request ticket is a separate new message in the private admin group, which is the intended business event.
- Normal search commands and proactive alert notifications still create new messages because they deliver new results/events.
- The bot does not call Telegram's delete-message API to implement menu navigation.
- Buy/Notify tokens are consumed atomically to prevent duplicate actions from rapid repeat taps.
- Only the fixed Superadmin can clear the full local index or reset operational data; a verified backup is required first.
- Only the Superadmin or an application Admin who is also a native Telegram group administrator can change source/admin group routing. Only the Superadmin can set the saved owner identity.

## 6. What has and has not been verified

**Verified locally for V5.8:** Python compilation, AST parsing, shell-script syntax, and 70 offline regression tests, including Help/Resume callback compatibility and media-caption editing. See `V5_7_AUDIT_REPORT.md` for the exact verification boundary.

**Not verified live:** Telegram token validity, actual account permissions, Termux package setup, real callback responses, bot membership in the private groups, source-group ingestion under the live privacy settings, and live Buy/Notify delivery. Those require deployment and real Telegram testing.


## V5.8 source channel correction

V5.8.1 adds Telegram channel source support and command-notice handling. This is separate from the original source-group support. The setup command for a channel is run in the Superadmin/Admin private chat as `/setsource @channelusername`; both the human operator and bot are verified as channel administrators. A new `MessageHandler` receives `channel_post` command posts because python-telegram-bot `CommandHandler` explicitly excludes channel posts. The indexer also receives new and edited channel posts using `filters.UpdateType.CHANNEL_POSTS`.


## V5.8.1 channel-post command routing correction

The command router now subscribes to both new and edited channel-post updates and sends setup guidance to the fixed Superadmin by private message, rather than attempting to publish a reply into the source channel. The Superadmin must have started the bot first. Offline tests cover the route and its private-DM failure path; live Telegram delivery still needs a staging check. See `V5_8_1_CHANNEL_COMMAND_FIX.md`.
