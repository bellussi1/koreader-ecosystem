import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./app.css";

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("renderer_root_missing");

createRoot(rootElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
