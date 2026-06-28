# Flow2 Metadata

Maintainable Manifest V3 side-panel extension for generating and applying Adobe Stock metadata through Flow2 API.

## Build and test

```powershell
npm install
npm run typecheck
npm test
npm run build
```

Load `dist/` as an unpacked extension in Chrome. Create a managed Flow2 API key with the `adobe:metadata` scope, then connect using the Flow2 API-only base URL. Provider credentials and model routing remain on the server.

The side panel and content automation activate only on `https://contributor.stock.adobe.com/ca/uploads`. Other Adobe Contributor routes remain locked.

The separate repository directory `extension/` is the Flow2 captcha worker and is not part of this package.
