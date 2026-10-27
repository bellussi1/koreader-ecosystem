from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from koreader_ecosystem.manifest import ManifestEntry, ManifestError, build_manifest
from koreader_ecosystem.portable_bundle import resolve_runtime_asset
from koreader_ecosystem.processes import run_hidden


class MtpError(RuntimeError):
    """Raised when the Kindle MTP adapter cannot provide a trustworthy result."""


class MtpUnavailableError(MtpError):
    """Raised when exactly one ready Kindle storage is not available."""


class MtpAmbiguousError(MtpError):
    """Raised when more than one Kindle or storage is visible."""


class MtpCopyError(MtpError):
    """Raised when local staging is incomplete or unsafe."""

    def __init__(self, reason_code: str = "copy_failed") -> None:
        safe_reason = reason_code if reason_code in _COPY_REASON_CODES else "copy_failed"
        self.reason_code = safe_reason
        super().__init__(f"Kindle staging did not complete ({safe_reason})")


@dataclass(frozen=True, slots=True)
class MtpDeviceSummary:
    roots: tuple[str, ...]
    file_count: int
    directory_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class MtpPresence:
    connected: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class MtpStageSummary(MtpDeviceSummary):
    destination: Path
    manifest: tuple[ManifestEntry, ...]
    cleanup_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class MtpReadingStageSummary:
    destination: Path
    file_count: int
    total_bytes: int
    manifest: tuple[ManifestEntry, ...]
    cleanup_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class MtpSendResult:
    filename: str
    size: int
    sha256: str
    verified: bool
    already_present: bool


class MtpSendError(MtpError):
    """A safe result from an explicit, verified Kindle transfer."""

    def __init__(self, reason_code: str, *, copied: bool = False) -> None:
        self.reason_code = reason_code if reason_code in _SEND_REASON_CODES else "send_failed"
        self.copied = copied
        super().__init__(self.reason_code)


Runner = Callable[..., subprocess.CompletedProcess[str]]
_FileRecord = tuple[str, tuple[str, ...], str]
_SCHEMA_VERSION = 4

_CONTENT_VERIFICATION_METHODS = frozenset(
    {
        "wpd-stream-sha256",
        "shell-content-readback-sha256",
        "empty-file-size",
        "wpd-stream-sha256+shell-content-readback-sha256",
        "wpd-stream-sha256+empty-file-size",
        "shell-content-readback-sha256+empty-file-size",
        "wpd-stream-sha256+shell-content-readback-sha256+empty-file-size",
    }
)
_COPY_REASON_CODES = frozenset(
    {
        "destination_exists",
        "invalid_destination",
        "invalid_name",
        "invalid_component",
        "name_collision",
        "path_too_long",
        "short_path_unavailable",
        "destination_collision",
        "insufficient_space",
        "copy_failed",
        "source_changed",
        "reading_data_ambiguous",
        "reading_wal_incomplete",
    }
)
_SEND_REASON_CODES = frozenset(
    {
        "device_not_found",
        "multiple_devices",
        "storage_not_found",
        "multiple_storages",
        "destination_exists",
        "invalid_source",
        "invalid_name",
        "copy_failed",
        "content_mismatch",
    }
)
_SEND_FORMATS = frozenset({".azw", ".azw3", ".cbz", ".epub", ".fb2", ".mobi", ".pdf", ".txt"})
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)


def _powershell_executable() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        raise MtpError("PowerShell is unavailable")
    return executable


def _parse_response(result: subprocess.CompletedProcess[str], expected_mode: str) -> dict[str, Any]:
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MtpError("MTP adapter returned invalid JSON") from exc

    if not isinstance(payload, dict):
        raise MtpError("MTP adapter returned an invalid response")
    if payload.get("schema_version") != _SCHEMA_VERSION or payload.get("mode") != expected_mode:
        raise MtpError("MTP adapter response schema does not match")

    if payload.get("status") != "ok":
        reason = payload.get("reason_code")
        if expected_mode == "presence" and reason in {
            "device_not_found",
            "multiple_devices",
            "storage_not_found",
            "multiple_storages",
        }:
            return payload
        if reason in {"device_not_found", "storage_not_found"}:
            raise MtpUnavailableError("Kindle storage is not available")
        if reason in {"multiple_devices", "multiple_storages"}:
            raise MtpAmbiguousError("More than one Kindle storage was found")
        if expected_mode in {"stage", "stage-readings"}:
            safe_reason = reason if isinstance(reason, str) else "copy_failed"
            raise MtpCopyError(safe_reason)
        if reason in {
            "destination_exists",
            "invalid_destination",
            "copy_failed",
        }:
            raise MtpCopyError("Kindle staging did not complete")
        raise MtpError("MTP adapter reported an error")

    if result.returncode != 0:
        raise MtpError("MTP adapter returned an unexpected process failure")
    return payload


