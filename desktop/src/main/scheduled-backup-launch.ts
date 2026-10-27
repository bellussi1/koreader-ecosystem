import path from "node:path";

export interface ScheduledBackupLaunch {
  command: string;
  args: ["--scheduled-backup"];
  cwd: string;
  windowsHide: true;
  shell: false;
  stdio: "ignore";
}

export function scheduledBackupLaunch(
  resourcesPath: string,
  platform: NodeJS.Platform,
): ScheduledBackupLaunch | null {
  if (platform !== "win32") return null;
  return {
    command: path.join(resourcesPath, "sidecar", "KOReaderEcosystem-Service.exe"),
    args: ["--scheduled-backup"],
    cwd: resourcesPath,
    windowsHide: true,
    shell: false,
    stdio: "ignore",
  };
}

export function scheduledBackupTaskLauncher(
  executablePath: string,
  platform: NodeJS.Platform,
): string | null {
  if (platform !== "win32") return null;
  return path.win32.resolve(
    path.win32.dirname(executablePath),
    "..",
    path.win32.basename(executablePath),
  );
}
