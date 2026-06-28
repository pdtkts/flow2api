export const ADOBE_UPLOADS_URL = "https://contributor.stock.adobe.com/ca/uploads";

export function isSupportedAdobeUrl(value: string | undefined): boolean {
  if (!value) return false;
  try {
    const url = new URL(value);
    const pathname = url.pathname.replace(/\/$/, "");
    return url.protocol === "https:"
      && url.hostname === "contributor.stock.adobe.com"
      && pathname === "/ca/uploads";
  } catch {
    return false;
  }
}
