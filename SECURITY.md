# Security policy

[English](SECURITY.md) | [Português (Brasil)](SECURITY.pt-BR.md)

## Reporting a vulnerability

Please do not report vulnerabilities in public issues, discussions, or pull requests.

Use [GitHub Private Vulnerability Reporting](https://github.com/bellussi1/koreader-ecosystem/security/advisories/new) to contact the maintainers privately. If it is unavailable, contact the repository maintainer privately through GitHub and ask for a secure reporting channel before sharing technical details. This project does not currently publish a dedicated security contact address or a response-time commitment.

Include a concise description of the affected component, impact, affected version or commit, a reproduction using synthetic data, and any known mitigations. Avoid sending proof-of-concept material that accesses another person's data or causes damage.

## Handling reports

Maintainers should acknowledge and triage reports privately, assess affected versions and impact, and coordinate a fix and disclosure with the reporter when practical. Keep details and reproductions private until a fix or mitigation is available and disclosure is coordinated. If an incident involves exposed credentials, revoke or rotate them promptly. After a fix, publish an advisory with affected versions, remediation and any remaining limitations when appropriate. Response times depend on maintainer availability and are not guaranteed.

## Do not include

Never send real library contents, ebook files, Calibre databases, KOReader `.sdr` sidecars, backup snapshots, personal exports, access tokens, API keys, passwords, or logs and screenshots containing personal information. Redact local paths, account identifiers, device identifiers, and book information. Revoke any credential that has been exposed.

## Scope and supported versions

The application handles local Calibre data, Kindle files over USB/WPD/MTP, and credentials for connected services. Report issues that could expose or modify user data, bypass backup or confirmation safeguards, execute untrusted content, or compromise credentials. Check the latest release notes for the version currently available; no formal support schedule is published yet.
