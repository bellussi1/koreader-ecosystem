// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CalibreBook } from "../shared/contracts";
import { LibraryPage } from "./LibraryPage";
import "./app.css";

class TestResizeObserver implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function book(bookId: number, title: string, authors: string[]): CalibreBook {
  return {
    book_id: bookId,
    uuid: `book-${bookId}`,
    title,
    authors,
    identifiers: {},
    custom_values: {},
  };
}

describe("LibraryPage", () => {
  it("localizes the main library controls in English", () => {
    render(<LibraryPage language="en" books={[]} status="ready" />);

    expect(screen.getByRole("heading", { name: "Library" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Want to read/ })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Sort shelf" })).toBeTruthy();
    expect(screen.getByRole("searchbox", { name: "Search this shelf" })).toBeTruthy();
    expect(screen.getByText("No books in the Calibre library")).toBeTruthy();
  });

  it("localizes the Calibre metadata editor in English", () => {
    render(<LibraryPage language="en" books={[book(4, "Sample Book", ["An Author"])]} status="ready" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit metadata: Sample Book" }));

    expect(screen.getByRole("dialog", { name: "Edit book" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Identification" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Save metadata" })).toBeTruthy();
  });

  it("localizes the import review dialog in English", async () => {
    const onSelectImport = vi.fn().mockResolvedValue({
      preview_id: "preview-1",
      files: [{
        name: "sample.epub",
        title: "Sample Book",
        authors: ["An Author"],
        isbn: null,
        format: "EPUB",
        candidates: [],
      }],
    });
    render(<LibraryPage language="en" books={[]} status="ready" onSelectImport={onSelectImport} />);

    fireEvent.click(screen.getByRole("button", { name: "Add book" }));

    expect(await screen.findByRole("dialog", { name: "Review book details" })).toBeTruthy();
    expect(screen.getByText("METADATA READ FROM FILE")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add to library" })).toBeTruthy();
  });

  it("localizes protected Calibre removal confirmation in English", () => {
    render(
      <LibraryPage
        language="en"
        books={[book(9, "Sample Book", ["An Author"])]}
        status="ready"
        onRemoveBook={vi.fn(async () => undefined)}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit metadata: Sample Book" }));
    fireEvent.click(screen.getByRole("button", { name: "Move to Calibre Recycle Bin" }));

    expect(screen.getByRole("dialog", { name: "Move book to the Calibre Recycle Bin?" })).toBeTruthy();
    expect(screen.getByText("Calibre must be closed. If backup or verification fails, the book will not be removed.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Back up and move" })).toBeTruthy();
  });

  it("localizes the safe Kindle transfer dialog in English", () => {
    render(
      <LibraryPage
        language="en"
        books={[{ ...book(10, "Sample Book", ["An Author"]), formats: ["EPUB"] }]}
        status="ready"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Send to Kindle: Sample Book" }));

    expect(screen.getByRole("dialog", { name: "Send book" })).toBeTruthy();
    expect(screen.getByLabelText("File format")).toBeTruthy();
    expect(screen.getByLabelText("Name on Kindle")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Back up and send" })).toBeTruthy();
  });

  it("renders the catalog when the Font Loading API is unavailable", () => {
    const fontsDescriptor = Object.getOwnPropertyDescriptor(document, "fonts");
    Object.defineProperty(document, "fonts", { configurable: true, value: undefined });

    try {
      render(<LibraryPage books={[book(1, "Livro de teste", ["Autora"])]} status="ready" />);
    } finally {
      if (fontsDescriptor) Object.defineProperty(document, "fonts", fontsDescriptor);
      else Reflect.deleteProperty(document, "fonts");
    }

    expect(screen.getByRole("listitem", { name: "Livro de teste" })).toBeTruthy();
  });

  it("focuses search with Ctrl+K", () => {
    render(<LibraryPage books={[]} status="ready" />);
    const search = screen.getByRole("searchbox", { name: "Buscar nesta estante" });

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    expect(document.activeElement).toBe(search);
  });

  it("makes sorting and filtering controls accessible and interactive", () => {
    render(<LibraryPage books={[]} status="ready" />);

    const sortSelect = screen.getByRole("combobox", { name: "Ordenar estante" });
    const filterTrigger = document.querySelector<HTMLElement>(".toolbar-filter > summary");

    expect(sortSelect.classList.contains("sort-field-hit-target")).toBe(true);
    expect(getComputedStyle(document.documentElement).getPropertyValue("--sans")).toContain("Inter Local");
    expect(getComputedStyle(sortSelect).fontFamily).toBe("var(--sans)");
    expect(getComputedStyle(sortSelect.parentElement!).minHeight).toBe("48px");
    expect(getComputedStyle(filterTrigger!).minHeight).toBe("48px");
    expect(document.querySelector(".sort-field-value")?.textContent).toBe("Título");
    fireEvent.change(sortSelect, { target: { value: "author" } });
    expect(document.querySelector(".sort-field-value")?.textContent).toBe("Autor");

    expect(filterTrigger).not.toBeNull();
    expect(filterTrigger?.getAttribute("aria-label")).toBe("Abrir filtros da estante");
    expect(filterTrigger?.getAttribute("aria-controls")).toBe("library-filter-panel");

    fireEvent.click(filterTrigger!);
    expect(filterTrigger!.closest("details")?.open).toBe(true);
    expect(screen.getByText("Filtrar biblioteca")).toBeTruthy();
  });

  it("searches the full loaded catalog by text without accents or manual pagination", () => {
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains("book-list")) return 600;
      if (this.classList.contains("book-row")) return 96;
      return 0;
    });
    const books = Array.from({ length: 1_200 }, (_, index) =>
      book(index + 1, `Livro ${index + 1}`, [`Autor ${index + 1}`]),
    );
    books[1_199] = book(
      1_200,
      "A travessia de João: histórias do coração",
      ["Ana Vitória"],
    );

    render(<LibraryPage books={books} status="ready" />);

    expect(screen.getByText("1.200 livros")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox", { name: "Buscar nesta estante" }), {
      target: { value: "JOAO coracao" },
    });

    const result = screen.getByRole("listitem", {
      name: "A travessia de João: histórias do coração",
    });
    expect(within(result).getByText("Ana Vitória")).toBeTruthy();
    expect(screen.getByText("1 resultado")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /carregar mais/i })).toBeNull();
  });

  it("shows loading and actionable unavailable states", () => {
    const { rerender } = render(<LibraryPage books={[]} status="loading" />);
    expect(screen.getByRole("status").textContent).toContain("Carregando biblioteca");

    rerender(<LibraryPage books={[]} status="error" />);
    expect(screen.getByRole("alert").textContent).toContain("Confira se o Calibre está instalado");
  });

  it("requires a second confirmation before moving a Calibre book to its Recycle Bin", async () => {
    const onRemoveBook = vi.fn(async () => undefined);
    render(
      <LibraryPage
        books={[book(7, "Livro para remover", ["Autora"])]}
        status="ready"
        onRemoveBook={onRemoveBook}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Editar metadados de Livro para remover" }));
    fireEvent.click(screen.getByRole("button", { name: "Mover para lixeira do Calibre" }));

    const confirmation = screen.getByRole("dialog", { name: "Mover livro para a lixeira do Calibre?" });
    expect(within(confirmation).getByText("Será criado um backup Restic e a restauração será conferida antes da remoção.")).toBeTruthy();
    expect(onRemoveBook).not.toHaveBeenCalled();

    fireEvent.click(within(confirmation).getByRole("button", { name: "Fazer backup verificado e mover" }));

    expect(onRemoveBook).toHaveBeenCalledWith(7);
  });

  it("locks document scrolling while the metadata editor is open and restores it on close", () => {
    const rootOverflow = document.documentElement.style.overflow;
    const bodyOverflow = document.body.style.overflow;

    render(<LibraryPage books={[book(1, "Livro de teste", ["Autora"])]} status="ready" />);
    fireEvent.click(screen.getByRole("button", { name: "Editar metadados de Livro de teste" }));

    expect(document.documentElement.style.overflow).toBe("hidden");
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(document.documentElement.style.overflow).toBe(rootOverflow);
    expect(document.body.style.overflow).toBe(bodyOverflow);
  });

  it("keeps a single cancel action for closing the metadata editor", () => {
    render(<LibraryPage books={[book(1, "Livro de teste", ["Autora"])]} status="ready" />);
    fireEvent.click(screen.getByRole("button", { name: "Editar metadados de Livro de teste" }));

    expect(screen.getByRole("button", { name: "Cancelar" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Fechar" })).toBeNull();
  });

  it("selects a cover and edits supported custom Calibre fields", async () => {
    const current = {
      ...book(8, "Livro com campos extras", ["Autora"]),
      custom_values: { reading_state: "New", shelf_note: "" },
      custom_column_definitions: [
        {
          label: "reading_state",
          name: "Reading State",
          datatype: "enumeration",
          editable: true,
          is_multiple: false,
          enum_values: ["New", "Reading", "Read"],
        },
        {
          label: "shelf_note",
          name: "Shelf Note",
          datatype: "comments",
          editable: true,
          is_multiple: false,
          enum_values: [],
        },
      ],
    };
    const onEditBook = vi.fn(async (_id, changes) => ({ ...current, custom_values: changes.custom_values }));
    const onSelectCover = vi.fn().mockResolvedValue({ selection_id: "cover-token", name: "capa.png" });
    render(
      <LibraryPage
        books={[current]}
        status="ready"
        onSelectCover={onSelectCover}
        onEditBook={onEditBook}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Editar metadados de Livro com campos extras" }));
    fireEvent.click(screen.getByRole("button", { name: "Alterar capa" }));
    await waitFor(() => expect(screen.getByText("Nova capa: capa.png")).toBeTruthy());
    fireEvent.click(screen.getByText("Campos personalizados"));
    fireEvent.change(screen.getByLabelText(/Reading State/), { target: { value: "Read" } });
    fireEvent.change(screen.getByLabelText(/Shelf Note/), { target: { value: "Favorito" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar metadados" }));

    await waitFor(() => expect(onEditBook).toHaveBeenCalledWith(
      8,
      expect.objectContaining({
        custom_values: { reading_state: "Read", shelf_note: "Favorito" },
      }),
      "cover-token",
    ));
  });

  it("previews selected books before applying a batch tag edit", async () => {
    const onEditTagsBatch = vi.fn(async (ids: number[]) => ids.map((id) => ({
      ...book(id, `Livro ${id}`, ["Autora"]),
      tags: ["Fantasia"],
    })));
    render(
      <LibraryPage
        books={[book(1, "Livro 1", ["Autora"]), book(2, "Livro 2", ["Autora"])]}
        status="ready"
        onEditTagsBatch={onEditTagsBatch}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Selecionar vários" }));
    fireEvent.click(screen.getByRole("button", { name: "Selecionar página" }));
    fireEvent.click(screen.getByRole("button", { name: "Editar tags (2)" }));
    expect(screen.getByRole("dialog", { name: "Editar tags de 2 livros" })).toBeTruthy();
    const tagInputs = screen.getAllByLabelText("Adicionar tag");
    fireEvent.change(tagInputs[0], { target: { value: "Fantasia" } });
    fireEvent.keyDown(tagInputs[0], { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Fazer backup e aplicar tags" }));

    await waitFor(() => expect(onEditTagsBatch).toHaveBeenCalledWith([1, 2], ["Fantasia"], []));
  });

  it("shows cover placeholder when a book has no cover", () => {
    render(
      <LibraryPage
        language="en"
        books={[book(1, "No Cover Book", ["An Author"])]}
        status="ready"
      />,
    );

    // Placeholder with BookOpen icon and number instead of cover image
    expect(screen.getByText("001")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("listitem", { name: "No Cover Book" })).toBeTruthy();
  });

  it("shows cover placeholder when cover image fails to load", () => {
    render(
      <LibraryPage
        language="en"
        books={[
          {
            ...book(2, "Broken Cover Book", ["An Author"]),
            cover_url: "https://example.com/broken-cover.jpg",
          },
        ]}
        status="ready"
      />,
    );

    // Initially shows the cover image
    const coverImg = screen.getByRole("img", {
      name: /Cover: Broken Cover Book/,
    });
    expect(coverImg).toBeTruthy();

    // Simulate image load error
    fireEvent.error(coverImg);

    // After error, placeholder is shown
    expect(screen.getByText("002")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("listitem", { name: "Broken Cover Book" })).toBeTruthy();
  });

  it("shows cover placeholder for missing cover in Portuguese", () => {
    render(
      <LibraryPage
        language="pt-BR"
        books={[book(3, "Livro sem capa", ["Autora"])]}
        status="ready"
      />,
    );

    expect(screen.getByText("003")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("searchbox", { name: "Buscar nesta estante" })).toBeTruthy();
  });

  it("shows cover placeholder when a book has no cover in shelf layout", () => {
    render(
      <LibraryPage
        language="en"
        books={[book(1, "No Cover Book", ["An Author"])]}
        status="ready"
      />,
    );

    // Switch to shelf layout
    fireEvent.click(screen.getByRole("button", { name: /Switch to covers/ }));

    // Placeholder with BookOpen icon and number instead of cover image
    expect(screen.getByText("001")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("listitem", { name: "No Cover Book" })).toBeTruthy();
  });

  it("shows cover placeholder when cover image fails to load in shelf layout", () => {
    render(
      <LibraryPage
        language="en"
        books={[
          {
            ...book(2, "Broken Cover Book", ["An Author"]),
            cover_url: "https://example.com/broken-cover.jpg",
          },
        ]}
        status="ready"
      />,
    );

    // Switch to shelf layout
    fireEvent.click(screen.getByRole("button", { name: /Switch to covers/ }));

    // Initially shows the cover image
    const coverImg = screen.getByRole("img", {
      name: /Cover: Broken Cover Book/,
    });
    expect(coverImg).toBeTruthy();

    // Simulate image load error
    fireEvent.error(coverImg);

    // After error, placeholder is shown
    expect(screen.getByText("002")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("listitem", { name: "Broken Cover Book" })).toBeTruthy();
  });
});
