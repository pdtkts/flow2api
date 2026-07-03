import type { KeywordStyle, LanguageCode, Preferences, TitleStyle, TitleSuffix } from "./types";

export const KNOWN_PLATFORMS = ["adobe-stock", "shutterstock", "getty-images", "istock", "pond5"] as const;
const TITLE_STYLES: TitleStyle[] = ["seo-optimized", "descriptive", "creative", "commercial"];
const KEYWORD_STYLES: KeywordStyle[] = ["single-word", "double-word", "mixed"];
const LANGUAGES: LanguageCode[] = ["en", "fr", "de", "es", "it", "pt", "ja", "pl", "ko"];
const TITLE_SUFFIXES: TitleSuffix[] = ["none", "transparent", "white", "png_transparent"];

export const DEFAULT_PREFERENCES: Preferences = {
  mode: "upload",
  includeCategory: true,
  language: "en",
  titleSuffix: "none",
  titlePrefix: "",
  customTitleSuffix: "",
  titleMin: 70,
  titleMax: 140,
  keywordMin: 25,
  keywordMax: 45,
  descriptionMin: 0,
  descriptionMax: 0,
  platforms: ["adobe-stock"],
  customPlatforms: "",
  includeReleases: false,
  titleStyle: "seo-optimized",
  keywordStyle: "mixed",
  transparentBackground: false,
  markGenerativeAi: true,
  confirmFictionalPeopleProperty: true,
  customPromptEnabled: false,
  customPrompt: "",
};

function numberOr(value: unknown, fallback: number): number {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number) : fallback;
}

function enumOr<T extends string>(value: unknown, values: readonly T[], fallback: T): T {
  return typeof value === "string" && values.includes(value as T) ? value as T : fallback;
}

export function normalizePlatforms(platforms: unknown, customPlatforms = ""): string[] {
  const selected = Array.isArray(platforms) ? platforms.map(String) : [];
  const custom = customPlatforms.split(",").map((item) => item.trim().toLowerCase()).filter(Boolean);
  return [...new Set(["adobe-stock", ...selected, ...custom].map((item) => item.trim().toLowerCase()).filter(Boolean))];
}

export function keywordTypesFor(style: KeywordStyle) {
  return {
    singleWord: style === "single-word",
    doubleWord: style === "double-word",
    mixed: style === "mixed",
  };
}

export function migratePreferences(value: unknown): Preferences {
  if (!value || typeof value !== "object") return { ...DEFAULT_PREFERENCES, platforms: [...DEFAULT_PREFERENCES.platforms] };
  const raw = value as Partial<Preferences> & { autoCategory?: boolean; limitsEnabled?: boolean };
  const legacy = raw.descriptionMin === undefined && raw.titleStyle === undefined && raw.platforms === undefined;
  const titleMin = legacy && !raw.limitsEnabled ? 80 : numberOr(raw.titleMin, DEFAULT_PREFERENCES.titleMin);
  const titleMax = legacy && !raw.limitsEnabled ? 150 : numberOr(raw.titleMax, DEFAULT_PREFERENCES.titleMax);
  const keywordMin = legacy && !raw.limitsEnabled ? 30 : numberOr(raw.keywordMin, DEFAULT_PREFERENCES.keywordMin);
  const keywordMax = legacy && !raw.limitsEnabled ? 40 : numberOr(raw.keywordMax, DEFAULT_PREFERENCES.keywordMax);
  return {
    mode: "upload",
    includeCategory: Boolean(raw.includeCategory ?? raw.autoCategory ?? (legacy ? false : DEFAULT_PREFERENCES.includeCategory)),
    language: enumOr(raw.language, LANGUAGES, DEFAULT_PREFERENCES.language),
    titleSuffix: enumOr(raw.titleSuffix, TITLE_SUFFIXES, DEFAULT_PREFERENCES.titleSuffix),
    titlePrefix: typeof raw.titlePrefix === "string" ? raw.titlePrefix : "",
    customTitleSuffix: typeof raw.customTitleSuffix === "string" ? raw.customTitleSuffix : "",
    titleMin,
    titleMax,
    keywordMin,
    keywordMax,
    descriptionMin: numberOr(raw.descriptionMin, DEFAULT_PREFERENCES.descriptionMin),
    descriptionMax: numberOr(raw.descriptionMax, DEFAULT_PREFERENCES.descriptionMax),
    platforms: normalizePlatforms(raw.platforms, ""),
    customPlatforms: typeof raw.customPlatforms === "string" ? raw.customPlatforms : "",
    includeReleases: Boolean(raw.includeReleases),
    titleStyle: enumOr(raw.titleStyle, TITLE_STYLES, DEFAULT_PREFERENCES.titleStyle),
    keywordStyle: enumOr(raw.keywordStyle, KEYWORD_STYLES, DEFAULT_PREFERENCES.keywordStyle),
    transparentBackground: Boolean(raw.transparentBackground),
    markGenerativeAi: raw.markGenerativeAi ?? DEFAULT_PREFERENCES.markGenerativeAi,
    confirmFictionalPeopleProperty: raw.confirmFictionalPeopleProperty ?? DEFAULT_PREFERENCES.confirmFictionalPeopleProperty,
    customPromptEnabled: Boolean(raw.customPromptEnabled),
    customPrompt: typeof raw.customPrompt === "string" ? raw.customPrompt : "",
  };
}
