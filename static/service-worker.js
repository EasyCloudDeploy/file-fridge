// File Fridge Service Worker
// Provides basic caching for static assets and offline support

const CACHE_NAME = 'file-fridge-v1';
const OFFLINE_URL = '/static/html/offline.html';

const STATIC_ASSETS = [
  '/',
  '/static/html/offline.html',
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/js/dashboard.js',
  '/static/js/files.js',
  '/static/js/paths.js',
  '/static/js/storage.js',
  '/static/js/stats.js',
  '/static/js/tags.js',
  '/static/js/notifiers.js',

  '/static/icons/icon-192.svg',
  '/static/icons/icon-512.svg',
  '/static/manifest.json'
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('Service Worker: Caching static assets');
        return cache.addAll(STATIC_ASSETS);
      })
      .then(() => self.skipWaiting())
  );
});

// Helper to check if user is online
function isOnline() {
  return self.clients.matchAll()
    .then(clients => {
      if (clients.length === 0) return false;
      return clients[0].navigator.onLine;
    });
}

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((cacheName) => cacheName !== CACHE_NAME)
            .map((cacheName) => caches.delete(cacheName))
        );
      })
      .then(() => self.clients.claim())
  );
});

// Fetch event - serve from cache, fall back to network
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only cache GET requests
  if (request.method !== 'GET') {
    return;
  }

  // For API requests, always use network first
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request)
        .catch(() => {
          // Return a simple error response for offline API calls
          return new Response(
            JSON.stringify({ error: 'Offline', message: 'Network unavailable' }),
            {
              status: 503,
              headers: { 'Content-Type': 'application/json' }
            }
          );
        })
    );
    return;
  }

  // For HTML pages, try network first, fall back to cache, then offline page
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          // Cache the fetched response
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, responseToCache);
          });
          return response;
        })
        .catch(() => {
          // Network failed, try cache
          return caches.match(request)
            .then((cachedResponse) => {
              if (cachedResponse) {
                return cachedResponse;
              }
              // Nothing in cache, show offline page
              return caches.match(OFFLINE_URL);
            });
        })
    );
    return;
  }

  // For static assets, try cache first, then network
  event.respondWith(
    caches.match(request)
      .then((cachedResponse) => {
        if (cachedResponse) {
          return cachedResponse;
        }

        return fetch(request)
          .then((response) => {
            // Don't cache non-successful responses
            if (!response || response.status !== 200 || response.type !== 'basic') {
              return response;
            }

            // Cache the fetched response for future use
            const responseToCache = response.clone();
            caches.open(CACHE_NAME)
              .then((cache) => {
                // Only cache static assets and HTML pages
                if (url.pathname.startsWith('/static/') ||
                    url.pathname === '/' ||
                    url.pathname.endsWith('.html')) {
                  cache.put(request, responseToCache);
                }
              });

            return response;
          });
      })
  );
});
