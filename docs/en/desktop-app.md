# Windows app guide

[English](desktop-app.md) | [Português (Brasil)](../desktop-app.md)

This guide describes the Electron app in version `0.3.7`. The interface supports English and Brazilian Portuguese. Choose **Settings → Interface language**; on first launch, the app follows the Windows language.

## Setup

1. Install Calibre and confirm where its library is stored.
2. Open **KOReader Ecosystem** and choose **Settings**.
3. To view Hardcover shelves, enter your own API key in the masked field and choose **Connect**. The app validates the key and stores it in Windows Credential Manager.
4. Configure the Restic password in **Settings**. Keep a separate copy in a password manager.
5. Close Calibre before adding or editing books. The app can read the library while Calibre is open, but writes require it to be closed.

## Calibre library

The **Library → Calibre** shelf shows books from the local Calibre library, including covers, formats, series, and ISBNs. Search and sort the catalog.

- Choose **Add book** to select one file. Review possible duplicates and explicitly choose whether to attach the format to an existing record or create a separate record.
- Choose **Edit** to change supported fields: title, authors, series, publisher, date, language, tags, rating, identifiers, cover, and eligible single-value custom fields. The app creates a backup, writes through the Calibre CLI, and rereads the catalog. Multi-value/calculated fields and comments are not part of the editor.
- Select several books to review and apply batch tag changes after a verified backup. The app rereads the catalog afterward.

Calibre metadata edits do not change Hardcover editions or shelf status.

## Hardcover shelves

Use **Want to read**, **Currently reading**, and **Read** to view the matching Hardcover shelves. Digital editions are shown by default; disable **eBook / Kindle only** to include other formats.

**Reading now** highlights the reading record with the most recent Hardcover update. Progress comes from KOReader and its Hardcover plugin, not from opening a book in the Windows app. Calibre metadata remains separate from Hardcover records.

If a Hardcover action remains queued after a failure, open **Settings → Hardcover connection**. The queue separates safe actions from those that need manual review. **Process up to four safe actions** asks for confirmation before sending; remaining actions stay queued. The review list shows the action, book (when available in local cache), state, and time, but never journal text. Check your Hardcover account, then choose **Already appears in Hardcover** or **Close without confirming**. This closes the item locally and does not change the account. An action that was never sent is submitted once; a new read or journal note with an uncertain submission is not resent to avoid duplicates. Uncertain updates to an existing read or rating may be retried safely.

The app does not automatically link a local Calibre file to a Hardcover edition. In KOReader, choose the Hardcover record and edition that should receive reading updates. Different pagination between Portuguese and original English editions can change progress percentages.

## Reading history CSV

On **Library**, choose **CSV history**, select Goodreads, StoryGraph, or Maratona as the source, then choose a CSV file. Review the suggested column mapping (title and author are required), adjust fields that do not match, and choose **Preview using these columns**. Reviews are unmapped by default; map that column explicitly if you want to import them. Only values from mapped columns are kept; ignored columns and private notes are discarded. Review the row counts, warnings, and books before confirming. Confirmed imports are stored locally and do not update Hardcover or Calibre.

Select a saved batch in **Recent imports** to export a Goodreads-compatible CSV using the Windows save dialog. Mapped reviews are excluded by default and included only after you check the opt-in box. Private notes are never imported or exported. To avoid mixing data, the same source and exact file cannot be reimported with a different mapping; the previous batch is kept. CSV exchange does not yet match imported rows to Hardcover or Calibre editions or synchronize changes automatically.

A successful sync status in the app means it queried Hardcover. It does not prove the Kindle uploaded progress; confirm that on Hardcover.

### Hardcover request limits

The Windows app blocks new calls after **1,000 requests in any rolling 24-hour window** or **25 in one minute**. Its persistent local counter reserves a request before connecting, so failed attempts are counted too. The updated Kindle plugin has a separate **2,000 requests per 24 hours** and **25 per minute** cap, shown in the KOReader Hardcover menu. Together, the two project components are capped at 3,000 requests per day and 50 per minute.

