import { describe, expect, it } from "vitest";
import { StableDeviceBackupTrigger } from "./backup-automation-trigger";

describe("stable device backup trigger", () => {
  it("waits for two connected checks after observing a disconnect", () => {
    const trigger = new StableDeviceBackupTrigger();

    expect(trigger.observe(true, true)).toBe(false);
    expect(trigger.observe(false, true)).toBe(false);
    expect(trigger.observe(true, true)).toBe(false);
    expect(trigger.observe(true, true)).toBe(true);
  });

  it("triggers after two stable checks when the app starts with the device connected", () => {
    const trigger = new StableDeviceBackupTrigger();

    expect(trigger.observe(true, true)).toBe(false);
    expect(trigger.observe(true, true)).toBe(true);
    expect(trigger.observe(true, true)).toBe(false);
  });

  it("waits for automation settings before deciding whether a connected device is eligible", () => {
    const trigger = new StableDeviceBackupTrigger();

    expect(trigger.observe(true, null)).toBe(false);
    expect(trigger.observe(true, true)).toBe(false);
    expect(trigger.observe(true, true)).toBe(true);
  });

  it("requires a new connection after an automatic backup is disabled or attempted", () => {
    const trigger = new StableDeviceBackupTrigger();

    expect(trigger.observe(false, false)).toBe(false);
    expect(trigger.observe(true, false)).toBe(false);
    expect(trigger.observe(true, true)).toBe(false);
    expect(trigger.observe(false, true)).toBe(false);
    expect(trigger.observe(true, true)).toBe(false);
    expect(trigger.observe(true, true)).toBe(true);
    expect(trigger.observe(true, true)).toBe(false);
  });
});
