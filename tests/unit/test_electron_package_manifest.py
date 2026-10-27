from __future__ import annotations

import json
from pathlib import Path


def test_electron_package_manifest_excludes_user_data_and_contains_sidecar() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    manifest_path = repository_root / "desktop" / "package-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["icon"] == "../assets/KOReaderEcosystem.ico"
    resources = {
        (resource["source"], resource["destination"]) for resource in manifest["resources"]
    }
    assert ("../build/electron-sidecar/sidecar", "sidecar") in resources
    assert ("../assets/KOReaderEcosystem.ico", "KOReaderEcosystem.ico") in resources
    excluded = set(manifest["excludedUserDataPatterns"])
    assert {
        "../Biblioteca do calibre/**",
        "../Calibre Library/**",
        "**/metadata.db*",
        "**/*.sdr/**",
        "**/*.epub",
        "**/*.azw*",
        "**/*.mobi",
        "**/.env*",
        "**/*credentials*",
        "../staging/**",
        "../restore-validation/**",
    } <= excluded

    forge_config = (repository_root / "desktop" / "forge.config.ts").read_text(encoding="utf-8")
    assert "extraResource: packageManifest.resources.map" in forge_config
    assert "icon = path.resolve(projectRoot, packageManifest.icon)" in forge_config
    assert 'resource.destination === "sidecar"' in forge_config
    assert "ignore: isPackageUserData" in forge_config
    assert 'exe: "KOReaderEcosystem.exe"' in forge_config
    assert 'setupExe: "KOReaderEcosystem-Setup.exe"' in forge_config
    assert "setupIcon: icon" in forge_config

    renderer = (repository_root / "desktop" / "src" / "renderer" / "App.tsx").read_text(
        encoding="utf-8"
    )
    renderer_config = (repository_root / "desktop" / "vite.renderer.config.ts").read_text(
        encoding="utf-8"
    )
    assert "__APP_VERSION__" in renderer
    assert "__APP_VERSION__" in renderer_config
    assert "packageManifest.version" in renderer_config

    main_process = (repository_root / "desktop" / "src" / "main" / "index.ts").read_text(
        encoding="utf-8"
    )
    assert 'app.setAppUserModelId("com.squirrel.koreader-ecosystem.KOReaderEcosystem")' in (
        main_process
    )
    assert "--createShortcut" in main_process
    assert "--removeShortcut" in main_process
    assert 'path.join(process.resourcesPath, "KOReaderEcosystem.ico")' in main_process
    assert '"--icon"' in main_process
    assert "setTimeout(app.quit, 1000)" in main_process


def test_sidecar_package_uses_nuitka_pefile_dependency_scanner() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    package_script = (repository_root / "desktop" / "scripts" / "package-sidecar.ps1").read_text(
        encoding="utf-8"
    )

    assert '"--experimental=force-dependencies-pefile"' in package_script
