import React from "react";
import { createRoot } from "react-dom/client";
import type { CalibreBook, HardcoverBook, ReadingApi } from "./src/shared/contracts";
import { App } from "./src/renderer/App";
import "./src/renderer/app.css";

function cover(title: string, background: string, accent: string): string {
  const words = title.split(" ");
  const lines: string[] = [];
  let line = "";
  for (const word of words) {
    if (`${line} ${word}`.trim().length > 16 && line) {
      lines.push(line);
      line = word;
    } else {
      line = `${line} ${word}`.trim();
    }
  }
  if (line) lines.push(line);
  const titleLines = lines.map((text, index) =>
    `<text x="28" y="${244 + index * 46}" fill="#fff9ed" font-family="Georgia,serif" font-size="30" letter-spacing=".4">${text}</text>`,
  ).join("");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="520" viewBox="0 0 360 520"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="${background}"/><stop offset="1" stop-color="#111827"/></linearGradient></defs><rect width="360" height="520" fill="url(#g)"/><circle cx="257" cy="154" r="94" fill="${accent}" opacity=".7"/><path d="M0 374 170 176 360 368v152H0Z" fill="#0a1420" opacity=".76"/><path d="M12 18h336v484H12Z" fill="none" stroke="#f5ead4" stroke-opacity=".35" stroke-width="2"/><text x="28" y="202" fill="#f0b58e" font-family="Arial,sans-serif" font-size="12" letter-spacing="3">A FICTIONAL NOVEL</text>${titleLines}<text x="28" y="474" fill="#d7d5ce" font-family="Arial,sans-serif" font-size="12" letter-spacing="2">KOReader ECOSYSTEM DEMO</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

const covers = [
  cover("The Glass Meridian", "#34445c", "#b98468"),
  cover("A Map of Borrowed Stars", "#1d5271", "#e6a351"),
  cover("The Orchard of Quiet Machines", "#626a53", "#c9925f"),
  cover("Northbound Archive", "#583d56", "#b9a0d0"),
  cover("Tide of Paper Moons", "#265260", "#91c4bf"),
  cover("The Last Lantern Keeper", "#6a4439", "#f0b56d"),
];

const books: CalibreBook[] = [
  { book_id: 301, uuid: "demo-301", title: "The Glass Meridian", authors: ["M. Arden"], identifiers: { isbn: "9780000000301" }, custom_values: {}, cover_url: covers[0], series: "The Meridian Archive", series_index: 1, publisher: "Northstar Press", pubdate: "2025-03-14", languages: ["eng"], tags: ["Science Fiction", "Adventure"], rating: 8, formats: ["EPUB"], size: 1258291 },
  { book_id: 302, uuid: "demo-302", title: "A Map of Borrowed Stars", authors: ["I. Maren"], identifiers: { isbn: "9780000000302" }, custom_values: {}, cover_url: covers[1], series: "The Meridian Archive", series_index: 2, publisher: "Northstar Press", pubdate: "2025-09-02", languages: ["eng"], tags: ["Science Fiction", "Found Family"], rating: 9, formats: ["EPUB"], size: 1481020 },
  { book_id: 303, uuid: "demo-303", title: "The Orchard of Quiet Machines", authors: ["Jo Sen"], identifiers: { isbn: "9780000000303" }, custom_values: {}, cover_url: covers[2], series: null, publisher: "Fern & Finch", pubdate: "2024-06-21", languages: ["por"], tags: ["Ficção científica", "Literatura"], rating: 7, formats: ["EPUB"], size: 940201 },
  { book_id: 304, uuid: "demo-304", title: "Northbound Archive", authors: ["L. Varela"], identifiers: { isbn: "9780000000304" }, custom_values: {}, cover_url: covers[3], series: "Atlas of Elsewhere", series_index: 1, publisher: "Blue Harbor", pubdate: "2023-11-05", languages: ["eng", "por"], tags: ["Fantasy", "Mystery"], rating: 10, formats: ["EPUB"], size: 1675400 },
  { book_id: 305, uuid: "demo-305", title: "Tide of Paper Moons", authors: ["A. Nadir"], identifiers: { isbn: "9780000000305" }, custom_values: {}, cover_url: covers[4], series: null, publisher: "Fern & Finch", pubdate: "2022-08-19", languages: ["por"], tags: ["Fantasia", "Aventura"], rating: 8, formats: ["EPUB"], size: 1038400 },
  { book_id: 306, uuid: "demo-306", title: "The Last Lantern Keeper", authors: ["R. Sol"], identifiers: { isbn: "9780000000306" }, custom_values: {}, cover_url: covers[5], series: "Lantern Coast", series_index: 3, publisher: "Blue Harbor", pubdate: "2021-02-11", languages: ["eng"], tags: ["Fantasy", "Family"], rating: 9, formats: ["EPUB"], size: 1162400 },
];

const hardcoverBooks: HardcoverBook[] = [
  { user_book_id: 501, book_id: 401, title: "The Glass Meridian", authors: ["M. Arden"], status_id: 2, rating: null, edition_id: 4101, edition_pages: 352, reads: [{ id: 1, started_at: "2026-10-08", finished_at: null, progress_pages: 156, edition_id: 4101 }], cover_url: covers[0], edition_format: "eBook" },
  { user_book_id: 502, book_id: 402, title: "A Map of Borrowed Stars", authors: ["I. Maren"], status_id: 1, rating: null, edition_id: 4102, edition_pages: 416, reads: [], cover_url: covers[1], edition_format: "eBook" },
  { user_book_id: 503, book_id: 403, title: "The Orchard of Quiet Machines", authors: ["Jo Sen"], status_id: 3, rating: 4.5, edition_id: 4103, edition_pages: 288, reads: [{ id: 2, started_at: "2026-06-02", finished_at: "2026-06-14", progress_pages: 288, edition_id: 4103 }], cover_url: covers[2], edition_format: "eBook" },
  { user_book_id: 504, book_id: 404, title: "Northbound Archive", authors: ["L. Varela"], status_id: 1, rating: null, edition_id: 4104, edition_pages: 304, reads: [], cover_url: covers[3], edition_format: "eBook" },
];

const demoBridge = {
  app: { status: async () => ({ status: "ready", protocol_version: 1 }) },
  calibre: { list: async () => ({ books, total: books.length }) },
  kindle: { status: async () => ({ connected: true, reason_code: "ready" }) },
  hardcover: {
    status: async () => ({ configured: true, requests_used: 312, request_limit: 1000, requests_remaining: 688, window_seconds: 86400 }),
    list: async () => ({ books: hardcoverBooks, has_more: false }),
  },
  backup: {
    credentials: async () => ({ password_configured: true }),
    list: async () => [
      { id: "demo-snapshot-01", time: "2026-10-24T18:30:00Z", scope: "calibre_only", includes_app_state: true },
      { id: "demo-snapshot-02", time: "2026-10-22T12:10:00Z", scope: "calibre_and_kindle", includes_app_state: true },
    ],
  },
} as unknown as ReadingApi;

Object.defineProperty(window, "reading", { configurable: true, value: demoBridge });
const root = document.getElementById("root");
if (!root) throw new Error("demo_root_missing");
createRoot(root).render(<App />);

const label = document.createElement("div");
label.textContent = "DEMO · DADOS FICTÍCIOS";
label.style.cssText = "position:fixed;right:10px;bottom:8px;z-index:200;color:#a9b8c9;font:600 10px/1.2 Inter,Arial,sans-serif;letter-spacing:.08em;pointer-events:none;opacity:.76";
document.body.append(label);
