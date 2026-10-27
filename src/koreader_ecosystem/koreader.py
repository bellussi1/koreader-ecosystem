from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from stat import S_ISLNK
from typing import Any

from koreader_ecosystem import __version__
from koreader_ecosystem.portable_bundle import resolve_runtime_asset
from koreader_ecosystem.processes import run_hidden
from koreader_ecosystem.safety import SafetyViolation, remove_tree


class PluginInstallError(RuntimeError):
    """Raised when the pinned plugin package or MTP installation is unsafe."""

    def __init__(self, reason_code: str = "install_failed") -> None:
        self.reason_code = reason_code if reason_code in _REASON_CODES else "install_failed"
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class PluginInstallResult:
    file_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class PluginUpdateResult:
    file_count: int
    total_bytes: int
    updated: bool
    rollback_backup_id: str | None


Runner = Callable[..., subprocess.CompletedProcess[str]]
Opener = Callable[..., Any]

PLUGIN_VERSION = "0.4.0"
PLUGIN_SHA256 = "0ca1eba0eccc627b0cab8c3457883ae7a9e771b2cd26aa09794a7041904e1b10"
PLUGIN_RELEASE_URL = (
    "https://github.com/Billiam/hardcoverapp.koplugin/releases/download/0.4.0/"
    "hardcoverapp.koplugin.zip"
)
PLUGIN_PATCH_FILE = resolve_runtime_asset("hardcoverapp-0.4.0.patch")
PLUGIN_PATCH_SHA256 = "5637fb9779b9c739beaafda1b1fe2a6ff918610633d3c35bba930aa465241d45"
INSTALL_SCRIPT = resolve_runtime_asset("install-hardcover-plugin.ps1")
_PLUGIN_NAME = "hardcoverapp.koplugin"
_MAX_ARCHIVE_BYTES = 5 * 1024 * 1024
_MAX_ARCHIVE_FILES = 500
_PLUGIN_PREVIOUS_PATCH_SHA256 = {
    "hardcover/lib/cache.lua": "ac8f4fc367581276e9688340fb4e160c26d54a42ca24f85cebecc28bcf568421",
    "hardcover/lib/hardcover_api.lua": (
        "77a35b968ab92355762e826a8506db69d73822dbb92df195b32ce19f3f2e58e3"
    ),
    "main.lua": "3122eabfee7313a3a487ee54bc18a82e1fd7ff10cb327edcd7c5b663dd654425",
}
_REASON_CODES = frozenset(
    {
        "authentication_failed",
        "device_not_found",
        "multiple_devices",
        "storage_not_found",
        "multiple_storages",
        "koreader_not_found",
        "plugins_not_found",
        "destination_exists",
        "plugin_not_installed",
        "plugin_version_mismatch",
        "rollback_failed",
        "package_download_failed",
        "package_digest_mismatch",
        "package_invalid",
        "patch_digest_mismatch",
        "patch_invalid",
        "copy_failed",
        "source_changed",
        "install_failed",
    }
)


def _validate_plugin_archive(archive_path: Path) -> tuple[zipfile.ZipInfo, ...]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = tuple(archive.infolist())
    except OSError, zipfile.BadZipFile:
        raise PluginInstallError("package_invalid") from None

    files: list[zipfile.ZipInfo] = []
    seen: set[str] = set()
    total_size = 0
    for info in infos:
        name = info.filename
        if (
            not name
            or name.startswith(("/", "\\"))
            or "\\" in name
            or ":" in name
            or "\x00" in name
        ):
            raise PluginInstallError("package_invalid")
        is_directory = info.is_dir()
        normalized_name = name[:-1] if is_directory else name
        path = PurePosixPath(normalized_name)
        if is_directory and path.parts == (_PLUGIN_NAME,):
            continue
        if (
            path.is_absolute()
            or any(component in {"", ".", ".."} for component in path.parts)
            or len(path.parts) < 2
            or path.parts[0] != _PLUGIN_NAME
        ):
            raise PluginInstallError("package_invalid")
        if is_directory:
            continue
        if len(path.parts) < 2:
            raise PluginInstallError("package_invalid")
        if info.flag_bits & 0x1 or S_ISLNK((info.external_attr >> 16) & 0xFFFF):
            raise PluginInstallError("package_invalid")
        key = path.as_posix().casefold()
        if key in seen:
            raise PluginInstallError("package_invalid")
        seen.add(key)
        total_size += info.file_size
        if info.file_size < 0 or info.file_size > _MAX_ARCHIVE_BYTES:
            raise PluginInstallError("package_invalid")
        if total_size > _MAX_ARCHIVE_BYTES or len(files) >= _MAX_ARCHIVE_FILES:
            raise PluginInstallError("package_invalid")
        files.append(info)

    required = {
        f"{_PLUGIN_NAME}/main.lua",
        f"{_PLUGIN_NAME}/hardcover_config.example.lua",
        f"{_PLUGIN_NAME}/LICENSE",
    }
    if not required.issubset({info.filename for info in files}) or not files:
        raise PluginInstallError("package_invalid")
    return tuple(files)


