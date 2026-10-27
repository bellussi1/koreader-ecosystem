# Reading history reconciliation rules

[English](reading-history-reconciliation.md) | [Português (Brasil)](../reading-history-reconciliation.md)

This document defines how KOReader, local history, Calibre, and Hardcover share reading data without deleting history or conflating editions.

## Source and destination for each value

| Information | Rule |
| --- | --- |
| Page events and sidecar changes | Stored as immutable local events. Each import appends events; it does not rewrite earlier events. |
| Current progress | The current KOReader snapshot is the source of the current value. We never use the highest historical percentage. A decrease from the last applied value is held for review because it may mean a reading reset, reread, or stale snapshot. |
| Status | Use only the explicit KOReader status (`new`, `reading`, `complete`, or `abandoned`). 100% progress alone does not complete a book. `complete` with progress below 100% is a conflict and blocks automatic projection. |
| Dates | Preserve observed timestamps from `page_stat_data` and `last_open` locally. They do not prove the actual start or finish date. Do not invent dates for Calibre or Hardcover. |
| Sessions | Grouping events across a 30-minute inactivity gap is an estimate, not an exact session. |
| KOReader rating and note | Keep them in local history. After review, they may be projected to KOReader-specific Calibre columns. They do not change Hardcover ratings, comments, or journal entries. |
| Annotations | Keep versions in local history. In Calibre, append by stable identifier; never remove existing annotations just because they are absent on the device. Do not send annotation text to Hardcover. |
| Hardcover rating, journal, and status | User actions in Hardcover or through the KOReader plugin. Desktop imports do not replay these actions or make remote note, rating, or annotation calls. |

## Book and edition identity

- A Calibre projection requires a confirmed link to the exact Calibre record UUID; a similar title is not enough.
- Editions in different languages or formats remain separate records. A PT-BR EPUB is never rewritten to represent the physical ENG edition.
- Preserve the user's explicit Hardcover edition link. A local projection neither changes that edition nor merges it with the EPUB edition.
- A difference between current values and previously synchronized values requires review before writing.

## Safe application

1. Create a fresh preview from the connected device and review its identity links, conflicts, and fields.
2. Confirm Calibre is closed and `metadata.db` passes integrity check.
3. Create a Restic snapshot of Calibre and local state; check the repository before writing.
4. Apply only the reviewed preview, reread fields by UUID, and check database integrity again.

An old preview is not evidence of the Kindle's current state. Local history import does not update Hardcover; the installed KOReader plugin remains responsible for direct service sync when online.
