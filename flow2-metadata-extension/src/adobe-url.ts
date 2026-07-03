export const ADOBE_UPLOADS_URL = "https://contributor.stock.adobe.com/ca/uploads";
export const ADOBE_UPLOADS_URLS = [
  "https://contributor.stock.adobe.com/en/uploads",
  ADOBE_UPLOADS_URL,
] as const;

const supportedUploadPaths = new Set(["/en/uploads", "/ca/uploads"]);

export function isSupportedAdobeUrl(value: string | undefined): boolean {
  if (!value) return false;
  try {
    const url = new URL(value);
    const pathname = url.pathname.replace(/\/$/, "");
    return url.protocol === "https:"
      && url.hostname === "contributor.stock.adobe.com"
      && supportedUploadPaths.has(pathname);
  } catch {
    return false;
  }
}

export function normalizedAdobeUploadsRoute(value: string | undefined): string {
  if (!isSupportedAdobeUrl(value)) return "";
  const url = new URL(value!);
  return `https://contributor.stock.adobe.com${url.pathname.replace(/\/$/, "")}`;
}