def _extract_plugin_archive(archive_path: Path, destination: Path) -> Path:
    if destination.exists() or destination.is_symlink() or destination.is_junction():
        raise PluginInstallError("destination_exists")
    entries = _validate_plugin_archive(archive_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink() or destination.parent.is_junction():
        raise PluginInstallError("package_invalid")

    destination.mkdir()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in entries:
                relative = PurePosixPath(info.filename).relative_to(_PLUGIN_NAME)
                target = destination.joinpath(*relative.parts)
                if not target.resolve(strict=False).is_relative_to(
                    destination.resolve(strict=True)
                ):
                    raise PluginInstallError("package_invalid")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=64 * 1024)
    except OSError, RuntimeError, zipfile.BadZipFile:
        remove_tree(destination)
        raise PluginInstallError("package_invalid") from None
    except PluginInstallError:
        remove_tree(destination)
        raise
    return destination


def _load_plugin_patch() -> tuple[dict[str, str], ...]:
    try:
        patch_bytes = PLUGIN_PATCH_FILE.read_bytes()
    except OSError:
        raise PluginInstallError("patch_invalid") from None
    if not PLUGIN_PATCH_SHA256 or hashlib.sha256(patch_bytes).hexdigest() != PLUGIN_PATCH_SHA256:
        raise PluginInstallError("patch_digest_mismatch")
    try:
        patch_data = json.loads(patch_bytes.decode("utf-8"))
    except UnicodeError, json.JSONDecodeError:
        raise PluginInstallError("patch_invalid") from None
    operations = patch_data.get("operations") if isinstance(patch_data, dict) else None
    if (
        not isinstance(patch_data, dict)
        or patch_data.get("schema_version") != 1
        or patch_data.get("upstream_version") != PLUGIN_VERSION
        or not isinstance(operations, list)
        or not operations
        or len(operations) > 32
    ):
        raise PluginInstallError("patch_invalid")

    validated: list[dict[str, str]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise PluginInstallError("patch_invalid")
        relative = operation.get("path")
        old = operation.get("old")
        new = operation.get("new")
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
            or ":" in relative
            or "\x00" in relative
            or PurePosixPath(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in PurePosixPath(relative).parts)
            or not isinstance(old, str)
            or not old
            or not isinstance(new, str)
            or old == new
        ):
            raise PluginInstallError("patch_invalid")
        validated.append({"path": PurePosixPath(relative).as_posix(), "old": old, "new": new})
    return tuple(validated)


def _apply_patch_to_files(
    source_files: dict[str, bytes], operations: tuple[dict[str, str], ...]
) -> dict[str, bytes]:
    updated = dict(source_files)
    for operation in operations:
        relative = operation["path"]
        source = updated.get(relative)
        if source is None:
            raise PluginInstallError("source_changed")
        try:
            source_text = source.decode("utf-8")
        except UnicodeError:
            raise PluginInstallError("source_changed") from None
        if source_text.count(operation["old"]) != 1:
            raise PluginInstallError("source_changed")
        updated[relative] = source_text.replace(operation["old"], operation["new"], 1).encode(
            "utf-8"
        )
    return updated


