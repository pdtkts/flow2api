(function () {
  'use strict';

  // ── 1. Remove navigator.webdriver from prototype chain ──────────────────────
  // Most reliable method — deletes Chrome's automation marker from the prototype
  try {
    const proto = Object.getPrototypeOf(navigator);
    if (Object.getOwnPropertyDescriptor(proto, 'webdriver')) {
      Object.defineProperty(proto, 'webdriver', {
        get: () => undefined,
        configurable: true,
        enumerable: true
      });
    }
  } catch (e) {}

  try {
    Object.defineProperty(navigator, 'webdriver', {
      get: () => undefined,
      configurable: true,
      enumerable: true
    });
  } catch (e) {}

  // ── 2. Platform & hardware ───────────────────────────────────────────────────
  try {
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32', configurable: true });
    Object.defineProperty(navigator, 'language', { get: () => 'en-US', configurable: true });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'], configurable: true });
    const hwc = [4, 6, 8, 12, 16][Math.floor(Math.random() * 5)];
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => hwc, configurable: true });
    const mem = [4, 8, 16][Math.floor(Math.random() * 3)];
    Object.defineProperty(navigator, 'deviceMemory', { get: () => mem, configurable: true });
    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0, configurable: true });
  } catch (e) {}

  // ── 3. Remove Chrome automation variable traces ──────────────────────────────
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; } catch (e) {}
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; } catch (e) {}
  try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol; } catch (e) {}
  try { delete window.__webdriver_evaluate; } catch (e) {}
  try { delete window.__selenium_evaluate; } catch (e) {}
  try { delete window.__webdriver_script_fn; } catch (e) {}

  // ── 4. Permissions API ───────────────────────────────────────────────────────
  try {
    const origQuery = window.navigator.permissions.query.bind(navigator.permissions);
    window.navigator.permissions.query = (params) => {
      if (params && params.name === 'notifications') {
        return Promise.resolve({ state: 'denied', onchange: null });
      }
      return origQuery(params);
    };
  } catch (e) {}

  // ── 5. Canvas fingerprint — fixed noise per session ──────────────────────────
  const noiseR = Math.floor(Math.random() * 4);
  const noiseG = Math.floor(Math.random() * 4);
  const noiseB = Math.floor(Math.random() * 4);
  const noiseCount = Math.floor(Math.random() * 8) + 2;

  function applyCanvasNoise(ctx, w, h) {
    try {
      if (!w || !h) return;
      const id = ctx.getImageData(0, 0, w, h);
      const step = Math.max(1, Math.floor(id.data.length / 4 / noiseCount));
      for (let i = 0; i < id.data.length; i += step * 4) {
        id.data[i]     = (id.data[i]     + noiseR) % 256;
        id.data[i + 1] = (id.data[i + 1] + noiseG) % 256;
        id.data[i + 2] = (id.data[i + 2] + noiseB) % 256;
      }
      ctx.putImageData(id, 0, 0);
    } catch (e) {}
  }

  try {
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function () {
      const ctx = this.getContext('2d');
      if (ctx && this.width > 0 && this.height > 0) applyCanvasNoise(ctx, this.width, this.height);
      return origToDataURL.apply(this, arguments);
    };

    const origToBlob = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function (cb, type, q) {
      const ctx = this.getContext('2d');
      if (ctx && this.width > 0 && this.height > 0) applyCanvasNoise(ctx, this.width, this.height);
      return origToBlob.call(this, cb, type, q);
    };

    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function () {
      const d = origGetImageData.apply(this, arguments);
      const step = Math.max(1, Math.floor(d.data.length / 4 / noiseCount));
      for (let i = 0; i < d.data.length; i += step * 4) {
        d.data[i]     = (d.data[i]     + noiseR) % 256;
        d.data[i + 1] = (d.data[i + 1] + noiseG) % 256;
        d.data[i + 2] = (d.data[i + 2] + noiseB) % 256;
      }
      return d;
    };
  } catch (e) {}

  // ── 6. WebGL fingerprint ─────────────────────────────────────────────────────
  const gpus = [
    ['Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0, D3D11)'],
    ['Google Inc. (Intel)',  'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)'],
    ['Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)'],
    ['Google Inc. (AMD)',    'ANGLE (AMD, Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)'],
    ['Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)'],
  ];
  const [vendor, renderer] = gpus[Math.floor(Math.random() * gpus.length)];

  function patchWebGL(ctx) {
    try {
      const orig = ctx.prototype.getParameter;
      ctx.prototype.getParameter = function (p) {
        if (p === 37445) return vendor;
        if (p === 37446) return renderer;
        return orig.call(this, p);
      };
    } catch (e) {}
  }
  try { patchWebGL(WebGLRenderingContext); } catch (e) {}
  try { patchWebGL(WebGL2RenderingContext); } catch (e) {}

  // ── 7. Screen size spoof ─────────────────────────────────────────────────────
  try {
    const screens = [
      [1920, 1080], [2560, 1440], [1366, 768], [1440, 900], [1280, 720]
    ];
    const [sw, sh] = screens[Math.floor(Math.random() * screens.length)];
    Object.defineProperty(screen, 'width',       { get: () => sw, configurable: true });
    Object.defineProperty(screen, 'height',      { get: () => sh, configurable: true });
    Object.defineProperty(screen, 'availWidth',  { get: () => sw, configurable: true });
    Object.defineProperty(screen, 'availHeight', { get: () => sh - 40, configurable: true });
  } catch (e) {}

})();
