# Contributing

[English](CONTRIBUTING.md) | [Português (Brasil)](CONTRIBUTING.pt-BR.md)

Thank you for your interest in KOReader Ecosystem. The original project code is available under the [MIT License](LICENSE). Third-party software and assets keep their own licenses; see [third-party notices](licenses/THIRD-PARTY-NOTICES.txt).

All participants are expected to follow the [code of conduct](CODE_OF_CONDUCT.md). Browse [good first issues](https://github.com/bellussi1/koreader-ecosystem/labels/good%20first%20issue) for scoped starting points.

## Project principles

- Keep Calibre, KOReader, and Hardcover as distinct sources of truth. Do not silently copy status, edition, dates, ratings, or progress between them.
- Do not match books using an ambiguous title alone. Preserve edition identity and ask for review when a match is uncertain.
- Use documented service APIs and documented device protocols. Do not add scraping or undocumented endpoints as a fallback.
- Keep credentials in the operating system's credential store. Never put tokens, passwords, personal library data, device exports, or backup contents in source code, logs, fixtures, issues, or pull requests.
- Use synthetic fixtures for tests. Never commit ebook files, Calibre databases, KOReader `.sdr` sidecars, personal exports, local manifests, or backup snapshots.
- Hardware validation must be described accurately. A test with mocks is not a physical Kindle test.

## Development environment

The supported desktop target is Windows x64. Install Git, PowerShell 7, Node.js 24.15 or later in the 24.x series, or 26 or later with npm, and Python 3.14 with `uv`.

From the repository root:

```powershell
uv sync --all-groups
npm --prefix desktop ci
```

Installing the desktop dependencies also installs the Husky Git hooks. Each commit runs the Python and desktop tests, lint, formatting and type checks against the working tree; keep dependencies installed and fix failures before retrying. Run `npm --prefix desktop run prepare` to reinstall the hooks. The Windows package build and secret scan still run in CI, so a successful hook does not guarantee a successful CI run.

Calibre is required to work with a live Calibre library. A physical Kindle is only needed for device-specific USB/WPD/MTP validation. Tests and development setup must not modify a user's live library or Kindle data.

## Checks

Run the checks relevant to your change before opening a pull request:

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check src tests
uv run mypy
npm --prefix desktop test -- --run
npm --prefix desktop run typecheck
npm --prefix desktop run lint
```

GitHub Actions runs three checks on pull requests: **Python checks** runs tests, Ruff, and mypy; **Windows desktop and package** tests the renderer and bridge and builds the Windows package; **Secret scan** scans Git history. A new commit cancels older runs of the same pull request, while pushes, scheduled scans, and manual runs remain independent. The separate **Windows release** workflow runs on version tags and publishes the installer, ZIP, and hashes after its checks pass. It does not cancel an in-progress release.

To investigate a failure, open the failed check in the pull request and expand its failed step. Run the corresponding command above locally. Do not share private library data or credentials in logs or issue comments. Local checks and CI cannot replace tests on a physical Kindle.

To build the Windows desktop package, run:

```powershell
npm --prefix desktop run make
```

See [the release guide](docs/en/releasing.md) for versioning and packaging details.

## Pull requests

- Changes to `main` go through a pull request with the required checks passing and review conversations resolved. Keep your branch up to date; merges use squash.
- The default code owner is listed in [CODEOWNERS](.github/CODEOWNERS). Owner approval is not currently required, but changes involving credentials, backups, Calibre, or device writes deserve careful review.
- Dependabot proposes weekly updates for GitHub Actions, the desktop npm package, and Python's uv lockfile. Each proposal goes through the same checks and review as other changes; updates are not merged automatically.
- Keep each change focused and explain the user-visible behavior, design choice, and any limitation.
- Add or update tests for behavior changes, using synthetic data.
- Update the English and Brazilian Portuguese documentation together when changing supported workflows or user-facing behavior.
- Keep personal paths, usernames, credentials, device identifiers, book data, and local audit details out of the diff.
- Preserve the backup and rollback safeguards around writes to Calibre or a Kindle.
- State which checks ran and distinguish automated checks from physical-device validation.
- Do not bundle generated installers, user data, or unrelated cleanup into a source change.

## Documentation language policy

The root `README.md` is the English entry point. The Brazilian Portuguese entry point is `README.pt-BR.md`; active user and developer guides are mirrored under `docs/en/` and `docs/` respectively. Keep the language links near the top and update both copies in the same change. Historical implementation plans and owner-specific operational records are not official contributor guides and are not translated automatically; review them for privacy before publishing them.

## Reporting an issue

Use the repository's bug report or feature request form and share a minimal reproduction with synthetic data. Do not attach a library, ebook, Calibre database, `.sdr` folder, backup, log, screenshot, token, or password that contains personal information or secrets. For security issues, follow [the security policy](SECURITY.md).
