import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { hasEnglishTranslation } from "./i18n";

const rendererDirectory = path.dirname(fileURLToPath(import.meta.url));

function collectLocalizedLiterals(source: string): string[] {
  const literals = new Set<string>();
  const patterns = [
    /\btx\("((?:[^"\\]|\\.)*)"\)/g,
    /\blocalizeText\(\s*language\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)/g,
  ];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      literals.add(match[1].replace(/\\(["\\])/g, "$1"));
    }
  }
  return [...literals];
}

describe("English translation coverage", () => {
  it("collects localized literals from a synthetic source", () => {
    const source = [
      'const label = tx("Título");',
      'const description = localizeText(language, "Série, publicação e idioma");',
      "tx(dynamicValue);",
    ].join("\n");

    expect(collectLocalizedLiterals(source)).toEqual([
      "Título",
      "Série, publicação e idioma",
    ]);
  });

  it("has an English entry for every literal used by renderer components", () => {
    const missing = fs
      .readdirSync(rendererDirectory)
      .filter((file) => file.endsWith(".tsx") && !file.endsWith(".test.tsx"))
      .flatMap((file) => {
        const source = fs.readFileSync(path.join(rendererDirectory, file), "utf8");
        return collectLocalizedLiterals(source)
          .filter((literal) => !hasEnglishTranslation(literal))
          .map((literal) => `${file}: ${literal}`);
      });

    expect(missing).toEqual([]);
  });
});
