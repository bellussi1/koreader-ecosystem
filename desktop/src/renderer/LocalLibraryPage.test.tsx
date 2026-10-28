// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LocalLibraryPage } from "./LocalLibraryPage";

afterEach(cleanup);

it("adds a local book and saves reading details without a connected service", async () => {
  const book = { book_id: "a".repeat(32), title: "Fixture", author: "Writer", status: "to_read" as const, progress: null, rating: null, started_on: null, finished_on: null, notes: "" };
  const api = { list: vi.fn().mockResolvedValue([]), get: vi.fn().mockResolvedValue(book), add: vi.fn().mockResolvedValue(book), save: vi.fn().mockImplementation(async (value) => value) };
  render(<LocalLibraryPage language="en" api={api} />);
  await screen.findByText("No local books yet.");
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Fixture" } });
  fireEvent.change(screen.getByLabelText("Author"), { target: { value: "Writer" } });
  fireEvent.click(screen.getByRole("button", { name: "Add book" }));
  await waitFor(() => expect(api.add).toHaveBeenCalledWith("Fixture", "Writer"));
  fireEvent.click(await screen.findByRole("button", { name: "Fixture" }));
  await screen.findByLabelText("Status");
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "reading" } });
  fireEvent.change(screen.getByLabelText("Progress (%)"), { target: { value: "42" } });
  fireEvent.change(screen.getByLabelText("Notes"), { target: { value: "Private note" } });
  fireEvent.click(screen.getByRole("button", { name: "Save reading" }));
  await waitFor(() => expect(api.save).toHaveBeenCalledWith(expect.objectContaining({status: "reading", progress: 42, notes: "Private note"})));
});

it("does not allow adding books before the initial list finishes", async () => {
  let finish!: (value: never[]) => void;
  const loading = new Promise<never[]>((resolve) => { finish = resolve; });
  const api = { list: vi.fn().mockReturnValue(loading), get: vi.fn(), add: vi.fn(), save: vi.fn() };
  render(<LocalLibraryPage language="en" api={api} />);
  expect(screen.getByRole("button", { name: "Add book" }).matches(":disabled")).toBe(true);
  await act(async () => finish([]));
  expect(screen.getByRole("button", { name: "Add book" }).matches(":disabled")).toBe(false);
});

it("locks edits and selection while a save is pending", async () => {
  const book = { book_id: "a".repeat(32), title: "Fixture", author: "Writer", status: "reading" as const, progress: null, rating: null, started_on: null, finished_on: null, notes: "Original" };
  let finish!: (value: typeof book) => void;
  const saving = new Promise<typeof book>((resolve) => { finish = resolve; });
  const api = { list: vi.fn().mockResolvedValue([{ ...book, notes: "" }]), get: vi.fn().mockResolvedValue(book), add: vi.fn(), save: vi.fn().mockReturnValue(saving) };
  render(<LocalLibraryPage language="en" api={api} />);
  fireEvent.click(await screen.findByRole("button", { name: "Fixture" }));
  const notes = await screen.findByLabelText("Notes");
  expect((notes as HTMLTextAreaElement).value).toBe("Original");
  fireEvent.change(notes, { target: { value: "Updated" } });
  fireEvent.click(screen.getByRole("button", { name: "Save reading" }));
  expect(notes.matches(":disabled")).toBe(true);
  expect((screen.getByRole("button", { name: "Fixture" }) as HTMLButtonElement).disabled).toBe(true);
  await act(async () => finish({ ...book, notes: "Updated" }));
  expect((notes as HTMLTextAreaElement).value).toBe("Updated");
});
