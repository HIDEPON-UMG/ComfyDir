// ComfyDir Service Worker
//
// 役割:
//   1) 静的アセット (HTML / CSS / JS / icon) を precache
//   2) サムネと /api/prompt-category-map は CacheFirst (起動時 1 回しか変わらない)
//   3) その他 /api/* は NetworkFirst (失敗時 cache fallback)
//   4) ナビゲーション (req.mode === "navigate") は NetworkFirst → 失敗時 /offline.html
//
// VERSION を変更すると、activate 時に古い comfydir-* cache が自動で消える。
// app.js / style.css / index.html / manifest.json を改修したら必ずインクリメントすること。

const VERSION = "v5";
const PRECACHE = `comfydir-precache-${VERSION}`;
const RUNTIME  = `comfydir-runtime-${VERSION}`;
const OFFLINE_URL = "/offline.html";

const PRECACHE_URLS = [
  "/",
  "/offline.html",
  "/manifest.json",
  "/static/style.css",
  "/static/app.js",
  "/static/favicon.svg",
  "/assets/icon-192.png",
  "/assets/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(PRECACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((n) => n.startsWith("comfydir-") && n !== PRECACHE && n !== RUNTIME)
          .map((n) => caches.delete(n))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;                  // 書込系は素通し
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // クロスオリジンは素通し

  // 1) サムネは CacheFirst (image_id + width で URL が固有なので冪等)
  if (/^\/api\/images\/\d+\/thumb/.test(url.pathname)) {
    event.respondWith(cacheFirst(req, RUNTIME));
    return;
  }
  // 2) prompt-category-map は CacheFirst (起動時 1 回しか変わらない)
  if (url.pathname === "/api/prompt-category-map") {
    event.respondWith(cacheFirst(req, RUNTIME));
    return;
  }
  // 3) /api/* は NetworkFirst (失敗時 cache、なければそのまま失敗)
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(networkFirst(req, RUNTIME));
    return;
  }
  // 4) ナビゲーション (HTML) は NetworkFirst → 失敗時 offline.html
  if (req.mode === "navigate") {
    event.respondWith(
      networkFirst(req, RUNTIME).catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }
  // 5) 同一オリジン GET 静的 (style.css / app.js / favicon / /static/*) は CacheFirst
  event.respondWith(cacheFirst(req, RUNTIME));
});

async function cacheFirst(req, cacheName) {
  const cached = await caches.match(req);
  if (cached) return cached;
  try {
    const res = await fetch(req);
    if (res.ok) {
      const cache = await caches.open(cacheName);
      cache.put(req, res.clone());
    }
    return res;
  } catch (e) {
    const fallback = await caches.match(OFFLINE_URL);
    if (fallback) return fallback;
    throw e;
  }
}

async function networkFirst(req, cacheName) {
  try {
    const res = await fetch(req);
    if (res.ok) {
      const cache = await caches.open(cacheName);
      cache.put(req, res.clone());
    }
    return res;
  } catch (e) {
    const cached = await caches.match(req);
    if (cached) return cached;
    throw e;
  }
}
