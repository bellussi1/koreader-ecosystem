from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from koreader_ecosystem.restic import ResticClient, ResticSnapshot


class RemoteSyncError(RuntimeError):
    """Raised when a remote Restic mirror is incomplete or unavailable."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__("Remote Restic synchronization failed")


@dataclass(frozen=True, slots=True)
class RemoteSyncResult:
    source_snapshot_count: int
    remote_snapshot_count: int


def _snapshot_key(snapshot: ResticSnapshot) -> str:
    if not snapshot.signature:
        raise RemoteSyncError("snapshot_signature_unavailable")
    return snapshot.signature


def _snapshots(client: ResticClient, *, source: bool) -> tuple[ResticSnapshot, ...]:
    result = client.run(["snapshots", "--json"])
    if not result.ok:
        raise RemoteSyncError(
            "source_snapshots_unavailable" if source else "remote_snapshots_unavailable"
        )
    return result.snapshots


def sync_remote(source: ResticClient, destination: ResticClient) -> RemoteSyncResult:
    """Mirror local snapshots to S3 and verify the destination without exposing metadata."""
    if not isinstance(source.repository, Path):
        raise RemoteSyncError("source_repository_must_be_local")

    initial_source_snapshots = _snapshots(source, source=True)
    if not initial_source_snapshots:
        raise RemoteSyncError("source_repository_has_no_snapshots")

    destination_config = destination.run(["cat", "config"])
    if destination_config.returncode == 10:
        initialized = destination.run_copy_from(
            [
                "init",
                "--from-repo",
                str(source.repository),
                "--copy-chunker-params",
            ],
            source,
        )
        if not initialized.ok:
            raise RemoteSyncError("remote_repository_initialization_failed")
    elif not destination_config.ok:
        raise RemoteSyncError("remote_repository_unavailable")

    copied = destination.run_copy_from(["copy"], source)
    if not copied.ok:
        raise RemoteSyncError("remote_snapshot_copy_failed")

    checked = destination.run(["check"])
    if not checked.ok:
        raise RemoteSyncError("remote_repository_check_failed")

    verified_source = _snapshots(source, source=True)
    verified_destination = _snapshots(destination, source=False)
    missing = Counter(map(_snapshot_key, verified_source)) - Counter(
        map(_snapshot_key, verified_destination)
    )
    if missing:
        raise RemoteSyncError("remote_snapshot_verification_failed")

    return RemoteSyncResult(
        source_snapshot_count=len(verified_source),
        remote_snapshot_count=len(verified_destination),
    )
