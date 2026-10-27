from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


class PortableBundleError(RuntimeError):
    """Raised when a portable application package is incomplete or contains user data."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"Portable application package is invalid ({reason_code})")


@dataclass(frozen=True, slots=True)
class PortableBundleReport:
    executable: Path
    runtime_files: tuple[str, ...]


_RUNTIME_ASSET_SOURCE_PARTS = {
    "restic.exe": None,
    "stage-kindle.ps1": ("scripts", "stage-kindle.ps1"),
    "WpdReadback.cs": ("scripts", "WpdReadback.cs"),
    "send-kindle.ps1": ("scripts", "send-kindle.ps1"),
    "install-hardcover-plugin.ps1": ("scripts", "install-hardcover-plugin.ps1"),
    "hardcoverapp-0.4.0.patch": (
        "patches",
        "hardcoverapp-koplugin",
        "0.4.0.patch",
    ),
}
_APPLICATION_ASSET_SOURCE_PARTS = {
    "KOReaderEcosystem.ico": ("assets", "KOReaderEcosystem.ico"),
    "Inter.ttf": ("assets", "fonts", "Inter.ttf"),
    "Lora.ttf": ("assets", "fonts", "Lora.ttf"),
}
_REQUIRED_FILES = (
    Path("KOReaderEcosystem.exe"),
    Path("assets/KOReaderEcosystem.ico"),
    Path("assets/fonts/Inter.ttf"),
    Path("assets/fonts/Lora.ttf"),
    Path("LEIA-ME-EXECUTAVEL.txt"),
    Path("runtime/restic.exe"),
    Path("runtime/stage-kindle.ps1"),
    Path("runtime/WpdReadback.cs"),
    Path("runtime/send-kindle.ps1"),
    Path("runtime/install-hardcover-plugin.ps1"),
    Path("runtime/hardcoverapp-0.4.0.patch"),
    Path("licenses/LGPL-3.0.txt"),
    Path("licenses/PYTHON-LICENSE.txt"),
    Path("licenses/RESTIC-LICENSE.txt"),
    Path("licenses/THIRD-PARTY-NOTICES.txt"),
    Path("licenses/fonts/Inter-OFL.txt"),
    Path("licenses/fonts/Lora-OFL.txt"),
)
_REQUIRED_RUNTIME_DEPENDENCIES = (
    "python314.dll",
    "qt6core.dll",
    "qt6gui.dll",
    "qt6network.dll",
    "qt6widgets.dll",
    "PySide6/qt-plugins/platforms/qwindows.dll",
)
_PRIVATE_FILENAMES = frozenset({"metadata.db", "config.json", ".env"})
_BOOK_SUFFIXES = frozenset({".epub", ".mobi", ".azw", ".azw3", ".kfx"})


def _application_directory() -> Path:
    executable = Path(sys.argv[0])
    if executable.is_absolute() or executable.suffix.casefold() == ".exe":
        return executable.resolve(strict=False).parent
    return Path(__file__).resolve().parents[2]


def _source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_runtime_asset(name: str) -> Path:
    """Resolve a bundled runtime file, falling back to source scripts during development."""
    if name not in _RUNTIME_ASSET_SOURCE_PARTS:
        raise PortableBundleError("runtime_asset_invalid")

    packaged = _application_directory() / "runtime" / name
    if packaged.is_file():
        return packaged
    source_parts = _RUNTIME_ASSET_SOURCE_PARTS[name]
    if source_parts is not None:
        source = _source_root().joinpath(*source_parts)
        if source.is_file():
            return source
    return packaged


def resolve_application_asset(name: str) -> Path:
    """Resolve an application asset from a portable bundle or the source checkout."""
    source_parts = _APPLICATION_ASSET_SOURCE_PARTS.get(name)
    if source_parts is None:
        raise PortableBundleError("application_asset_invalid")

    packaged = _application_directory().joinpath(*source_parts)
    if packaged.is_file():
        return packaged
    source = _source_root().joinpath(*source_parts)
    if source.is_file():
        return source
    return packaged


def validate_portable_bundle(root: Path) -> PortableBundleReport:
    """Check the portable directory before it is released or handed to the user."""
    root = Path(root)
    if not root.is_dir():
        raise PortableBundleError("bundle_missing")

    for relative in _REQUIRED_FILES:
        target = root / relative
        if not target.is_file() or target.stat().st_size == 0:
            raise PortableBundleError("runtime_missing")

    for runtime_dependency in _REQUIRED_RUNTIME_DEPENDENCIES:
        if not (root / runtime_dependency).is_file():
            raise PortableBundleError("runtime_missing")

    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part.casefold() == ".sdr" for part in relative.parts):
            raise PortableBundleError("private_data_in_bundle")
        if path.is_file():
            if path.name.casefold() in _PRIVATE_FILENAMES:
                raise PortableBundleError("private_data_in_bundle")
            if path.suffix.casefold() in _BOOK_SUFFIXES:
                raise PortableBundleError("private_data_in_bundle")
            if path.name.casefold().startswith(".env."):
                raise PortableBundleError("private_data_in_bundle")

    return PortableBundleReport(
        executable=root / _REQUIRED_FILES[0],
        runtime_files=(
            "restic.exe",
            "stage-kindle.ps1",
            "WpdReadback.cs",
            "send-kindle.ps1",
            "install-hardcover-plugin.ps1",
            "hardcoverapp-0.4.0.patch",
        ),
    )