def apply_hardcover_plugin_patch(plugin_directory: Path) -> None:
    """Apply the digest-pinned lifecycle replacements to the verified release tree."""
    plugin_directory = Path(plugin_directory)
    if (
        not plugin_directory.is_dir()
        or plugin_directory.is_symlink()
        or plugin_directory.is_junction()
    ):
        raise PluginInstallError("package_invalid")
    operations = _load_plugin_patch()
    source_files: dict[str, bytes] = {}
    targets: dict[str, Path] = {}
    for operation in operations:
        relative = operation["path"]
        target = plugin_directory.joinpath(*PurePosixPath(relative).parts)
        if (
            not target.resolve(strict=False).is_relative_to(plugin_directory.resolve(strict=True))
            or target.is_symlink()
            or target.is_junction()
            or not target.is_file()
        ):
            raise PluginInstallError("source_changed")
        try:
            source_files[relative] = target.read_bytes()
        except OSError:
            raise PluginInstallError("source_changed") from None
        targets[relative] = target
    patched = _apply_patch_to_files(source_files, operations)
    for relative, content in patched.items():
        temporary = targets[relative].with_name(f".{targets[relative].name}.patching")
        try:
            with temporary.open("xb") as output:
                output.write(content)
            temporary.replace(targets[relative])
        except OSError:
            temporary.unlink(missing_ok=True)
            raise PluginInstallError("package_invalid") from None


def _patched_archive_files(archive_path: Path) -> dict[str, bytes]:
    entries = _validate_plugin_archive(archive_path)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            source_files = {
                PurePosixPath(info.filename).relative_to(_PLUGIN_NAME).as_posix(): archive.read(
                    info
                )
                for info in entries
            }
    except OSError, RuntimeError, zipfile.BadZipFile:
        raise PluginInstallError("package_invalid") from None
    return _apply_patch_to_files(source_files, _load_plugin_patch())


def _tree_matches_archive(
    plugin_directory: Path, archive_path: Path, *, patched: bool = False
) -> bool:
    if plugin_directory.is_symlink() or plugin_directory.is_junction():
        return False
    try:
        if patched:
            expected = _patched_archive_files(archive_path)
        else:
            entries = _validate_plugin_archive(archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                expected = {
                    PurePosixPath(info.filename).relative_to(_PLUGIN_NAME).as_posix(): archive.read(
                        info
                    )
                    for info in entries
                }
        actual_paths: dict[str, Path] = {}
        for path in plugin_directory.rglob("*"):
            if path.is_symlink() or path.is_junction():
                return False
            if path.is_file():
                actual_paths[path.relative_to(plugin_directory).as_posix()] = path
        if actual_paths.keys() != expected.keys():
            return False
        for relative, content in expected.items():
            digest = hashlib.sha256(actual_paths[relative].read_bytes()).digest()
            if digest != hashlib.sha256(content).digest():
                return False
    except OSError, PluginInstallError, zipfile.BadZipFile:
        return False
    return True


def prepare_hardcover_plugin(state_root: Path, opener: Opener = urllib.request.urlopen) -> Path:
    """Download and extract the official release only after its pinned digest matches."""
    download_root = Path(state_root) / "downloads"
    if download_root.is_symlink() or download_root.is_junction():
        raise PluginInstallError("package_invalid")
    download_root.mkdir(parents=True, exist_ok=True)
    archive_path = download_root / f"hardcoverapp.koplugin-{PLUGIN_VERSION}.zip"

    if not archive_path.exists():
        temporary_path = download_root / f".{uuid.uuid4().hex}.download"
        request = urllib.request.Request(
            PLUGIN_RELEASE_URL,
            headers={"User-Agent": f"koreader-ecosystem/{__version__}"},
        )
        try:
            with opener(request, timeout=60) as response:
                payload = response.read(_MAX_ARCHIVE_BYTES + 1)
            if not payload or len(payload) > _MAX_ARCHIVE_BYTES:
                raise PluginInstallError("package_download_failed")
            temporary_path.write_bytes(payload)
            if hashlib.sha256(payload).hexdigest() != PLUGIN_SHA256:
                raise PluginInstallError("package_digest_mismatch")
            temporary_path.replace(archive_path)
        except PluginInstallError:
            raise
        except Exception:
            raise PluginInstallError("package_download_failed") from None
        finally:
            temporary_path.unlink(missing_ok=True)

    try:
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    except OSError:
        raise PluginInstallError("package_invalid") from None
    if digest != PLUGIN_SHA256:
        raise PluginInstallError("package_digest_mismatch")
    _validate_plugin_archive(archive_path)

    plugin_directory = download_root / f"hardcoverapp.koplugin-{PLUGIN_VERSION}-patched"
    if plugin_directory.exists():
        if not _tree_matches_archive(plugin_directory, archive_path, patched=True):
            raise PluginInstallError("destination_exists")
        return plugin_directory
    plugin_directory = _extract_plugin_archive(archive_path, plugin_directory)
    try:
        apply_hardcover_plugin_patch(plugin_directory)
    except BaseException:
        remove_tree(plugin_directory)
        raise
    return plugin_directory


def _plugin_config(token: str) -> str:
    if (
        not isinstance(token, str)
        or not token
        or not token.isascii()
        or len(token) > 4096
        or token != token.strip()
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in token
        )
    ):
        raise PluginInstallError("authentication_failed")

    escaped: list[str] = []
    for character in token:
        codepoint = ord(character)
        if character in {"\\", '"'}:
            escaped.append("\\" + character)
        elif character == "\n":
            escaped.append("\\n")
        elif character == "\r":
            escaped.append("\\r")
        elif character == "\t":
            escaped.append("\\t")
        elif codepoint < 32 or codepoint == 127:
            escaped.append(f"\\{codepoint:03d}")
        else:
            escaped.append(character)
    return 'return {\n  token = "' + "".join(escaped) + '"\n}\n'


