import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { hasEnglishTranslation } from "./i18n";

const rendererDirectory = path.dirname(fileURLToPath(import.meta.url));

function collectCallLiterals(source: string, openIndex: number): string[] {
  const literals: string[] = [];
  let depth = 0;

  for (let index = openIndex; index < source.length; index++) {
    const character = source[index];

    if (character === "\"" || character === "'" || character === "`") {
      const quote = character;
      const before = source.slice(Math.max(0, index - 8), index);
      if (/(===|!==|==|!=)\s*$/.test(before)) {
        index++;
        while (index < source.length) {
          if (source[index] === "\\") {
            index += 2;
            continue;
          }
          if (source[index] === quote) break;
          index++;
        }
        continue;
      }

      let value = "";
      let closed = false;
      for (index++; index < source.length; index++) {
        const current = source[index];
        if (current === "\\") {
          value += source[index + 1] ?? "";
          index++;
          continue;
        }
        if (current === quote) {
          closed = true;
          break;
        }
        value += current;
      }
      if (closed && (quote !== "`" || !value.includes("${")) && value.trim()) {
        literals.push(value);
      }
      continue;
    }

    if (character === "(") {
      depth++;
    } else if (character === ")") {
      depth--;
      if (depth === 0) break;
    }
  }

  return literals;
}

function collectLocalizedLiterals(source: string): string[] {
  const literals = new Set<string>();
  for (const match of source.matchAll(/\b(?:tx|localizeText)\s*\(/g)) {
    const openIndex = (match.index ?? 0) + match[0].length - 1;
    for (const literal of collectCallLiterals(source, openIndex)) {
      literals.add(literal);
    }
  }
  return [...literals];
}

describe("English translation coverage", () => {
  it("collects single-quoted and conditional literals from a synthetic source", () => {
    const source = [
      "const title = tx('Título');",
      'const state = tx(status === "ready" ? "Pronto" : "Offline");',
      'const step = localizeText(language, value === "confirm" ? "Confirmar" : "Voltar");',
      "tx(dynamicValue);",
    ].join("\n");

    expect(collectLocalizedLiterals(source)).toEqual([
      "Título",
      "Pronto",
      "Offline",
      "Confirmar",
      "Voltar",
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