def _summary(payload: dict[str, Any]) -> MtpDeviceSummary:
    roots = payload.get("roots")
    if not isinstance(roots, list) or not all(
        isinstance(root, str)
        and root not in {"", ".", ".."}
        and "/" not in root
        and "\\" not in root
        for root in roots
    ):
        raise MtpError("MTP adapter returned invalid root names")

    values: list[int] = []
    for key in ("file_count", "directory_count", "total_bytes"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MtpError("MTP adapter returned invalid aggregate counts")
        values.append(value)

    return MtpDeviceSummary(
        roots=tuple(sorted(roots, key=str.casefold)),
        file_count=values[0],
        directory_count=values[1],
        total_bytes=values[2],
    )


def _run_adapter(
    mode: str,
    runner: Runner,
    destination: Path | None = None,
) -> dict[str, Any]:
    script = str(resolve_runtime_asset("stage-kindle.ps1"))
    command = [
        _powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script,
        "-Mode",
        mode,
    ]
    if destination is not None:
        command.extend(["-Destination", str(destination)])

    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
            timeout=7200
            if mode in {"stage", "stage-readings"}
            else (8 if mode == "presence" else 60),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MtpError("Could not run the PowerShell MTP adapter") from exc
    return _parse_response(result, mode)


def detect_kindle(runner: Runner = run_hidden) -> MtpDeviceSummary:
    payload = _run_adapter("inventory", runner)
    summary = _summary(payload)
    if payload.get("device_count") != 1 or payload.get("storage_count") != 1:
        raise MtpError("MTP adapter returned inconsistent inventory counts")
    return summary


def probe_kindle(runner: Runner = run_hidden) -> MtpPresence:
    """Check USB/MTP readiness without recursively inventorying device contents."""
    payload = _run_adapter("presence", runner)
    reason = payload.get("reason_code")
    if payload.get("status") == "ok":
        if payload.get("device_count") == 1 and payload.get("storage_count") == 1:
            return MtpPresence(connected=True, reason_code="ready")
        raise MtpError("MTP adapter returned inconsistent presence counts")
    if reason in {
        "device_not_found",
        "multiple_devices",
        "storage_not_found",
        "multiple_storages",
    }:
        return MtpPresence(connected=False, reason_code=str(reason))
    raise MtpError("MTP adapter could not check device presence")


def _windows_path_identity(path: str) -> tuple[str, ...]:
    components = path.replace("\\", "/").split("/")
    identity = tuple(unicodedata.normalize("NFC", part.casefold()) for part in components)
    if not path or any(part in {"", ".", ".."} for part in identity):
        return ()
    return identity


def _verified_hashes_match(source_hashes: Any, staged_hashes: dict[str, str | None]) -> bool:
    if not isinstance(source_hashes, dict) or len(source_hashes) != len(staged_hashes):
        return False
    if not all(
        isinstance(path, str) and isinstance(digest, str) for path, digest in source_hashes.items()
    ):
        return False

    normalized_source_hashes: dict[str, str] = {}
    source_identities: set[tuple[str, ...]] = set()
    for path, digest in source_hashes.items():
        normalized_path = path.replace("\\", "/")
        identity = _windows_path_identity(path)
        if (
            _source_path_components(path) is None
            or not normalized_path
            or not identity
            or identity in source_identities
            or normalized_path in normalized_source_hashes
        ):
            return False
        source_identities.add(identity)
        normalized_source_hashes[normalized_path] = digest
    if not all(
        re.fullmatch(r"[0-9a-f]{64}", digest) for digest in normalized_source_hashes.values()
    ):
        return False

    normalized_staged_hashes: dict[str, str] = {}
    staged_identities: set[tuple[str, ...]] = set()
    for path, digest in staged_hashes.items():
        normalized_path = path.replace("\\", "/")
        identity = _windows_path_identity(path)
        if (
            _source_path_components(path) is None
            or not normalized_path
            or not identity
            or identity in staged_identities
            or normalized_path in normalized_staged_hashes
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            return False
        staged_identities.add(identity)
        normalized_staged_hashes[normalized_path] = digest

    return normalized_source_hashes == normalized_staged_hashes


def _verified_directories_match(
    source_directories: Any,
    staged_directories: tuple[ManifestEntry, ...],
) -> bool:
    if not isinstance(source_directories, list) or len(source_directories) != len(
        staged_directories
    ):
        return False

    source_paths: set[str] = set()
    source_identities: set[tuple[str, ...]] = set()
    for path in source_directories:
        if not isinstance(path, str) or _source_path_components(path) is None:
            return False
        normalized_path = path.replace("\\", "/")
        identity = _windows_path_identity(path)
        if not identity or identity in source_identities or normalized_path in source_paths:
            return False
        source_paths.add(normalized_path)
        source_identities.add(identity)

    staged_paths: set[str] = set()
    staged_identities: set[tuple[str, ...]] = set()
    for entry in staged_directories:
        if _source_path_components(entry.path) is None:
            return False
        normalized_path = entry.path.replace("\\", "/")
        identity = _windows_path_identity(entry.path)
        if not identity or identity in staged_identities or normalized_path in staged_paths:
            return False
        staged_paths.add(normalized_path)
        staged_identities.add(identity)

    return source_paths == staged_paths


def _source_path_components(path: str) -> tuple[str, ...] | None:
    components = tuple(path.replace("\\", "/").split("/"))
    if not components or any(component in {"", ".", ".."} for component in components):
        return None
    if any(
        any(ord(character) < 32 or character in '<>:"|?*' for character in component)
        or component.endswith((" ", "."))
        or component.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
        for component in components
    ):
        return None
    return components


def _canonicalize_stage_paths(
    destination: Path,
    source_hashes: Any,
    source_directories: Any,
    staged_manifest: tuple[ManifestEntry, ...],
    summary: MtpDeviceSummary,
    source_path_map: Any = None,
) -> tuple[tuple[ManifestEntry, ...], tuple[Path, ...]]:
    if source_path_map is not None:
        return _canonicalize_stage_paths_from_map(
            destination,
            source_hashes,
            source_directories,
            staged_manifest,
            summary,
            source_path_map,
        )

    if (
        not isinstance(source_hashes, dict)
        or len(source_hashes) != summary.file_count
        or not isinstance(source_directories, list)
        or len(source_directories) != summary.directory_count
    ):
        raise MtpCopyError("source_changed")

    source_file_identities: set[tuple[str, ...]] = set()
    source_files: list[tuple[str, tuple[str, ...], str]] = []
    for path, digest in source_hashes.items():
        if not isinstance(path, str) or not isinstance(digest, str):
            raise MtpCopyError("source_changed")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise MtpCopyError("source_changed")
        components = _source_path_components(path)
        identity = _windows_path_identity(path)
        if components is None or not identity or identity in source_file_identities:
            raise MtpCopyError("source_changed")
        source_file_identities.add(identity)
        source_files.append((path, components, digest))

    source_dirs: list[tuple[str, tuple[str, ...]]] = []
    source_directory_identities: set[tuple[str, ...]] = set()
    for path in source_directories:
        if not isinstance(path, str):
            raise MtpCopyError("source_changed")
        components = _source_path_components(path)
        identity = _windows_path_identity(path)
        if components is None or not identity or identity in source_directory_identities:
            raise MtpCopyError("source_changed")
        source_directory_identities.add(identity)
        source_dirs.append((path, components))

    source_root_identities = {
        _windows_path_identity(path) for path, components in source_dirs if len(components) == 1
    }
    if source_root_identities != {_windows_path_identity(root) for root in summary.roots}:
        raise MtpCopyError("source_changed")

    staged_manifest_files = tuple(entry for entry in staged_manifest if entry.kind == "file")
    staged_directories = tuple(entry for entry in staged_manifest if entry.kind == "directory")
    staged_dirs: list[tuple[str, tuple[str, ...]]] = []
    staged_directory_identities: set[tuple[str, ...]] = set()
    for entry in staged_directories:
        components = _source_path_components(entry.path)
        identity = _windows_path_identity(entry.path)
        if components is None or not identity or identity in staged_directory_identities:
            raise MtpCopyError("source_changed")
        staged_directory_identities.add(identity)
        staged_dirs.append((entry.path, components))

    staged_file_identities: set[tuple[str, ...]] = set()
    staged_file_records: list[_FileRecord] = []
    for entry in staged_manifest_files:
        if not isinstance(entry.sha256, str):
            raise MtpCopyError("source_changed")
        components = _source_path_components(entry.path)
        identity = _windows_path_identity(entry.path)
        if components is None or not identity or identity in staged_file_identities:
            raise MtpCopyError("source_changed")
        staged_file_identities.add(identity)
        staged_file_records.append((entry.path, components, entry.sha256))

    if len(source_files) != len(staged_manifest_files) or len(source_dirs) != len(
        staged_directories
    ):
        raise MtpCopyError("source_changed")

    def validate_file_parents(
        files: list[_FileRecord],
        directory_identities: set[tuple[str, ...]],
    ) -> None:
        for record in files:
            components = record[1]
            for length in range(1, len(components)):
                parent_identity = _windows_path_identity("/".join(components[:length]))
                if parent_identity not in directory_identities:
                    raise MtpCopyError("source_changed")
            if _windows_path_identity("/".join(components)) in directory_identities:
                raise MtpCopyError("source_changed")

    validate_file_parents(source_files, source_directory_identities)
    validate_file_parents(staged_file_records, staged_directory_identities)

    def directory_signature(
        base: tuple[str, ...],
        directories: list[tuple[str, tuple[str, ...]]],
        files: list[_FileRecord],
    ) -> tuple[tuple[str, int, str, str], ...]:
        records: list[tuple[str, int, str, str]] = []
        for _, components in directories:
            if len(components) > len(base) and components[: len(base)] == base:
                records.append(("directory", len(components) - len(base), "", ""))
        for _, components, digest in files:
            if components[: len(base)] != base:
                continue
            records.append(
                (
                    "file",
                    len(components) - len(base),
                    PurePosixPath(components[-1]).suffix.casefold(),
                    digest,
                )
            )
        return tuple(sorted(records))

    def direct_child_directories(
        parent: tuple[str, ...], directories: list[tuple[str, tuple[str, ...]]]
    ) -> list[tuple[str, tuple[str, ...]]]:
        return [
            item
            for item in directories
            if len(item[1]) == len(parent) + 1 and item[1][:-1] == parent
        ]

    def direct_child_files(
        parent: tuple[str, ...],
        files: list[_FileRecord],
    ) -> list[_FileRecord]:
        return [
            item for item in files if len(item[1]) == len(parent) + 1 and item[1][:-1] == parent
        ]

    source_roots = [item for item in source_dirs if len(item[1]) == 1]
    staged_roots = [item for item in staged_dirs if len(item[1]) == 1]
    staged_roots_by_identity = {
        _windows_path_identity(path): (path, components) for path, components in staged_roots
    }
    if len(staged_roots_by_identity) != len(staged_roots):
        raise MtpCopyError("source_changed")

    file_pairs: list[tuple[str, str]] = []
    mapped_source_directories: set[tuple[str, ...]] = set()
    mapped_staged_directories: set[tuple[str, ...]] = set()

    def pair_files(source_parent: tuple[str, ...], staged_parent: tuple[str, ...]) -> None:
        remaining_source = list(direct_child_files(source_parent, source_files))
        remaining_staged = list(direct_child_files(staged_parent, staged_file_records))
        staged_by_name = {
            _windows_path_identity(record[1][-1]): record for record in remaining_staged
        }
        if len(staged_by_name) != len(remaining_staged):
            raise MtpCopyError("source_changed")

        unmatched_source: list[_FileRecord] = []
        matched_staged: set[str] = set()
        for source_path, source_components, source_digest in remaining_source:
            staged_match = staged_by_name.get(_windows_path_identity(source_components[-1]))
            if staged_match is None:
                unmatched_source.append((source_path, source_components, source_digest))
                continue
            if staged_match[2] != source_digest:
                raise MtpCopyError("source_changed")
            file_pairs.append((source_path, staged_match[0]))
            matched_staged.add(staged_match[0])

        remaining_staged = [item for item in remaining_staged if item[0] not in matched_staged]
        source_groups: dict[tuple[str, str], list[_FileRecord]] = defaultdict(list)
        staged_groups: dict[tuple[str, str], list[_FileRecord]] = defaultdict(list)
        for record in unmatched_source:
            source_groups[(PurePosixPath(record[1][-1]).suffix.casefold(), record[2])].append(
                record
            )
        for record in remaining_staged:
            staged_groups[(PurePosixPath(record[1][-1]).suffix.casefold(), record[2])].append(
                record
            )
        if source_groups.keys() != staged_groups.keys():
            raise MtpCopyError("source_changed")
        for key, source_group in source_groups.items():
            staged_group = staged_groups[key]
            if len(source_group) != 1 or len(staged_group) != 1:
                raise MtpCopyError("source_changed")
            file_pairs.append((source_group[0][0], staged_group[0][0]))

    def pair_directory_children(
        source_parent: tuple[str, ...], staged_parent: tuple[str, ...]
    ) -> None:
        source_children = direct_child_directories(source_parent, source_dirs)
        staged_children = direct_child_directories(staged_parent, staged_dirs)
        staged_by_identity = {
            _windows_path_identity(path): (path, components) for path, components in staged_children
        }
        if len(staged_by_identity) != len(staged_children):
            raise MtpCopyError("source_changed")

        pairs: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        unmatched_source: list[tuple[str, tuple[str, ...]]] = []
        matched_staged: set[tuple[str, ...]] = set()
        for source_path, source_components in source_children:
            staged_match = staged_by_identity.get(_windows_path_identity(source_path))
            if staged_match is None:
                unmatched_source.append((source_path, source_components))
                continue
            staged_path, staged_components = staged_match
            if directory_signature(
                source_components, source_dirs, source_files
            ) != directory_signature(staged_components, staged_dirs, staged_file_records):
                raise MtpCopyError("source_changed")
            pairs.append((source_components, staged_components))
            matched_staged.add(staged_components)

        unmatched_staged = [item for item in staged_children if item[1] not in matched_staged]
        source_groups: dict[
            tuple[tuple[str, int, str, str], ...], list[tuple[str, tuple[str, ...]]]
        ] = defaultdict(list)
        staged_groups: dict[
            tuple[tuple[str, int, str, str], ...], list[tuple[str, tuple[str, ...]]]
        ] = defaultdict(list)
        for path, components in unmatched_source:
            source_groups[directory_signature(components, source_dirs, source_files)].append(
                (path, components)
            )
        for path, components in unmatched_staged:
            staged_groups[directory_signature(components, staged_dirs, staged_file_records)].append(
                (path, components)
            )
        if source_groups.keys() != staged_groups.keys():
            raise MtpCopyError("source_changed")
        for signature, source_group in source_groups.items():
            staged_group = staged_groups[signature]
            if len(source_group) != 1 or len(staged_group) != 1:
                raise MtpCopyError("source_changed")
            pairs.append((source_group[0][1], staged_group[0][1]))

        for source_components, staged_components in pairs:
            source_identity = _windows_path_identity("/".join(source_components))
            staged_identity = _windows_path_identity("/".join(staged_components))
            if (
                source_identity in mapped_source_directories
                or staged_identity in mapped_staged_directories
            ):
                raise MtpCopyError("source_changed")
            mapped_source_directories.add(source_identity)
            mapped_staged_directories.add(staged_identity)
            pair_files(source_components, staged_components)
            pair_directory_children(source_components, staged_components)

    for source_root_path, source_root_components in source_roots:
        staged_root = staged_roots_by_identity.get(_windows_path_identity(source_root_path))
        if staged_root is None:
            raise MtpCopyError("source_changed")
        staged_root_path, staged_root_components = staged_root
        if directory_signature(
            source_root_components, source_dirs, source_files
        ) != directory_signature(staged_root_components, staged_dirs, staged_file_records):
            raise MtpCopyError("source_changed")
        mapped_source_directories.add(_windows_path_identity(source_root_path))
        mapped_staged_directories.add(_windows_path_identity(staged_root_path))
        pair_files(source_root_components, staged_root_components)
        pair_directory_children(source_root_components, staged_root_components)

    if (
        len(mapped_source_directories) != len(source_dirs)
        or len(mapped_staged_directories) != len(staged_dirs)
        or len(file_pairs) != len(source_files)
    ):
        raise MtpCopyError("source_changed")

    canonical_parent = destination.parent
    canonical_root = Path(tempfile.mkdtemp(prefix=".kre-stage-canonical-", dir=canonical_parent))
    original_path = canonical_root.with_name(canonical_root.name + ".original")
    try:
        for _, components in sorted(
            source_dirs, key=lambda item: (len(item[1]), item[0].casefold())
        ):
            canonical_root.joinpath(*components).mkdir(parents=True, exist_ok=True)

        for source_path, staged_path in file_pairs:
            source_components = _source_path_components(source_path)
            staged_components = _source_path_components(staged_path)
            if source_components is None or staged_components is None:
                raise MtpCopyError("source_changed")
            target_path = canonical_root.joinpath(*source_components)
            if target_path.exists():
                raise MtpCopyError("source_changed")
            staged_source_path = destination.joinpath(*staged_components)
            try:
                target_path.hardlink_to(staged_source_path)
            except OSError:
                shutil.copyfile(staged_source_path, target_path)

        canonical_manifest = build_manifest(canonical_root)
        canonical_files = tuple(entry for entry in canonical_manifest if entry.kind == "file")
        canonical_dirs = tuple(entry for entry in canonical_manifest if entry.kind == "directory")
        canonical_roots = tuple(
            sorted(
                (entry.path for entry in canonical_dirs if "/" not in entry.path),
                key=str.casefold,
            )
        )
        canonical_hashes = {entry.path: entry.sha256 for entry in canonical_files}
        if (
            len(canonical_files) != summary.file_count
            or len(canonical_dirs) != summary.directory_count
            or sum(entry.size for entry in canonical_files) != summary.total_bytes
            or canonical_roots != summary.roots
            or not _verified_hashes_match(source_hashes, canonical_hashes)
        ):
            raise MtpCopyError("source_changed")

        destination.rename(original_path)
        try:
            canonical_root.rename(destination)
        except OSError:
            original_path.rename(destination)
            raise
        try:
            shutil.rmtree(original_path)
            cleanup_paths: tuple[Path, ...] = ()
        except OSError:
            cleanup_paths = (original_path,)
        return canonical_manifest, cleanup_paths
    except OSError, ManifestError:
        if original_path.exists() and not destination.exists():
            original_path.rename(destination)
        if canonical_root.exists():
            shutil.rmtree(canonical_root, ignore_errors=True)
        raise MtpCopyError("source_changed") from None
    except MtpCopyError:
        if original_path.exists() and not destination.exists():
            original_path.rename(destination)
        if canonical_root.exists():
            shutil.rmtree(canonical_root, ignore_errors=True)
        raise


def _canonicalize_stage_paths_from_map(
    destination: Path,
    source_hashes: Any,
    source_directories: Any,
    staged_manifest: tuple[ManifestEntry, ...],
    summary: MtpDeviceSummary,
    source_path_map: Any,
) -> tuple[tuple[ManifestEntry, ...], tuple[Path, ...]]:
    if (
        not isinstance(source_hashes, dict)
        or len(source_hashes) != summary.file_count
        or not isinstance(source_directories, list)
        or len(source_directories) != summary.directory_count
        or not isinstance(source_path_map, dict)
        or len(source_path_map) != summary.file_count + summary.directory_count
    ):
        raise MtpCopyError("source_changed")

    source_nodes: dict[tuple[str, ...], tuple[str, tuple[str, ...], str, str | None]] = {}
    for path, digest in source_hashes.items():
        components = _source_path_components(path) if isinstance(path, str) else None
        identity = _windows_path_identity(path) if isinstance(path, str) else ()
        if (
            components is None
            or not identity
            or identity in source_nodes
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise MtpCopyError("source_changed")
        source_nodes[identity] = (path, components, "file", digest)

    for path in source_directories:
        components = _source_path_components(path) if isinstance(path, str) else None
        identity = _windows_path_identity(path) if isinstance(path, str) else ()
        if components is None or not identity or identity in source_nodes:
            raise MtpCopyError("source_changed")
        source_nodes[identity] = (path, components, "directory", None)

    source_to_staged: dict[tuple[str, ...], tuple[str, tuple[str, ...]]] = {}
    staged_path_identities: set[tuple[str, ...]] = set()
    for source_path, staged_path in source_path_map.items():
        source_components = (
            _source_path_components(source_path) if isinstance(source_path, str) else None
        )
        staged_components = (
            _source_path_components(staged_path) if isinstance(staged_path, str) else None
        )
        source_identity = (
            _windows_path_identity(source_path) if isinstance(source_path, str) else ()
        )
        staged_identity = (
            _windows_path_identity(staged_path) if isinstance(staged_path, str) else ()
        )
        if (
            source_components is None
            or staged_components is None
            or source_identity not in source_nodes
            or source_identity in source_to_staged
            or not staged_identity
            or staged_identity in staged_path_identities
        ):
            raise MtpCopyError("source_changed")
        source_to_staged[source_identity] = (staged_path, staged_components)
        staged_path_identities.add(staged_identity)

    if source_to_staged.keys() != source_nodes.keys():
        raise MtpCopyError("source_changed")

    staged_nodes: dict[tuple[str, ...], tuple[ManifestEntry, tuple[str, ...], str]] = {}
    for entry in staged_manifest:
        components = _source_path_components(entry.path)
        identity = _windows_path_identity(entry.path)
        if components is None or not identity or identity in staged_nodes:
            raise MtpCopyError("source_changed")
        if entry.kind not in {"file", "directory"}:
            raise MtpCopyError("source_changed")
        if entry.kind == "file" and (
            not isinstance(entry.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", entry.sha256)
        ):
            raise MtpCopyError("source_changed")
        staged_nodes[identity] = (entry, components, entry.kind)

    if len(source_nodes) != len(staged_nodes):
        raise MtpCopyError("source_changed")

    source_pairs: list[tuple[str, str]] = []
    mapped_stage_identities: set[tuple[str, ...]] = set()
    for source_identity, (source_path, source_components, kind, digest) in source_nodes.items():
        staged_path, staged_components = source_to_staged[source_identity]
        staged_identity = _windows_path_identity(staged_path)
        staged_match = staged_nodes.get(staged_identity)
        if staged_match is None:
            raise MtpCopyError("source_changed")
        staged_entry, actual_staged_components, staged_kind = staged_match
        if staged_kind != kind or staged_identity in mapped_stage_identities:
            raise MtpCopyError("source_changed")
        if kind == "file" and staged_entry.sha256 != digest:
            raise MtpCopyError("source_changed")
        if len(source_components) != len(staged_components):
            raise MtpCopyError("source_changed")
        if kind == "file":
            source_pairs.append((source_path, staged_entry.path))
        mapped_stage_identities.add(staged_identity)

    if mapped_stage_identities != staged_nodes.keys():
        raise MtpCopyError("source_changed")

    for source_identity, (_, source_components, _, _) in source_nodes.items():
        if len(source_components) <= 1:
            continue
        parent_identity = _windows_path_identity("/".join(source_components[:-1]))
        parent_mapping = source_to_staged.get(parent_identity)
        staged_path, staged_components = source_to_staged[source_identity]
        if (
            parent_mapping is None
            or _windows_path_identity(parent_mapping[0])
            != _windows_path_identity("/".join(staged_components[:-1]))
            or len(staged_components) != len(source_components)
            or _windows_path_identity(staged_path)
            != _windows_path_identity("/".join(staged_components))
        ):
            raise MtpCopyError("source_changed")

    source_roots = {
        source_nodes[identity][0]: source_to_staged[identity][0]
        for identity in source_nodes
        if source_nodes[identity][2] == "directory" and len(source_nodes[identity][1]) == 1
    }
    if {_windows_path_identity(path) for path in source_roots.values()} != {
        _windows_path_identity(root) for root in summary.roots
    }:
        raise MtpCopyError("source_changed")

    canonical_parent = destination.parent
    canonical_root = Path(tempfile.mkdtemp(prefix=".kre-stage-canonical-", dir=canonical_parent))
    original_path = canonical_root.with_name(canonical_root.name + ".original")
    try:
        source_directories_with_components = [
            (path, node[1])
            for node_identity, node in source_nodes.items()
            if node[2] == "directory"
            for path in (node[0],)
        ]
        for _, components in sorted(
            source_directories_with_components,
            key=lambda item: (len(item[1]), item[0].casefold()),
        ):
            directory = canonical_root.joinpath(*components)
            if directory.exists():
                raise MtpCopyError("source_changed")
            directory.mkdir(parents=True)

        for source_path, staged_path in source_pairs:
            source_components = _source_path_components(source_path)
            staged_components = _source_path_components(staged_path)
            if source_components is None or staged_components is None:
                raise MtpCopyError("source_changed")
            target_path = canonical_root.joinpath(*source_components)
            if target_path.exists():
                raise MtpCopyError("source_changed")
            staged_source_path = destination.joinpath(*staged_components)
            try:
                target_path.hardlink_to(staged_source_path)
            except OSError:
                shutil.copyfile(staged_source_path, target_path)

        canonical_manifest = build_manifest(canonical_root)
        canonical_files = tuple(entry for entry in canonical_manifest if entry.kind == "file")
        canonical_dirs = tuple(entry for entry in canonical_manifest if entry.kind == "directory")
        canonical_roots = tuple(
            sorted(
                (entry.path for entry in canonical_dirs if "/" not in entry.path),
                key=str.casefold,
            )
        )
        canonical_hashes = {entry.path: entry.sha256 for entry in canonical_files}
        expected_roots = tuple(sorted(source_roots, key=str.casefold))
        if (
            len(canonical_files) != summary.file_count
            or len(canonical_dirs) != summary.directory_count
            or sum(entry.size for entry in canonical_files) != summary.total_bytes
            or canonical_roots != expected_roots
            or not _verified_hashes_match(source_hashes, canonical_hashes)
            or not _verified_directories_match(source_directories, canonical_dirs)
        ):
            raise MtpCopyError("source_changed")

        destination.rename(original_path)
        try:
            canonical_root.rename(destination)
        except OSError:
            original_path.rename(destination)
            raise
        try:
            shutil.rmtree(original_path)
            cleanup_paths: tuple[Path, ...] = ()
        except OSError:
            cleanup_paths = (original_path,)
        return canonical_manifest, cleanup_paths
    except OSError, ManifestError:
        if original_path.exists() and not destination.exists():
            original_path.rename(destination)
        if canonical_root.exists():
            shutil.rmtree(canonical_root, ignore_errors=True)
        raise MtpCopyError("source_changed") from None
    except MtpCopyError:
        if original_path.exists() and not destination.exists():
            original_path.rename(destination)
        if canonical_root.exists():
            shutil.rmtree(canonical_root, ignore_errors=True)
        raise


def stage_kindle(destination: Path, runner: Runner = run_hidden) -> MtpStageSummary:
    destination = Path(destination)
    if not destination.is_absolute():
        raise MtpCopyError("invalid_destination")
    if destination.exists() or destination.is_symlink() or destination.is_junction():
        raise MtpCopyError("destination_exists")

    payload = _run_adapter("stage", runner, destination)
    summary = _summary(payload)
    if payload.get("device_count") != 1 or payload.get("storage_count") != 1:
        raise MtpCopyError("MTP adapter returned inconsistent device counts")
    if payload.get("content_verified") is not True:
        raise MtpCopyError("source_changed")
    if payload.get("content_verification_method") not in _CONTENT_VERIFICATION_METHODS:
        raise MtpCopyError("source_changed")
    if payload.get("source_paths_verified") is not True:
        raise MtpCopyError("source_changed")

    try:
        manifest = build_manifest(destination)
    except ManifestError as exc:
        raise MtpCopyError("The staged Kindle tree is incomplete") from exc

    files = tuple(entry for entry in manifest if entry.kind == "file")
    directories = tuple(entry for entry in manifest if entry.kind == "directory")
    total_bytes = sum(entry.size for entry in files)
    roots = tuple(
        sorted(
            (
                entry.path
                for entry in directories
                if "/" not in entry.path and "\\" not in entry.path
            ),
            key=str.casefold,
        )
    )

    if (
        len(files) != summary.file_count
        or len(directories) != summary.directory_count
        or total_bytes != summary.total_bytes
        or roots != summary.roots
    ):
        raise MtpCopyError("Staged Kindle tree does not match the MTP inventory")

    staged_hashes = {entry.path: entry.sha256 for entry in files}
    source_hashes = payload.get("source_sha256")
    source_directories = payload.get("source_directories")
    cleanup_paths: tuple[Path, ...] = ()
    if not _verified_hashes_match(source_hashes, staged_hashes) or not _verified_directories_match(
        source_directories, directories
    ):
        manifest, cleanup_paths = _canonicalize_stage_paths(
            destination,
            source_hashes,
            source_directories,
            manifest,
            summary,
            payload.get("source_path_map"),
        )

    return MtpStageSummary(
        destination=destination,
        roots=summary.roots,
        file_count=summary.file_count,
        directory_count=summary.directory_count,
        total_bytes=summary.total_bytes,
        manifest=manifest,
        cleanup_paths=cleanup_paths,
    )


def _is_reading_data_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return False
    filename = parts[-1].casefold()
    if filename in {"statistics.sqlite3", "statistics.sqlite3-wal", "statistics.sqlite3-shm"}:
        return True
    return re.fullmatch(r"metadata\..+\.lua", filename) is not None and any(
        part.casefold().endswith(".sdr") for part in parts[:-1]
    )


def _validate_reading_file_set(paths: tuple[str, ...]) -> None:
    databases = tuple(
        path for path in paths if path.rsplit("/", 1)[-1].casefold() == "statistics.sqlite3"
    )
    companions = tuple(
        path
        for path in paths
        if path.rsplit("/", 1)[-1].casefold()
        in {"statistics.sqlite3-wal", "statistics.sqlite3-shm"}
    )
    if len(databases) > 1:
        raise MtpCopyError("reading_data_ambiguous")
    if not databases:
        if companions:
            raise MtpCopyError("reading_wal_incomplete")
        return

    database = databases[0]
    wal = database + "-wal"
    shm = database + "-shm"
    actual_companions = {path.casefold() for path in companions}
    expected_companions = {path.casefold() for path in (wal, shm)}
    if actual_companions - expected_companions or len(actual_companions) not in {0, 2}:
        raise MtpCopyError("reading_wal_incomplete")


def stage_koreader_reading_data(
    destination: Path,
    runner: Runner = run_hidden,
) -> MtpReadingStageSummary:
    """Stage only KOReader statistics and metadata sidecars from Kindle MTP storage."""
    destination = Path(destination)
    if not destination.is_absolute():
        raise MtpCopyError("invalid_destination")
    if destination.exists() or destination.is_symlink() or destination.is_junction():
        raise MtpCopyError("destination_exists")

    payload = _run_adapter("stage-readings", runner, destination)
    summary = _summary(payload)
    if payload.get("device_count") != 1 or payload.get("storage_count") != 1:
        raise MtpCopyError("MTP adapter returned inconsistent device counts")
    if payload.get("content_verified") is not True:
        raise MtpCopyError("source_changed")
    if payload.get("content_verification_method") not in _CONTENT_VERIFICATION_METHODS:
        raise MtpCopyError("source_changed")
    if payload.get("source_paths_verified") is not True:
        raise MtpCopyError("source_changed")

    try:
        manifest = build_manifest(destination)
    except ManifestError as exc:
        raise MtpCopyError("The staged KOReader data is incomplete") from exc

    files = tuple(entry for entry in manifest if entry.kind == "file")
    directories = tuple(entry for entry in manifest if entry.kind == "directory")
    total_bytes = sum(entry.size for entry in files)
    roots = tuple(
        sorted(
            (
                entry.path
                for entry in directories
                if "/" not in entry.path and "\\" not in entry.path
            ),
            key=str.casefold,
        )
    )
    if (
        len(files) != summary.file_count
        or len(directories) != summary.directory_count
        or total_bytes != summary.total_bytes
        or roots != summary.roots
    ):
        raise MtpCopyError("Staged KOReader data does not match the MTP inventory")

    staged_hashes = {entry.path: entry.sha256 for entry in files}
    source_hashes = payload.get("source_sha256")
    source_directories = payload.get("source_directories")
    cleanup_paths: tuple[Path, ...] = ()
    if not _verified_hashes_match(source_hashes, staged_hashes) or not _verified_directories_match(
        source_directories, directories
    ):
        manifest, cleanup_paths = _canonicalize_stage_paths(
            destination,
            source_hashes,
            source_directories,
            manifest,
            summary,
            payload.get("source_path_map"),
        )
        files = tuple(entry for entry in manifest if entry.kind == "file")
        file_paths = tuple(entry.path for entry in files)
        staged_hashes = {entry.path: entry.sha256 for entry in files}
        if not _verified_hashes_match(source_hashes, staged_hashes):
            raise MtpCopyError("source_changed")
    file_paths = tuple(entry.path for entry in files)
    if not all(_is_reading_data_path(path) for path in file_paths):
        raise MtpCopyError("MTP adapter staged data outside the KOReader reading allowlist")
    _validate_reading_file_set(file_paths)
    if not _verified_hashes_match(source_hashes, staged_hashes):
        raise MtpCopyError("source_changed")

    return MtpReadingStageSummary(
        destination=destination,
        file_count=summary.file_count,
        total_bytes=summary.total_bytes,
        manifest=manifest,
        cleanup_paths=cleanup_paths,
    )


def send_kindle_file(
    source: str | Path,
    filename: str,
    runner: Runner = run_hidden,
) -> MtpSendResult:
    """Copy one new ebook through WPD/MTP, then read it back and compare SHA-256."""
    source_path = Path(source).expanduser()
    if not source_path.is_absolute():
        raise MtpSendError("invalid_source")
    try:
        resolved_source = source_path.resolve(strict=True)
        source_details = resolved_source.stat()
    except OSError, RuntimeError:
        raise MtpSendError("invalid_source") from None
    suffix = resolved_source.suffix.casefold()
    if (
        source_path.is_symlink()
        or source_path.is_junction()
        or not resolved_source.is_file()
        or suffix not in _SEND_FORMATS
        or source_details.st_size <= 0
    ):
        raise MtpSendError("invalid_source")
    if (
        not isinstance(filename, str)
        or not filename
        or len(filename) > 180
        or filename in {".", ".."}
        or filename.rstrip(" .") != filename
        or any(character in filename for character in '<>:"/\\|?*')
        or any(ord(character) < 32 or ord(character) == 127 for character in filename)
        or Path(filename).suffix.casefold() != suffix
        or Path(filename).stem.upper() in _WINDOWS_RESERVED_NAMES
    ):
        raise MtpSendError("invalid_name")

    script = str(resolve_runtime_asset("send-kindle.ps1"))
    command = [
        _powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script,
        "-SourcePath",
        str(resolved_source),
        "-TargetName",
        filename,
    ]
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
    except OSError, subprocess.SubprocessError:
        raise MtpSendError("send_failed") from None

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError, TypeError:
        raise MtpSendError("send_failed") from None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("mode") != "send"
    ):
        raise MtpSendError("send_failed")
    if payload.get("status") != "ok":
        reason = payload.get("reason_code")
        copied = payload.get("copied") is True
        raise MtpSendError(reason if isinstance(reason, str) else "send_failed", copied=copied)
    digest = payload.get("sha256")
    size = payload.get("size")
    if (
        result.returncode != 0
        or payload.get("filename") != filename
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size != source_details.st_size
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or payload.get("verified") is not True
        or not isinstance(payload.get("already_present"), bool)
    ):
        raise MtpSendError("send_failed", copied=payload.get("copied") is True)
    return MtpSendResult(
        filename=filename,
        size=size,
        sha256=digest,
        verified=True,
        already_present=payload["already_present"],
    )
