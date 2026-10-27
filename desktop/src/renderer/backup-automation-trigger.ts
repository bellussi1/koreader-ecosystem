/** Fires once per USB connection, after the device remains present across two polls. */
export class StableDeviceBackupTrigger {
  private connectedChecks = 0;
  private attemptedForConnection = false;

  observe(connected: boolean, enabled: boolean | null): boolean {
    if (!connected) {
      this.connectedChecks = 0;
      this.attemptedForConnection = false;
      return false;
    }

    if (enabled === null) return false;

    if (!enabled) {
      this.connectedChecks = 0;
      this.attemptedForConnection = true;
      return false;
    }

    if (this.attemptedForConnection) return false;

    this.connectedChecks += 1;
    if (this.connectedChecks < 2) return false;

    this.attemptedForConnection = true;
    return true;
  }
}
