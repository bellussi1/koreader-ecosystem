# KOReader Ecosystem roadmap

[English](roadmap.md) | [Português (Brasil)](../roadmap.md)

Updated 2026-10-26. This is the active project checklist. Older plans in `docs/superpowers/plans/` document completed work or historical gates; this roadmap is the source of truth for work still outstanding.

## GitHub issues

Technical work is tracked in GitHub Issues, with priority in each title. Items tied to a specific personal library or computer remain outside the public tracker.

### P0 — Data integrity and publication

- [#1 — Prepare the first public release](https://github.com/bellussi1/koreader-ecosystem/issues/1)
- [#2 — Harden MTP transfers and sidecar recovery](https://github.com/bellussi1/koreader-ecosystem/issues/2)
- [#3 — Validate the KOReader → Hardcover lifecycle](https://github.com/bellussi1/koreader-ecosystem/issues/3)
- [#4 — Finalize reviewable reading-history projection to Calibre](https://github.com/bellussi1/koreader-ecosystem/issues/4)
- [#5 — Validate isolated Restic profile recovery](https://github.com/bellussi1/koreader-ecosystem/issues/5)
- [#6 — Complete public-release privacy gates](https://github.com/bellussi1/koreader-ecosystem/issues/6)

### P1 — Integrations and automation

- [#7 — Define a capability-based provider interface](https://github.com/bellussi1/koreader-ecosystem/issues/7)
- [#8 — Add quota-aware Hardcover pagination](https://github.com/bellussi1/koreader-ecosystem/issues/8)
- [#9 — Validate request budgets across app and plugin](https://github.com/bellussi1/koreader-ecosystem/issues/9)
- [#10 — Validate packaged Windows automation](https://github.com/bellussi1/koreader-ecosystem/issues/10)
- [#11 — Publish a Windows compatibility matrix and verified updater](https://github.com/bellussi1/koreader-ecosystem/issues/11)
- [#12 — Export data available through the Hardcover API](https://github.com/bellussi1/koreader-ecosystem/issues/12)
- [#22 — Expand the Calibre editor and bulk operations](https://github.com/bellussi1/koreader-ecosystem/issues/22)
- [#23 — Add safe Windows build retention](https://github.com/bellussi1/koreader-ecosystem/issues/23)
- [#24 — Select library and backup locations on first run](https://github.com/bellussi1/koreader-ecosystem/issues/24)

### P2 — Future features

- [#13 — Add non-destructive EPUB audits](https://github.com/bellussi1/koreader-ecosystem/issues/13)
- [#14 — Add reviewable metadata enrichment candidates](https://github.com/bellussi1/koreader-ecosystem/issues/14)
- [#15 — Explore AI-assisted metadata organization](https://github.com/bellussi1/koreader-ecosystem/issues/15)
- [#16 — Add selective Obsidian and Anki exports](https://github.com/bellussi1/koreader-ecosystem/issues/16)
- [#17 — Add statistics with data provenance](https://github.com/bellussi1/koreader-ecosystem/issues/17)
- [#18 — Evaluate secure OPDS and Wi-Fi transfer](https://github.com/bellussi1/koreader-ecosystem/issues/18)
- [#19 — Evaluate a spoiler-aware reading assistant](https://github.com/bellussi1/koreader-ecosystem/issues/19)
- [#20 — Build a Tauri proof of concept](https://github.com/bellussi1/koreader-ecosystem/issues/20)
- [#21 — Migrate unique PySide6 workflows before removal](https://github.com/bellussi1/koreader-ecosystem/issues/21)

## Verified status

- Version **0.3.7** prepares this repository's first package. See [Releases](https://github.com/bellussi1/koreader-ecosystem/releases) for available downloads and [Actions](https://github.com/bellussi1/koreader-ecosystem/actions) for CI results.
- Publication requires a privacy audit, rotation of exposed credentials, and artifact verification.
- Remote backup and independent restoration remain pending and require explicit configuration.

## P0 — finish the safe daily workflow

- [x] **Interface:** Inter UI type, Lora book titles, and 48 px sort/filter hit areas that respond across the full control without overlap. Renderer tests pass. The Windows 0.2.8 installation opened with the expected icon.
- [x] **Calibre preview:** the workflow generates candidates in read-only mode for review before any write.
- [ ] **General MTP transfer flow:** validate batches and reconnection beyond the individual transfers already verified.
- [ ] **KOReader — sidecar restoration:** validate file recovery and verification after interruptions on a physical device.
- [x] **Preview performance:** KOReader–Calibre stages only reading statistics and metadata, with source verification.
- [ ] **KOReader → Hardcover:** validate edition linking, start, progress, dates, completion, and offline recovery on the physical Kindle; record the result without unnecessary personal data.
- [ ] **Reading history sync:** source, date, progress, status, note, rating, annotation, and edition identity rules are documented; `complete` with progress below 100% now blocks projection. A fresh preview after the physical reading, candidate review, backup, and exact Calibre link application/verification are still pending.
- [ ] **Profile recovery:** tests cover isolated installation, checkpoint preservation, reopening, and schema migration. UI validation with a real Restic snapshot remains pending.
- [ ] **First release of this repository:** validate CI, Setup, ZIP, and hashes; the installer is not digitally signed.
- [x] **Installed shortcut:** the `--squirrel-install` and `--squirrel-updated` events recreate the shortcut with the application icon.
- [ ] **Local build cleanup:** define retention and protect the current package, rollback copy, and release assets; see issue #23.

## P1 — integrations and optional automation

- [x] CSV foundation: compatible-format parser, reviewable local storage, paged preview, and Goodreads export with spreadsheet formula protection and opt-in reviews.
- [x] Suggest local CSV matches by ISBN/provider ID or title + author; conflicts remain explicit, and nothing is linked or changed. Matching uses only catalogs already loaded in the view and makes no extra remote requests.
- [x] **CSV column mapping:** suggestions for English and Brazilian Portuguese headers, required manual title/author mapping, and reviewable optional fields. No rows are previewed or saved until the mapping is confirmed; a digest prevents importing a file changed after inspection.
- [ ] Define a provider interface and automate only official/documented APIs; do not assume statuses, editions, notes, or ratings are equivalent.
- [x] **Hardcover outbox reconciliation:** Settings shows safe actions and actions that need manual review. It requires confirmation, processes up to four actions per click, and uses an atomic claim. Never-sent actions can be submitted once; uncertain submissions retry only idempotent updates to an existing read or rating. Ambiguous new reads and journal notes are never resent automatically.
- [x] **Per-action outbox review:** Settings lists each ambiguous action with a book when available in cache, type, state, and time. Journal text never leaves the backend. After checking the account, users can record that the action appears or close the item locally only; this makes no API call and does not change Hardcover. SQLite v5 persists the decision and prevents a concurrent retry.
- [x] **Hardcover offline cache:** fetched pages and current reading are persisted and may be shown for up to 30 days after transient failures; the UI marks cached data.
- [ ] **Background pagination:** no separate incremental prefetch is implemented yet. Any prefetch must avoid extra calls and stay within the service budget.
- [ ] **Plugin request limits:** app code caps requests at 1,000 per rolling 24 hours and the plugin patch at 2,000, for a projected 3,000 total. Installation/counter behavior still needs Kindle validation, and usage by other Hardcover clients shares the account quota.
- [x] Add an EN/PT-BR selector, follow the Windows language on first launch, and use English demo screenshots with fictional data in the README.
- [ ] **Local automation — implementation under review:** preferences are off by default; the code adds a Windows daily task, USB backup after two stable device checks, a cross-process lock for backups, Calibre/MTP writes, and profile recovery, plus recovery for interrupted runs. Local tests cover concurrency, disabling future runs without interrupting an active run, and recovery. A packaged Squirrel/Task Scheduler run on Windows is still pending; no task was registered and both options remain disabled.
- [ ] Create a compatibility matrix and verified updater; automatic updates wait for digital signing and reliable rollback.
- [ ] Export only the data actually available from the Hardcover API; do not call the cache a full account backup.

## P2 — features after data integrity

- [ ] Audit EPUB files and suggest repairs without replacing originals.
- [ ] Enrich metadata by ISBN, title, and author with explicit review of candidates.
- [ ] Add AI-assisted metadata organization that remains reviewable and does not invent information.
- [ ] Export notes to Obsidian and create Anki cards through deliberate selection.
- [ ] Add statistics that distinguish measured, calculated, and estimated values.
- [ ] Evaluate OPDS and Wi-Fi transfer after security and compatibility review.
- [ ] Evaluate a contextual assistant with spoiler limits and transparent costs.
- [ ] Build a Tauri proof of concept and compare size, installation, and compatibility before proposing a migration.
- [ ] Remove the legacy PySide6 UI only after its unique workflows have been inventoried and migrated.

## Gates that need user input or action

- Remote backup remains deferred until the user chooses a destination and an independent restore is validated outside this computer.
- Calibre and Kindle writes require known device state, a valid backup, and the specific approvals already recorded; ambiguous changes remain pending.
- Make the repository public only after a history scan and privacy audit. Credentials shared outside the repository must be rotated before public disclosure.
- Real reading, rating, and completion validation needs action on the Kindle and access to the Hardcover account; the desktop can remain closed while the plugin tracks reading.

## Principles

- Calibre, KOReader, and Hardcover keep their own data; writes between services must be reviewable and verified.
- A Hardcover edition choice does not replace or modify the local digital file.
- Existing books and sidecars are not overwritten by default.
- Clearly distinguish measured, calculated, and estimated results.
- Never include a personal library, credentials, private logs, or backups in source, screenshots, or release assets.
