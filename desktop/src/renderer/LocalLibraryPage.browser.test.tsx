import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LocalLibraryPage } from "./LocalLibraryPage";
import "./app.css";

afterEach(cleanup);

it("keeps the local library form inside a narrow viewport", async () => {
  const api = { list: vi.fn().mockResolvedValue([]), get: vi.fn(), add: vi.fn(), save: vi.fn() };
  render(<LocalLibraryPage language="pt-BR" api={api} />);
  expect(screen.getByRole("heading", { name: "Biblioteca de leitura local" })).toBeTruthy();
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(document.documentElement.clientWidth);
});
