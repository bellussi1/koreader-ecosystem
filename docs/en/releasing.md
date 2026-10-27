# Release process for Windows

[English](releasing.md) | [Português (Brasil)](../releasing.md)

Each release uses a SemVer tag (`vMAJOR.MINOR.PATCH`) and a matching GitHub Release. The release contains the installer, portable ZIP package, and SHA-256 hashes. Binary assets are not committed to Git.

The [release workflow](../../.github/workflows/release.yml) starts when a `v*` tag is pushed. It validates version metadata, runs checks, builds the Windows installer and ZIP package, generates `SHA256SUMS.txt`, and publishes the files. A failed check or build prevents publication.

## Prepare a release

1. Choose the next SemVer version and update `version` in `desktop/package.json`.
2. The UI receives the version from `desktop/package.json`; do not maintain a second manual version number.
3. Add release notes at `docs/releases/vX.Y.Z.md` and an English copy at `docs/en/releases/vX.Y.Z.md`. Include changes, requirements, limitations, and relevant instructions.
4. Update both language versions of the README and guides that identify the current release.
5. Review the diff for personal data, credentials, logs, book files, Calibre databases, KOReader sidecars, and backups.
6. Run the checks listed in the README on the exact commit to be tagged.

The workflow compares the tag with the package version, checks the UI version, and requires release notes. A mismatch stops the release.

## Push the commit and tag

After the release commit has been merged into `main`, create an annotated tag pointing to that commit:

```powershell
git switch main
git pull --ff-only origin main
git tag -a vX.Y.Z -m "KOReader Ecosystem vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

If `main` is already on GitHub, it does not need to be pushed again. The tag triggers the release workflow. Never reuse a published release tag; create a new version.

## Published files

Each release contains:

- `KOReaderEcosystem-Setup.exe` — Windows x64 installer.
- `KOReaderEcosystem-Windows-x64.zip` — portable package with the app executable, Python service, and required support files. Extract the whole folder; do not copy the executable by itself.
- `SHA256SUMS.txt` — hashes for the installer and ZIP package.

The installer is not digitally signed. Do not commit installers, ZIPs, `.nupkg` files, standalone executables, personal data, or backups to Git.

## Release history

- `v0.1.2` was a manually published Electron preview.
- `v0.2.0` was the first release published by the automated tag workflow.
- `v0.2.6` adds local KOReader reading history and hybrid MTP verification; the Windows workflow attaches its assets after completion.
- `v0.2.7` adds persistent Hardcover cache/outbox, guided local profile recovery, supported Calibre cover/custom-field editing, and batch tag changes; the tag workflow publishes the Windows assets.
- `v0.2.8` renames the Python package and desktop service, migrates the existing local profile and credentials, and updates the public documentation.
- `v0.2.9` adds English/PT-BR UI selection, follows the Windows language on first launch, and documents the workflows with fictional English screenshots.
- `v0.3.0` adds local Goodreads, StoryGraph, and Maratona-compatible CSV interchange with reviewable previews and protected export.
- `v0.3.5` adds reviewable English and Portuguese CSV header mapping before a local preview.
- `v0.3.4` adds per-action local review of ambiguous Hardcover outbox items without exposing journal text; `v0.3.3` increases sort and filter hit areas to 48 px and applies Inter to toolbar controls; `v0.3.2` adds bounded, manually confirmed outbox reconciliation that retries only idempotent operations.
- `v0.2.5` was the previous stable release with an installer, portable ZIP, and SHA-256 hashes.
- `v0.2.4` is the previous stable release with an installer, portable ZIP, and SHA-256 hashes.
- `v0.2.3` has a tag without a corresponding GitHub Release; its preparation was superseded by `v0.2.4`.
- The release workflow builds the Electron distribution. The legacy PySide6 distribution is built separately and is not included in Electron release assets.

The Python distribution, import package, Credential Manager service, and desktop sidecar use the `koreader-ecosystem` / `KOReaderEcosystem` name. The Windows app version is controlled by `desktop/package.json`. On first launch after upgrade, the app migrates the previous local profile and credentials without deleting their legacy copies.