The official API documentation reviewed lists a limit of 60 requests per minute but does not specify a daily allowance on that page. The local caps described above do not count the Hardcover website or other clients using the same account, so they cannot guarantee total usage or free-plan eligibility. [Hardcover API documentation](https://docs.hardcover.app/api/getting-started/).

## Send a book to Kindle

1. Connect the Kindle over USB and leave it on the **USB connected** screen. The app checks for the device automatically; **Check now** starts a manual check.
2. Close or exit KOReader while transferring. Keep the cable connected.
3. In **Library**, choose **Send**, select a format already in Calibre, and review the destination filename.
4. Confirm **Create and verify backup, then send**. The app creates and checks a Restic snapshot, copies the selected file to documents/Books, and attempts to reread it for SHA-256 comparison.
5. Eject the Kindle through Windows before unplugging it. Open the book in KOReader after safe ejection.

Existing Kindle files are never overwritten. Identical content is reported as already present; a filename collision requires a different name. If verification is uncertain or fails, inspect the Kindle before retrying.

**Validation limit:** individual transfers have been verified on a physical Kindle. General batch transfers, reconnection, and KOReader sidecar restoration still need validation.

## Reading progress

For KOReader to sync progress, dates, and status, the Kindle must be out of USB mode, KOReader must be open, and Wi-Fi must be available. The plugin talks directly to Hardcover; the Windows app can be closed. Refresh the shelves in the app after reading.

Journal entries and ratings remain manual actions. Reflowable EPUB page counts or percentages may be estimates when Hardcover pagination does not match the reading file.

## Import KOReader history locally

With the Kindle connected in USB mode, **KOReader & sync** can read a temporary copy of KOReader's `statistics.sqlite3` and `.sdr` reading metadata. Preview is read-only on the device. It shows available page events, position and progress, status, rating, note, and annotation count. Only **Import local history** confirms and saves the data to the app's private SQLite database.

KOReader does not store session IDs. The app groups events separated by up to 30 minutes and labels those sessions as **estimated**. The import stays on this computer; it does not send or update Hardcover data. The KOReader–Hardcover plugin's online sync remains separate and runs from the Kindle over Wi-Fi.

Sidecars without an exact link remain separate. If KOReader has no title in its metadata, the preview uses the `.sdr` folder name and labels its source; the name is only a visual review aid and never creates an automatic link.

To project the history into Calibre, manually link each KOReader book to the correct local Calibre record/edition and review the preview. There is no automatic title matching. Applying a projection requires Calibre to be closed, a verified Restic backup, and a read-back check. Projected fields are KOReader progress, status, position, rating, note, and annotations; they do not change Hardcover shelves or editions.

The allowlisted local history database is included in Restic snapshots when present. The current destination remains on this computer and disk. This workflow still needs a real Kindle reading-cycle validation before it is ready for daily use.

### Physical Kindle acceptance checklist

This validation is still pending. Use a test EPUB whose local edition has been identified correctly:

- [ ] Read a few pages in KOReader and add a test note or annotation; observe the progress and timestamps shown by the reader.
- [ ] Connect the Kindle in USB mode and create a local preview; review the book, progress, status, note, annotations, and warnings before confirming.
- [ ] Confirm the local import and verify that page events and the annotation revision remain visible after reopening the app.
- [ ] Manually link the book to the correct Calibre edition. With Calibre closed, review proposed fields and apply the projection only after a verified backup.
- [ ] Restore a snapshot to a new folder and confirm that the recovered local database contains the batch, events, link, and annotations without replacing the active profile.
- [ ] If validating online integration as well, safely eject the Kindle, connect Wi-Fi, and confirm on Hardcover that the KOReader plugin updated the expected edition. This upload is separate from local import.

## Backup and availability

- Calibre writes and Kindle transfers require a backup before changing data.
- On **KOReader & sync**, create Restic snapshots of the Calibre library and allowlisted app state, including `application-state.sqlite3` with imported KOReader history and the local Hardcover request timestamp ledger; include the Kindle when detected and selected.
- Backup creation requires Calibre to be closed and the Calibre database to pass integrity validation. Verification restores the snapshot temporarily and compares manifests.
- Restore creates a new folder and validates it. It never replaces the active library. Version 0.2.8 can preview and restore the app profile from a verified restore folder after explicit confirmation; it checkpoints the active profile, leaves credentials in Windows Credential Manager, and requires an app restart.
- The Restic password is stored in Windows Credential Manager. A replacement password is checked against the repository before saving.
- Remote backup requires a configured destination and an independently validated restore. Check whether your Restic repository is on the same physical disk as its sources.
- The Windows app need not stay open while you read. It must run locally for Calibre access, USB/MTP transfers, and local backups.

### Optional automation

In **Settings → Local automation**, both options start off:

- **Daily backup** registers a Windows Task Scheduler task for Calibre and local app data. It runs only in the signed-in Windows session while the computer is awake, and Calibre must be closed. It uses the installer's stable launcher and opens no window or terminal.
- **When connected over USB** works only while KOReader Ecosystem is open. The app requires two consecutive device checks. If the device is already connected when the app opens and the option is enabled, backup starts after the second check. If you enable the option while the device is already connected, it waits for the next disconnect and reconnect. This backup includes the device and local data; a failed attempt is retried only on a new connection.
- Backups, protected Calibre/Kindle writes, and profile recovery share a cross-process lock. A conflicting operation shows a retry message; an active copy is never force-killed.
- Automatic runs validate the snapshot but do not perform a full temporary restore each time. Use **Create and verify backup** to validate the restore as well.
- Turning an option off removes future runs. A backup that has already started is allowed to finish; disabling automation does not terminate a process mid-copy.

The packaged Windows app and a real Task Scheduler run still need validation before this flow is considered ready for daily use. Both options remain off until the user enables them.
