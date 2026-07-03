import { describe, expect, it } from "vitest";
import { DEFAULT_PREFERENCES, keywordTypesFor, migratePreferences, normalizePlatforms } from "./preferences";

describe("generation preferences", () => {
  it("uses Adobe-balanced defaults for a fresh installation", () => {
    expect(migratePreferences(undefined)).toMatchObject({
      titleMin: 70,
      titleMax: 140,
      keywordMin: 25,
      keywordMax: 45,
      descriptionMin: 0,
      descriptionMax: 0,
      includeCategory: true,
      titleStyle: "seo-optimized",
      keywordStyle: "mixed",
      markGenerativeAi: true,
      confirmFictionalPeopleProperty: true,
    });
  });

  it("migrates legacy disabled limits to their previous effective values", () => {
    const migrated = migratePreferences({
      ...DEFAULT_PREFERENCES,
      titleMin: 12,
      titleMax: 24,
      keywordMin: 2,
      keywordMax: 4,
      descriptionMin: undefined,
      titleStyle: undefined,
      platforms: undefined,
      includeCategory: undefined,
      limitsEnabled: false,
      autoCategory: false,
    });
    expect(migrated).toMatchObject({ titleMin: 80, titleMax: 150, keywordMin: 30, keywordMax: 40, includeCategory: false });
  });

  it("preserves legacy custom limits when they were enabled", () => {
    const migrated = migratePreferences({
      titleMin: 90,
      titleMax: 160,
      keywordMin: 20,
      keywordMax: 42,
      limitsEnabled: true,
      autoCategory: true,
    });
    expect(migrated).toMatchObject({ titleMin: 90, titleMax: 160, keywordMin: 20, keywordMax: 42, includeCategory: true });
  });

  it("keeps Adobe selected while trimming and deduplicating platforms", () => {
    expect(normalizePlatforms(["shutterstock", "Adobe-Stock"], " custom-market, SHUTTERSTOCK "))
      .toEqual(["adobe-stock", "shutterstock", "custom-market"]);
  });

  it.each([
    ["single-word", { singleWord: true, doubleWord: false, mixed: false }],
    ["double-word", { singleWord: false, doubleWord: true, mixed: false }],
    ["mixed", { singleWord: false, doubleWord: false, mixed: true }],
  ] as const)("maps %s to a one-hot API keyword configuration", (style, expected) => {
    expect(keywordTypesFor(style)).toEqual(expected);
  });
});
