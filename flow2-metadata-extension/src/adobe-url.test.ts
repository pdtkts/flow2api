import { describe, expect, it } from "vitest";
import { ADOBE_UPLOADS_URL, ADOBE_UPLOADS_URLS, isSupportedAdobeUrl, normalizedAdobeUploadsRoute } from "./adobe-url";

describe("Adobe page restriction", () => {
  it("accepts only the supported English and Canadian uploads routes", () => {
    for (const url of ADOBE_UPLOADS_URLS) {
      expect(isSupportedAdobeUrl(url)).toBe(true);
      expect(isSupportedAdobeUrl(`${url}/`)).toBe(true);
      expect(isSupportedAdobeUrl(`${url}?sort=newest`)).toBe(true);
      expect(isSupportedAdobeUrl(`${url}#asset-2`)).toBe(true);
    }
    expect(ADOBE_UPLOADS_URL).toBe("https://contributor.stock.adobe.com/ca/uploads");
  });

  it("normalizes supported routes without query strings or fragments", () => {
    expect(normalizedAdobeUploadsRoute("https://contributor.stock.adobe.com/en/uploads/?sort=newest#asset-2"))
      .toBe("https://contributor.stock.adobe.com/en/uploads");
    expect(normalizedAdobeUploadsRoute("https://contributor.stock.adobe.com/ca/uploads#asset-2"))
      .toBe("https://contributor.stock.adobe.com/ca/uploads");
    expect(normalizedAdobeUploadsRoute("https://contributor.stock.adobe.com/us/uploads")).toBe("");
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
