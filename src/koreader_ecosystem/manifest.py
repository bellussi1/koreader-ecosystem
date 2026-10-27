from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class ManifestError(RuntimeError):
    """Raised when a source tree cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    kind: Literal["file", "directory"]
    size: int
    sha256: str | None
    mtime_ns: int | None


@dataclass(frozen=True, slots=True)
class ManifestDiff:
    missing: tuple[str, ...]
    extra: tuple[str, ...]
    changed: tuple[str, ...]

    @property
    def matches(self) -> bool:
        return not (self.missing or self.extra or self.changed)


def _is_unsafe_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> tuple[ManifestEntry, ...]:
    root = Path(root)
    if not root.exists() or not root.is_dir() or _is_unsafe_link(root):
        raise ManifestError("Manifest root must be an existing regular directory")

    entries: list[ManifestEntry] = []
    casefolded_paths: set[str] = set()

    try:
        paths = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
        for path in paths:
            relative = path.relative_to(root).as_posix()
            if _is_unsafe_link(path):
                raise ManifestError("Manifest trees cannot contain symbolic links")

            folded = relative.casefold()
            if folded in casefolded_paths:
                raise ManifestError("Manifest contains paths that collide on Windows")
            casefolded_paths.add(folded)

            if path.is_dir():
                entries.append(
                    ManifestEntry(relative, "directory", 0, None, path.stat().st_mtime_ns)
                )
                continue
            if not path.is_file():
                raise ManifestError("Manifest contains an unsupported filesystem entry")

            before = path.stat()
            digest = _sha256(path)
            after = path.stat()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                raise ManifestError("A file changed while its manifest was being generated")
            entries.append(
                ManifestEntry(relative, "file", after.st_size, digest, after.st_mtime_ns)
            )
    except OSError as exc:
        raise ManifestError("Unable to read the complete source tree") from exc

    return tuple(entries)


def compare_manifests(
    expected: Sequence[ManifestEntry], actual: Sequence[ManifestEntry]
) -> ManifestDiff:
    expected_by_path = {entry.path: entry for entry in expected}
    actual_by_path = {entry.path: entry for entry in actual}
    if len(expected_by_path) != len(expected) or len(actual_by_path) != len(actual):
        raise ManifestError("Manifest contains duplicate paths")

    expected_paths = set(expected_by_path)
    actual_paths = set(actual_by_path)
    missing = tuple(sorted(expected_paths - actual_paths))
    extra = tuple(sorted(actual_paths - expected_paths))
    changed = tuple(
        sorted(
            path
            for path in expected_paths & actual_paths
            if (
                expected_by_path[path].kind,
                expected_by_path[path].size,
                expected_by_path[path].sha256,
                expected_by_path[path].mtime_ns,
            )
            != (
                actual_by_path[path].kind,
                actual_by_path[path].size,
                actual_by_path[path].sha256,
                actual_by_path[path].mtime_ns,
            )
        )
    )
    return ManifestDiff(missing=missing, extra=extra, changed=changed)
