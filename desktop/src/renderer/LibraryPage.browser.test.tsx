import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { CalibreBook } from "../shared/contracts";
import { LibraryPage } from "./LibraryPage";
import "./app.css";

afterEach(cleanup);

function book(coverUrl?: string): CalibreBook {
  return {
    book_id: 2,
    uuid: "broken-cover-book",
    title: "Broken Cover Book",
    authors: ["An Author"],
    identifiers: {},
    custom_values: {},
    ...(coverUrl ? { cover_url: coverUrl } : {}),
  };
}

async function expectPlaceholderFitsCard(number: string, title: string) {
  const card = screen.getByRole("listitem", { name: title });
  const placeholder = card.querySelector<HTMLDivElement>(".book-card-cover-placeholder");
  expect(placeholder).toBeTruthy();

  const numberLabel = within(placeholder!).getByText(number);
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

  const cardRect = card.getBoundingClientRect();
  const coverRect = placeholder!.getBoundingClientRect();
  const numberRect = numberLabel.getBoundingClientRect();
  const tolerance = 0.5;

  expect(coverRect.width).toBeGreaterThan(0);
  expect(coverRect.height).toBeGreaterThan(0);
  expect(coverRect.left).toBeGreaterThanOrEqual(cardRect.left - tolerance);
  expect(coverRect.right).toBeLessThanOrEqual(cardRect.right + tolerance);
  expect(coverRect.top).toBeGreaterThanOrEqual(cardRect.top - tolerance);
  expect(coverRect.bottom).toBeLessThanOrEqual(cardRect.bottom + tolerance);
  expect(numberRect.left).toBeGreaterThanOrEqual(coverRect.left - tolerance);
  expect(numberRect.right).toBeLessThanOrEqual(coverRect.right + tolerance);
  expect(numberRect.top).toBeGreaterThanOrEqual(coverRect.top - tolerance);
  expect(numberRect.bottom).toBeLessThanOrEqual(coverRect.bottom + tolerance);
  expect(placeholder!.scrollWidth).toBeLessThanOrEqual(placeholder!.clientWidth);
  expect(placeholder!.scrollHeight).toBeLessThanOrEqual(placeholder!.clientHeight);
}

describe("LibraryPage cover placeholder browser layout", () => {
  for (const layout of ["cards", "shelf"] as const) {
    it(`contains a missing-cover placeholder in ${layout} layout`, async () => {
      render(<LibraryPage language="en" books={[book()]} status="ready" />);
      if (layout === "shelf") {
        fireEvent.click(screen.getByRole("button", { name: /Switch to covers/ }));
      }

      expect(screen.queryByRole("img")).toBeNull();
      await expectPlaceholderFitsCard("002", "Broken Cover Book");
    });

    it(`contains a failed-image placeholder in ${layout} layout`, async () => {
      render(
        <LibraryPage
          language="en"
          books={[book("https://example.com/broken-cover.jpg")]}
          status="ready"
        />,
      );
      if (layout === "shelf") {
        fireEvent.click(screen.getByRole("button", { name: /Switch to covers/ }));
      }

      fireEvent.error(screen.getByRole("img", { name: "Cover: Broken Cover Book" }));
      expect(screen.queryByRole("img")).toBeNull();
      await expectPlaceholderFitsCard("002", "Broken Cover Book");
    });
  }
});
