from __future__ import annotations

from pathlib import Path

import pytest

from koreader_ecosystem.portable_bundle import (
    PortableBundleError,
    resolve_application_asset,
    resolve_runtime_asset,
    validate_portable_bundle,
)


def _bundle(root: Path) -> Path:
    runtime = root / "runtime"
    licenses = root / "licenses"
    font_licenses = licenses / "fonts"
    qt_plugins = root / "PySide6" / "qt-plugins" / "platforms"
    runtime.mkdir(parents=True)
    font_licenses.mkdir(parents=True)
    qt_plugins.mkdir(parents=True)
    (root / "KOReaderEcosystem.exe").write_bytes(b"app")
    icon_directory = root / "assets"
    icon_directory.mkdir()
    (icon_directory / "KOReaderEcosystem.ico").write_bytes(b"icon")
    font_directory = icon_directory / "fonts"
    font_directory.mkdir()
    (font_directory / "Inter.ttf").write_bytes(b"inter")
    (font_directory / "Lora.ttf").write_bytes(b"lora")
    (root / "LEIA-ME-EXECUTAVEL.txt").write_text("Guia de início", encoding="utf-8")
    for filename in (
        "python314.dll",
        "qt6core.dll",
        "qt6gui.dll",
        "qt6network.dll",
        "qt6widgets.dll",
    ):
        (root / filename).write_bytes(b"runtime")
    (qt_plugins / "qwindows.dll").write_bytes(b"platform")
    (runtime / "restic.exe").write_bytes(b"restic")
    (runtime / "stage-kindle.ps1").write_text("# adapter", encoding="utf-8")
    (runtime / "WpdReadback.cs").write_text("// read-only WPD verifier", encoding="utf-8")
    (runtime / "send-kindle.ps1").write_text("# sender", encoding="utf-8")
    (runtime / "install-hardcover-plugin.ps1").write_text("# installer", encoding="utf-8")
    (runtime / "hardcoverapp-0.4.0.patch").write_text("{}", encoding="utf-8")
    (licenses / "LGPL-3.0.txt").write_text("LGPL-3.0", encoding="utf-8")
    (licenses / "PYTHON-LICENSE.txt").write_text("PSF license", encoding="utf-8")
    (licenses / "RESTIC-LICENSE.txt").write_text("BSD-2-Clause", encoding="utf-8")
    (licenses / "THIRD-PARTY-NOTICES.txt").write_text("Notices", encoding="utf-8")
    (font_licenses / "Inter-OFL.txt").write_text("Inter OFL", encoding="utf-8")
    (font_licenses / "Lora-OFL.txt").write_text("Lora OFL", encoding="utf-8")
    return root


def test_validates_portable_bundle_with_required_runtime_and_notices(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "KOReaderEcosystem")

    result = validate_portable_bundle(bundle)

    assert result.executable == bundle / "KOReaderEcosystem.exe"
    assert (bundle / "assets" / "KOReaderEcosystem.ico").is_file()
    assert result.runtime_files == (
        "restic.exe",
        "stage-kindle.ps1",
        "WpdReadback.cs",
        "send-kindle.ps1",
        "install-hardcover-plugin.ps1",
        "hardcoverapp-0.4.0.patch",
    )


def test_rejects_bundle_missing_runtime_binary(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "KOReaderEcosystem")
    (bundle / "runtime" / "restic.exe").unlink()

    with pytest.raises(PortableBundleError, match="runtime_missing"):
        validate_portable_bundle(bundle)


def test_rejects_bundle_missing_qt_network_runtime(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "KOReaderEcosystem")
    (bundle / "qt6network.dll").unlink()

    with pytest.raises(PortableBundleError, match="runtime_missing"):
        validate_portable_bundle(bundle)


def test_rejects_bundle_missing_quick_start_guide(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "KOReaderEcosystem")
    (bundle / "LEIA-ME-EXECUTAVEL.txt").unlink()

    with pytest.raises(PortableBundleError, match="runtime_missing"):
        validate_portable_bundle(bundle)


def test_rejects_bundle_missing_application_icon(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "KOReaderEcosystem")
    (bundle / "assets" / "KOReaderEcosystem.ico").unlink()

    with pytest.raises(PortableBundleError, match="runtime_missing"):
        validate_portable_bundle(bundle)


def test_rejects_bundle_missing_editorial_font(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "KOReaderEcosystem")
    (bundle / "assets" / "fonts" / "Lora.ttf").unlink()

    with pytest.raises(PortableBundleError, match="runtime_missing"):
        validate_portable_bundle(bundle)


