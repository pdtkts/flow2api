import { describe, expect, it } from "vitest";
import { ADOBE_UPLOADS_URL, isSupportedAdobeUrl } from "./adobe-url";

describe("Adobe page restriction", () => {
  it("accepts only the Canadian uploads route", () => {
    expect(isSupportedAdobeUrl(ADOBE_UPLOADS_URL)).toBe(true);
    expect(isSupportedAdobeUrl(`${ADOBE_UPLOADS_URL}/`)).toBe(true);
    expect(isSupportedAdobeUrl(`${ADOBE_UPLOADS_URL}?sort=newest`)).toBe(true);
    expect(isSupportedAdobeUrl(`${ADOBE_UPLOADS_URL}#asset-2`)).toBe(true);
  });

  it.each([
    "https://contributor.stock.adobe.com/uploads",
    "https://contributor.stock.adobe.com/us/uploads",
    "https://contributor.stock.adobe.com/ca/uploads/assets",
    "https://contributor.stock.adobe.com/ca/portfolio",
    "http://contributor.stock.adobe.com/ca/uploads",
    "https://fr.contributor.stock.adobe.com/ca/uploads",
  ])("rejects %s", (url) => {
    expect(isSupportedAdobeUrl(url)).toBe(false);
  });
});
