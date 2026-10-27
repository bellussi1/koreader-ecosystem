# Project instructions

- Write user-facing documentation in English and Brazilian Portuguese.
- Git may contain source, infrastructure, and generic defaults only. Never add books, Calibre databases, `.sdr` folders, private exports, logs, personal manifests, tokens, or backups.
- Before a Calibre backup or write, confirm Calibre is closed and `metadata.db` passes `integrity_check`.
- Treat a Kindle as WPD/MTP storage; do not assume a drive letter, SSH, or root access.
- Never delete, rename, or restore over active Kindle or Calibre data without a verified backup and explicit user confirmation.
- Keep ambiguous title matching disabled. Resolve identities by ISBN, Hardcover Book ID, or Hardcover Edition ID.
- Follow test-driven development for behavior changes: first observe the test fail for the expected reason, then implement and verify it passes.
- Run pytest, Ruff, mypy, and the relevant desktop checks before calling a change complete.
- Store secrets only in a protected local credential store; never expose them through arguments, stdout, exceptions, or logs.
- Do not hardcode service credentials. Collect them through the app's masked local flow and document the service's storage limitations.
- Describe real limitations and mark unverified features as pending instead of presenting them as ready.