@pytest.mark.parametrize("filename", ["metadata.db", "book.epub", "config.json", ".env"])
def test_rejects_personal_data_or_configuration_in_bundle(tmp_path: Path, filename: str) -> None:
    bundle = _bundle(tmp_path / "KOReaderEcosystem")
    (bundle / filename).write_text("private", encoding="utf-8")

    with pytest.raises(PortableBundleError, match="private_data_in_bundle"):
        validate_portable_bundle(bundle)


@pytest.mark.parametrize(
    "asset_name",
    [
        "stage-kindle.ps1",
        "WpdReadback.cs",
        "install-hardcover-plugin.ps1",
        "hardcoverapp-0.4.0.patch",
    ],
)
def test_runtime_asset_prefers_packaged_file_and_has_dev_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, asset_name: str
) -> None:
    import koreader_ecosystem.portable_bundle as portable_bundle

    package_root = tmp_path / "package"
    source_root = tmp_path / "source"
    (package_root / "runtime").mkdir(parents=True)
    (source_root / "scripts").mkdir(parents=True)
    packaged = package_root / "runtime" / asset_name
    source_parent = (
        source_root / "scripts"
        if asset_name.endswith((".ps1", ".cs"))
        else source_root / "patches" / "hardcoverapp-koplugin"
    )
    source_filename = "0.4.0.patch" if asset_name == "hardcoverapp-0.4.0.patch" else asset_name
    source = source_parent / source_filename
    source_parent.mkdir(parents=True, exist_ok=True)
    packaged.write_text("packaged", encoding="utf-8")
    source.write_text("source", encoding="utf-8")
    monkeypatch.setattr(portable_bundle, "_application_directory", lambda: package_root)
    monkeypatch.setattr(portable_bundle, "_source_root", lambda: source_root)

    assert resolve_runtime_asset(asset_name) == packaged

    packaged.unlink()
    assert resolve_runtime_asset(asset_name) == source


def test_runtime_asset_rejects_unlisted_names() -> None:
    with pytest.raises(PortableBundleError, match="runtime_asset_invalid"):
        resolve_runtime_asset("../../config.json")


@pytest.mark.parametrize("asset_name", ["Inter.ttf", "Lora.ttf"])
def test_application_font_prefers_packaged_file_and_has_dev_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, asset_name: str
) -> None:
    import koreader_ecosystem.portable_bundle as portable_bundle

    package_root = tmp_path / "package"
    source_root = tmp_path / "source"
    (package_root / "assets" / "fonts").mkdir(parents=True)
    (source_root / "assets" / "fonts").mkdir(parents=True)
    packaged = package_root / "assets" / "fonts" / asset_name
    source = source_root / "assets" / "fonts" / asset_name
    packaged.write_bytes(b"packaged")
    source.write_bytes(b"source")
    monkeypatch.setattr(portable_bundle, "_application_directory", lambda: package_root)
    monkeypatch.setattr(portable_bundle, "_source_root", lambda: source_root)

    assert resolve_application_asset(asset_name) == packaged

    packaged.unlink()
    assert resolve_application_asset(asset_name) == source


def test_restic_searches_packaged_runtime_before_system_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import koreader_ecosystem.restic as restic

    bundled = tmp_path / "runtime" / "restic.exe"
    bundled.parent.mkdir()
    bundled.write_bytes(b"restic")
    monkeypatch.setattr(restic, "resolve_runtime_asset", lambda _: bundled)
    monkeypatch.setattr(restic.shutil, "which", lambda _: "system-restic")

    assert restic._find_executable() == str(bundled)


def test_mtp_uses_the_resolved_runtime_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    import koreader_ecosystem.mtp as mtp

    script = tmp_path / "runtime" / "stage-kindle.ps1"
    script.parent.mkdir()
    script.write_text("# packaged", encoding="utf-8")
    command_seen: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        command_seen.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            '{"schema_version":4,"mode":"inventory","status":"ok",'
            '"roots":[],"file_count":0,"directory_count":0,"total_bytes":0,'
            '"device_count":1,"storage_count":1}',
            "",
        )

    monkeypatch.setattr(mtp, "resolve_runtime_asset", lambda _: script)
    monkeypatch.setattr(mtp, "_powershell_executable", lambda: "powershell.exe")

    mtp.detect_kindle(runner)

    assert str(script) in command_seen[0]
