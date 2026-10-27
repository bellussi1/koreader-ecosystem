import MakerSquirrel from "@electron-forge/maker-squirrel";
import MakerZIP from "@electron-forge/maker-zip";
import VitePlugin from "@electron-forge/plugin-vite";
import type { ForgeConfig } from "@electron-forge/shared-types";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.dirname(fileURLToPath(import.meta.url));
const appVersion = (
  JSON.parse(readFileSync(path.resolve(projectRoot, "package.json"), "utf8")) as {
    version: string;
  }
).version;
const packageManifest = JSON.parse(
  readFileSync(path.resolve(projectRoot, "package-manifest.json"), "utf8"),
) as {
  icon: string;
  resources: Array<{ source: string; destination: string }>;
  excludedUserDataPatterns: string[];
};
const icon = path.resolve(projectRoot, packageManifest.icon);
const packageResource = packageManifest.resources.find(
  (resource) => resource.destination === "sidecar",
);
const packageIconResource = packageManifest.resources.find(
  (resource) => resource.destination === "KOReaderEcosystem.ico",
);
const requiredLicenseResources = [
  "LICENSE.txt",
  "THIRD-PARTY-NOTICES.txt",
  "LGPL-3.0.txt",
  "RESTIC-LICENSE.txt",
  "HARDCOVER-KOReader-PLUGIN-MIT.txt",
  "Inter-OFL.txt",
  "Lora-OFL.txt",
];
const requiredResourcesPresent = requiredLicenseResources.every((destination) =>
  packageManifest.resources.some((resource) => resource.destination === destination),
);
if (
  !packageResource ||
  !packageIconResource ||
  !requiredResourcesPresent ||
  path.basename(path.resolve(projectRoot, packageResource.source)) !== packageResource.destination ||
  path.basename(path.resolve(projectRoot, packageIconResource.source)) !==
    packageIconResource.destination
) {
  throw new Error("Desktop package manifest must contain the sidecar and window icon resources.");
}

const excludedDirectoryNames = new Set([
  "biblioteca do calibre",
  "calibre library",
  "staging",
  "backups",
  "restore-validation",
  "private",
]);

function isPackageUserData(candidate: string): boolean {
  const normalizedInput = candidate.replaceAll("\\", "/").replace(/^\/+/, "");
  if (normalizedInput === "out" || normalizedInput.startsWith("out/")) {
    return true;
  }
  const resolvedCandidate = path.resolve(candidate);
  const relativeCandidate = path.relative(projectRoot, resolvedCandidate).replaceAll("\\", "/");
  if (relativeCandidate === "out" || relativeCandidate.startsWith("out/")) {
    return true;
  }
  const normalized = resolvedCandidate.replaceAll("\\", "/");
  const segments = normalized.split("/");
  const filename = segments.at(-1)?.toLocaleLowerCase("en-US") ?? "";
  if (segments.some((segment) => excludedDirectoryNames.has(segment.toLocaleLowerCase("en-US")))) {
    return true;
  }
  if (
    segments.some((segment) =>
      /^(?:metadata\.db(?:-.*)?|[^/]+\.sdr|\.env(?:\..*)?|.*credentials.*|hardcover_config\.lua)$/i.test(segment),
    )
  ) {
    return true;
  }
  return /\.(?:epub|azw\d*|mobi|pdf)$/i.test(filename);
}

const config: ForgeConfig = {
  buildIdentifier: `v${appVersion}-release`,
  packagerConfig: {
    asar: true,
    appBundleId: "local.koreader.ecosystem",
    executableName: "KOReaderEcosystem",
    icon,
    extraResource: packageManifest.resources.map((resource) =>
      path.resolve(projectRoot, resource.source),
    ),
    ignore: isPackageUserData,
  },
  makers: [
    new MakerSquirrel({
      name: "koreader-ecosystem",
      authors: "KOReader Ecosystem contributors",
      description: "Biblioteca local, Calibre e sincronização KOReader",
      exe: "KOReaderEcosystem.exe",
      setupExe: "KOReaderEcosystem-Setup.exe",
      setupIcon: icon,
      noMsi: true,
    }),
    new MakerZIP({}),
  ],
  plugins: [
    new VitePlugin({
      hotRestart: true,
      concurrent: false,
      build: [
        {
          entry: "src/main/index.ts",
          config: "vite.main.config.ts",
          target: "main",
        },
        {
          entry: "src/preload/index.ts",
          config: "vite.preload.config.ts",
          target: "preload",
        },
      ],
      renderer: [
        {
          name: "main_window",
          config: "vite.renderer.config.ts",
        },
      ],
    }),
  ],
};

export default config;
