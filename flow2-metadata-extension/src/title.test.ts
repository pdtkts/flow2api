import { describe, expect, it } from "vitest";
import { DEFAULT_PREFERENCES } from "./storage";
import { applyTitleRules, expandCustomPrompt } from "./title";

describe("title compatibility rules", () => {
  it("replaces an existing background phrase before adding the selected localized suffix", () => {
    const preferences = { ...DEFAULT_PREFERENCES, language: "de" as const, titleSuffix: "transparent" as const };
    expect(applyTitleRules("Ceramic cup on white background", preferences)).toBe("Ceramic cup isoliert auf Transparentem Hintergrund");
  });

  it("applies custom text and caps titles at 195 characters", () => {
    const preferences = { ...DEFAULT_PREFERENCES, titlePrefix: "Prefix", customTitleSuffix: "Suffix" };
    expect(applyTitleRules("x".repeat(220), preferences).length).toBeLessThanOrEqual(195);
  });

  it("expands custom prompt placeholders without exposing model controls", () => {
    const preferences = { ...DEFAULT_PREFERENCES, language: "fr" as const, includeCategory: true };
    expect(expandCustomPrompt("Use {language}; type {fileType}; {category}", preferences, "vector"))
      .toContain("Use French; type vector; Return categoryId");
  });
});
