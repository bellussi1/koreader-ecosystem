# Legacy portable Windows bundle

[English](legacy-portable-bundle.md) | [Português (Brasil)](../LEIA-ME-EXECUTAVEL.txt)

This guide applies only to the legacy PySide6 distribution produced by `scripts/build-windows-app.ps1`. The current Windows release is the Electron app; see the [current desktop app guide](desktop-app.md) for its workflows. The legacy bundle is kept for features that have not yet moved to Electron, including advanced recovery and KOReader–Calibre sidecar preview.

## Open the bundle

Keep the complete `KOReaderEcosystem-next` folder together and run `KOReaderEcosystem.exe` from inside it. Do not move the executable out of the package folder. The bundle includes its runtime files; Python and a terminal are not required to launch it. Local settings and state are stored under `%LOCALAPPDATA%\KOReaderEcosystem`.

## Configure the application

1. Open **Configuração** (Settings) and use the computer check to review the Calibre and Restic locations.
2. Store the Restic repository password in Windows Credential Manager and keep a recovery copy in a separate password manager.
3. To use Hardcover features, enter your own API key in the masked field and validate it. Do not put credentials in files or messages.
4. Close Calibre before any workflow that writes to its library.

## Read with KOReader and sync to Hardcover

1. Eject the Kindle in Windows, unplug USB, and connect the Kindle to Wi-Fi.
2. Open the book in KOReader and use **Marcador → Hardcover → Link book** to select the correct Hardcover book. Confirm the edition whose reading progress should be updated.
3. Enable **Track progress** in the KOReader Hardcover menu. KOReader communicates with Hardcover directly over Wi-Fi; the Windows app can stay closed.
4. Mark the book as finished in KOReader when you complete it. Check the resulting status and dates on Hardcover.
5. Ratings and journal entries are explicit user actions in the Windows app; they are not inferred from reading progress.

Pagination can differ between editions. EPUB progress may be estimated, so choose the intended Hardcover edition and review the result after syncing.

## USB, backups, and sidecars

- For USB workflows, leave the Kindle on its USB-connected screen and close KOReader on the device.
- Before creating a Calibre backup, applying sidecar changes, or updating a device plugin, close Calibre and review the operation's backup and confirmation prompts.
- Create and verify a sidecar preview before applying any proposed Calibre metadata changes.
- Restore Restic snapshots to a new folder and inspect the recovered contents before using them. Never restore over an active Calibre library or Kindle data.

The application runs locally. USB operations require this Windows computer and the Kindle connected over USB. A local Restic snapshot is not protection against losing the same physical disk; configure and independently validate an off-device copy before relying on disaster recovery.