def build_plugin_install_tree(plugin_source: Path, state_root: Path, token: str) -> Path:
    """Build a short-lived local install tree; caller must remove it in a finally block."""
    config = _plugin_config(token)
    plugin_source = Path(plugin_source)
    if not plugin_source.is_dir() or plugin_source.is_symlink() or plugin_source.is_junction():
        raise PluginInstallError("package_invalid")
    staging_parent = Path(state_root) / "staging"
    if staging_parent.is_symlink() or staging_parent.is_junction():
        raise PluginInstallError("package_invalid")
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging_path = staging_parent / f"hardcover-install-{uuid.uuid4().hex}"
    install_tree = staging_path / "source"
    try:
        for source_path in plugin_source.rglob("*"):
            if source_path.is_symlink() or source_path.is_junction():
                raise PluginInstallError("package_invalid")
        shutil.copytree(plugin_source, install_tree)
        config_path = install_tree / "hardcover_config.lua"
        file_descriptor = config_path.open("x", encoding="utf-8", newline="\n")
        with file_descriptor:
            file_descriptor.write(config)
    except BaseException as error:
        try:
            if staging_path.exists():
                remove_tree(staging_path)
        except OSError, SafetyViolation:
            raise PluginInstallError("install_failed") from None
        if isinstance(error, PluginInstallError):
            raise
        if isinstance(error, (OSError, shutil.Error)):
            raise PluginInstallError("package_invalid") from None
        raise
    return install_tree


def _powershell_executable() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        raise PluginInstallError("install_failed")
    return executable


def install_hardcover_plugin(
    source_directory: Path, runner: Runner = run_hidden
) -> PluginInstallResult:
    source_directory = Path(source_directory)
    if not source_directory.is_absolute() or not source_directory.is_dir():
        raise PluginInstallError("package_invalid")
    source_files = tuple(
        path for path in source_directory.rglob("*") if path.is_file() and not path.is_symlink()
    )
    if not source_files:
        raise PluginInstallError("package_invalid")
    expected_count = len(source_files)
    expected_bytes = sum(path.stat().st_size for path in source_files)
    command = [
        _powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(INSTALL_SCRIPT),
        "-SourceDirectory",
        str(source_directory),
    ]
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=1800,
        )
    except OSError, subprocess.SubprocessError:
        raise PluginInstallError("install_failed") from None
    try:
        payload = json.loads(result.stdout)
    except ValueError, TypeError:
        raise PluginInstallError("install_failed") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise PluginInstallError("install_failed")
    if result.returncode != 0 or payload.get("status") != "ok":
        reason = payload.get("reason_code")
        raise PluginInstallError(reason if isinstance(reason, str) else "install_failed")
    file_count = payload.get("file_count")
    total_bytes = payload.get("total_bytes")
    if (
        payload.get("content_verified") is not True
        or isinstance(file_count, bool)
        or not isinstance(file_count, int)
        or isinstance(total_bytes, bool)
        or not isinstance(total_bytes, int)
        or file_count != expected_count
        or total_bytes != expected_bytes
    ):
        raise PluginInstallError("source_changed")
    return PluginInstallResult(file_count=file_count, total_bytes=total_bytes)


