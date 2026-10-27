import { describe, expect, it } from "vitest";
import path from "node:path";
import { scheduledBackupLaunch, scheduledBackupTaskLauncher } from "./scheduled-backup-launch";

describe("scheduled backup launcher", () => {
  it("uses the packaged sidecar without opening a window or terminal", () => {
    expect(scheduledBackupLaunch("C:/Program Files/KOReader Ecosystem/resources", "win32")).toEqual({
      command: path.join("C:/Program Files/KOReader Ecosystem/resources", "sidecar", "KOReaderEcosystem-Service.exe"),
      args: ["--scheduled-backup"],
      cwd: "C:/Program Files/KOReader Ecosystem/resources",
      windowsHide: true,
      shell: false,
      stdio: "ignore",
    });
  });

  it("does not run a packaged scheduled backup on unsupported platforms", () => {
    expect(scheduledBackupLaunch("/Applications/KOReader Ecosystem", "darwin")).toBeNull();
  });

  it("resolves the stable Squirrel launcher above the versioned app folder", () => {
    expect(scheduledBackupTaskLauncher(
      "C:/Users/Reader/AppData/Local/KOReader Ecosystem/app-0.3.5/KOReaderEcosystem.exe",
      "win32",
    )).toBe("C:\\Users\\Reader\\AppData\\Local\\KOReader Ecosystem\\KOReaderEcosystem.exe");
  });
});
