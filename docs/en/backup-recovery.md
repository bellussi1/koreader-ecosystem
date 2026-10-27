# Backup and recovery

[English](backup-recovery.md) | [Português (Brasil)](../backup-recovery.md)

## Before you start

- Close Calibre and the Calibre content server before creating or restoring a library backup.
- To include the Kindle, connect it by USB and leave it on the USB-connected screen. A Calibre-only backup does not require the device.
- Restic is included in the Electron app package. For the legacy CLI, install the project dependencies and Restic as described in the [development guide](../../README.md#development).
- Store the repository password in an external password manager as well as Windows Credential Manager. Losing the password means losing access to encrypted snapshots.

On first launch of version 0.2.6, legacy local records are migrated into `application-state.sqlite3` without deleting their source files; the database is included in snapshots.

## Create and verify a backup in the app

1. Open **Kindle e sincronização** (Kindle & Sync).
2. Choose whether to include Calibre and app state. Include the Kindle only when it is connected and detected.
3. Choose **Criar e verificar backup** (Create and verify backup).
4. Confirm that Calibre is closed. The app checks the Calibre database, creates the encrypted Restic snapshot, checks the repository, and can restore the snapshot temporarily to compare manifests.

The app excludes credentials, temporary cache, staging data, and plugin rollback files. It does not include every file on the computer.

### Reopen a recovered profile

1. Install/open the app on the Windows account where the profile will be used, and keep Calibre closed.
2. Under **Kindle e sincronização → Backup e restauração** (Kindle & Sync → Backup and recovery), select the snapshot and choose a new parent folder outside the active library and Restic repository. Confirm and wait for validation to finish.
3. Under **Recuperar perfil local** (Recover local profile), select the newly restored folder. Check the schema version and preview counts; continue only if they match the state you expect.
4. Confirm **Revisar e recuperar perfil** (Review and recover profile). The app checkpoints the previous profile under `%LOCALAPPDATA%\KOReaderEcosystem\profile-recovery-checkpoints\<checkpoint-id>` and replaces only `config.json` and `application-state.sqlite3` in the active profile. The restore folder, Calibre library, and Kindle are not modified.
5. Click **Reiniciar agora** (Restart now). If automatic restart fails, close and reopen KOReader Ecosystem. In **Configuração** (Settings), confirm the Calibre library is connected; in **Kindle e sincronização** (Kindle & Sync), confirm the expected local history is present. Keep the restore folder and checkpoint until these checks are complete.

Profile recovery reinstalls app configuration and its local database, but retains the Calibre and Restic paths saved in the snapshot. On the same computer, those paths must still be available. The restored Calibre copy remains separate and is not selected automatically as the active library. If a saved path is missing or the library was moved, stop before editing books or creating backups: the Electron version does not yet provide Calibre/Restic path selection in its UI. Do not restore over the old library. The Restic password and Hardcover token are not imported; they remain in Windows Credential Manager and must be available in that Windows account.

This sequence was validated with a synthetic profile in a temporary directory: after simulating app reopening, the restored database was readable, the configured path was preserved, and the source `config.json`/SQLite files and a sentinel file in the original library remained byte-identical. This validates the profile flow, not post-reinstall recovery or an off-device backup.

The Kindle does not provide a content checksum over MTP. The adapter compares SHA-256 from a second read with the staged copy, then re-enumerates the device. Windows WPD does not reliably expose books under documents, .sdr directories, or some files under other Kindle roots, so those items are reread through a temporary Windows Shell MTP copy. Files under the system and koreader roots are read through WPD. This detects common copy mismatches but is not an independent checksum supplied by the Kindle. Shell does not report a reliable copy-completion signal, so the adapter waits for stable size and exclusive access. Runtime depends on the library and may approach two full transfers; extra temporary space is bounded by the largest file.

## Restore a snapshot

In the app, select a snapshot, choose a parent folder, and confirm. The app creates a new destination and validates the restored files; it never overwrites the active Calibre library. Version 0.2.8 can then preview app state from a verified restore folder. Applying it is a separate, explicit action that checkpoints the current profile, leaves credentials in Windows Credential Manager, and requires an app restart.

For the legacy CLI, list snapshots and restore to a new absolute path outside every source and the Restic repository:

```powershell
uv run koreader-ecosystem status
uv run koreader-ecosystem restore --snapshot <snapshot-id> --target C:\Restore\reading-ecosystem
```

Review the restored files before using them. Never restore directly over an active Calibre library or Kindle data.

## After reinstalling Windows

1. Install Python, uv, Git, and Restic if you will use the CLI.
2. Install the app or clone the repository and run scripts/install.ps1.
3. Point the app or CLI at the intact Restic repository.
4. Retrieve the repository password from your external password manager and save it in Windows Credential Manager.
5. Restore to a new folder and check the contents before reopening Calibre.

## Remote copies

The current desktop workflow has not completed independent remote backup and restore validation. A copy on the same physical disk as the source is not disaster recovery. Do not treat remote backup as ready until it has been configured, checked, and restored successfully to a separate destination.