def update_hardcover_plugin(
    source_directory: Path,
    rollback_root: Path,
    runner: Runner = run_hidden,
) -> PluginUpdateResult:
    """Update patched plugin files, keeping verified local rollback copies."""
    source_directory = Path(source_directory)
    rollback_root = Path(rollback_root)
    main_lua = source_directory / "main.lua"
    archive_path = source_directory.parent / f"hardcoverapp.koplugin-{PLUGIN_VERSION}.zip"
    if (
        not source_directory.is_absolute()
        or not source_directory.is_dir()
        or source_directory.is_symlink()
        or source_directory.is_junction()
        or not main_lua.is_file()
        or main_lua.is_symlink()
        or not archive_path.is_file()
        or archive_path.is_symlink()
        or not rollback_root.is_absolute()
        or rollback_root.is_symlink()
        or rollback_root.is_junction()
    ):
        raise PluginInstallError("package_invalid")

    try:
        archive_bytes = archive_path.read_bytes()
        if hashlib.sha256(archive_bytes).hexdigest() != PLUGIN_SHA256:
            raise PluginInstallError("package_digest_mismatch")
        if not _tree_matches_archive(source_directory, archive_path, patched=True):
            raise PluginInstallError("package_invalid")
        operations = _load_plugin_patch()
        patched_archive_files = _patched_archive_files(archive_path)
        changed_paths = tuple(dict.fromkeys(operation["path"] for operation in operations))
        if not changed_paths or len(changed_paths) > 32:
            raise PluginInstallError("package_invalid")
        with zipfile.ZipFile(archive_path) as archive:
            entries = _validate_plugin_archive(archive_path)
            archive_files = {
                PurePosixPath(entry.filename).relative_to(_PLUGIN_NAME).as_posix(): entry
                for entry in entries
            }
            manifest: list[dict[str, str]] = []
            patched_total_bytes = 0
            for relative in changed_paths:
                source_path = source_directory.joinpath(*PurePosixPath(relative).parts)
                original_entry = archive_files.get(relative)
                patched_content = patched_archive_files.get(relative)
                if (
                    original_entry is None
                    or patched_content is None
                    or not source_path.is_file()
                    or source_path.is_symlink()
                    or source_path.is_junction()
                ):
                    raise PluginInstallError("package_invalid")
                patched_bytes = source_path.read_bytes()
                if patched_bytes != patched_content or not patched_bytes:
                    raise PluginInstallError("source_changed")
                item = {
                    "path": relative,
                    "original_sha256": hashlib.sha256(archive.read(original_entry)).hexdigest(),
                    "patched_sha256": hashlib.sha256(patched_bytes).hexdigest(),
                }
                previous_hash = _PLUGIN_PREVIOUS_PATCH_SHA256.get(relative)
                if previous_hash is not None:
                    item["legacy_sha256"] = previous_hash
                manifest.append(item)
                patched_total_bytes += len(patched_bytes)
    except PluginInstallError:
        raise
    except OSError, zipfile.BadZipFile:
        raise PluginInstallError("package_invalid") from None

    try:
        rollback_root.mkdir(parents=True, exist_ok=True)
        if rollback_root.is_symlink() or rollback_root.is_junction():
            raise PluginInstallError("package_invalid")
        rollback_directory = rollback_root / f"hardcover-{uuid.uuid4().hex}"
        rollback_directory.mkdir()
    except PluginInstallError:
        raise
    except OSError:
        raise PluginInstallError("package_invalid") from None

    command = [
        _powershell_executable(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(INSTALL_SCRIPT),
        "-Mode",
        "update",
        "-SourceDirectory",
        str(source_directory),
        "-RollbackDirectory",
        str(rollback_directory),
        "-UpdateManifestJson",
        json.dumps(manifest, separators=(",", ":")),
    ]
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=1800,
        )
    except OSError, subprocess.SubprocessError:
        raise PluginInstallError("install_failed") from None
    try:
        payload = json.loads(result.stdout)
    except ValueError, TypeError:
        raise PluginInstallError("install_failed") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise PluginInstallError("install_failed")
    if result.returncode != 0 or payload.get("status") != "ok":
        reason = payload.get("reason_code")
        raise PluginInstallError(reason if isinstance(reason, str) else "install_failed")

    action = payload.get("action")
    rollback_backup_id = payload.get("rollback_backup_id")
    file_count = payload.get("file_count")
    total_bytes = payload.get("total_bytes")
    if (
        payload.get("content_verified") is not True
        or action not in {"updated", "already_current"}
        or isinstance(file_count, bool)
        or not isinstance(file_count, int)
        or file_count != len(manifest)
        or isinstance(total_bytes, bool)
        or not isinstance(total_bytes, int)
        or total_bytes != patched_total_bytes
    ):
        raise PluginInstallError("source_changed")

    backup_hashes = payload.get("rollback_backup_hashes")
    if action == "updated":
        if (
            rollback_backup_id != rollback_directory.name
            or not isinstance(backup_hashes, list)
            or len(backup_hashes) != len(manifest)
        ):
            raise PluginInstallError("source_changed")
        expected_backup_paths = {item["path"] for item in manifest}
        validated_backup_paths: set[str] = set()
        for item in backup_hashes:
            if not isinstance(item, dict):
                raise PluginInstallError("source_changed")
            raw_relative = item.get("path")
            expected_hash = item.get("sha256")
            if (
                not isinstance(raw_relative, str)
                or raw_relative not in expected_backup_paths
                or raw_relative in validated_backup_paths
                or not isinstance(expected_hash, str)
                or len(expected_hash) != 64
                or any(character not in "0123456789abcdef" for character in expected_hash)
            ):
                raise PluginInstallError("source_changed")
            relative = raw_relative
            validated_backup_paths.add(relative)
            backup_file = rollback_directory.joinpath(*PurePosixPath(relative).parts)
            if not backup_file.is_file() or backup_file.is_symlink() or backup_file.is_junction():
                raise PluginInstallError("source_changed")
            try:
                backup_hash = hashlib.sha256(backup_file.read_bytes()).hexdigest()
            except OSError:
                raise PluginInstallError("source_changed") from None
            if backup_hash != expected_hash:
                raise PluginInstallError("source_changed")
        if validated_backup_paths != expected_backup_paths:
            raise PluginInstallError("source_changed") from None
    else:
        if rollback_backup_id is not None or backup_hashes != []:
            raise PluginInstallError("source_changed")
        remove_tree(rollback_directory)

    return PluginUpdateResult(
        file_count=file_count,
        total_bytes=total_bytes,
        updated=action == "updated",
        rollback_backup_id=rollback_backup_id if isinstance(rollback_backup_id, str) else None,
    )


def install_hardcover_with_secret(
    state_root: Path,
    token: str,
    installer: Callable[[Path], PluginInstallResult] = install_hardcover_plugin,
    opener: Opener = urllib.request.urlopen,
    hardcover_client: Any | None = None,
) -> PluginInstallResult:
    from koreader_ecosystem.hardcover import HardcoverClient

    (hardcover_client or HardcoverClient()).verify_token(token)
    plugin_source = prepare_hardcover_plugin(state_root, opener=opener)
    source_directory = build_plugin_install_tree(plugin_source, state_root, token)
    staging_path = source_directory.parent
    try:
        return installer(source_directory)
    finally:
        try:
            remove_tree(staging_path)
        except OSError, SafetyViolation:
            raise PluginInstallError("install_failed") from None
