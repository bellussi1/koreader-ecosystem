import { describe, expect, it, vi } from "vitest";
import { getUiLocale, loadLanguagePreference, resolveInitialLanguage, saveLanguagePreference, translate } from "./i18n";

describe("UI language preference", () => {
  it("prefers a previously saved language", () => {
    expect(resolveInitialLanguage("en", "pt-BR")).toBe("en");
    expect(resolveInitialLanguage("pt-BR", "en-US")).toBe("pt-BR");
  });

  it("uses the Windows locale when there is no saved preference", () => {
    expect(resolveInitialLanguage(null, "en-US")).toBe("en");
    expect(resolveInitialLanguage(null, "pt-PT")).toBe("pt-BR");
    expect(resolveInitialLanguage(null, "es-ES")).toBe("pt-BR");
  });

  it("ignores unsupported saved values and falls back to the system locale", () => {
    expect(resolveInitialLanguage("fr", "en-GB")).toBe("en");
  });

  it("persists the explicit selection under an app-scoped key", () => {
    const setItem = vi.fn();
    saveLanguagePreference({ setItem }, "en");
    expect(setItem).toHaveBeenCalledWith("kre.ui-language", "en");
  });

  it("loads the stored language and provides matching formatting locales", () => {
    expect(loadLanguagePreference({ getItem: () => "pt-BR" }, "en-US")).toBe("pt-BR");
    expect(getUiLocale("pt-BR")).toBe("pt-BR");
    expect(getUiLocale("en")).toBe("en-US");
  });

  it("translates navigation and language settings labels", () => {
    expect(translate("en", "nav.library")).toBe("Library");
    expect(translate("pt-BR", "settings.language.ptBR")).toBe("Português (Brasil)");
  });
});
