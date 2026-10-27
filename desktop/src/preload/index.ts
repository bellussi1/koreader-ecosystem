import { contextBridge, ipcRenderer } from "electron";
import { createReadingApi } from "../shared/contracts";

contextBridge.exposeInMainWorld(
  "reading",
  createReadingApi({
    invoke: (channel, payload) => ipcRenderer.invoke(channel, payload),
  }),
);
