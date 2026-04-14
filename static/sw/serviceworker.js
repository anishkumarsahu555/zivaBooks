var version = 'v1.4.1::';
var OFFLINE_URL = '/static/sw/offline.html';
var CORE_CACHE = version + 'fundamentals';
var RUNTIME_CACHE = version + 'pages';

var CORE_ASSETS = [
    OFFLINE_URL,
    '/static/sw/images/icon-192.png',
    '/static/sw/images/icon-512.png',
    '/static/sw/images/apple-touch-icon.png',
    'https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.1/jquery.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/fomantic-ui/2.9.4/semantic.min.css',
    'https://cdnjs.cloudflare.com/ajax/libs/fomantic-ui/2.9.4/semantic.min.js',
    'https://cdn.datatables.net/1.12.1/css/dataTables.semanticui.min.css',
    'https://cdn.datatables.net/1.12.1/js/jquery.dataTables.min.js',
    'https://cdn.datatables.net/1.12.1/js/dataTables.semanticui.min.js',
    'https://cdn.datatables.net/buttons/2.2.2/js/dataTables.buttons.min.js',
    'https://cdn.datatables.net/buttons/2.2.2/js/buttons.semanticui.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/jszip/3.1.3/jszip.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.1.53/pdfmake.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.1.53/vfs_fonts.js',
    'https://cdn.datatables.net/buttons/2.2.2/js/buttons.html5.min.js',
    'https://cdn.datatables.net/buttons/2.2.2/js/buttons.print.min.js',
    'https://cdn.datatables.net/buttons/2.2.2/js/buttons.colVis.min.js',
    'https://fonts.googleapis.com/css?family=Lato:400,700,400italic,700italic&subset=latin'
];

self.addEventListener("install", function (event) {
    console.log('WORKER: install event in progress.');
    event.waitUntil(
        caches
            .open(CORE_CACHE)
            .then(function (cache) {
                return cache.addAll(CORE_ASSETS);
            })
            .then(function () {
                console.log('WORKER: install completed');
                return self.skipWaiting();
            })
    );
});

function get_url_extension(url) {
    return url.split(/[#?]/)[0].split('.').pop().trim();
}

self.addEventListener("fetch", function (event) {
    console.log('WORKER: fetch event in progress.');
    if (event.request.method !== 'GET') {
        return;
    }

    var request = event.request;
    var acceptsHtml = request.headers.get('accept') && request.headers.get('accept').indexOf('text/html') !== -1;
    var isNavigation = request.mode === 'navigate' || acceptsHtml;

    if (isNavigation) {
        event.respondWith(
            Promise.resolve(event.preloadResponse)
                .then(function (preloadedResponse) {
                    if (preloadedResponse) {
                        var preloadCopy = preloadedResponse.clone();
                        event.waitUntil(
                            caches.open(RUNTIME_CACHE).then(function (cache) {
                                return cache.put(request, preloadCopy);
                            })
                        );
                        return preloadedResponse;
                    }
                    return fetch(request).then(function (response) {
                        var cacheCopy = response.clone();
                        event.waitUntil(
                            caches.open(RUNTIME_CACHE).then(function (cache) {
                                return cache.put(request, cacheCopy);
                            })
                        );
                        return response;
                    });
                })
                .catch(function () {
                    return caches.match(request).then(function (cachedPage) {
                        return cachedPage || caches.match(OFFLINE_URL);
                    });
                })
        );
        return;
    }

    event.respondWith(
        caches
            .match(request)
            .then(function (cached) {
                var networked = fetch(request)
                    .then(fetchedFromNetwork, unableToResolve)
                    .catch(unableToResolve);
                return cached || networked;

                function fetchedFromNetwork(response) {
                    var cacheCopy = response.clone();
                    caches
                        .open(RUNTIME_CACHE)
                        .then(function add(cache) {
                            var img = get_url_extension(request.url);
                            if (img.toLowerCase() === 'png' || img.toLowerCase() === 'jpg' || img.toLowerCase() === 'jpeg' || img.toLowerCase() === 'svg') {
                                cache.put(request, cacheCopy);
                            }
                        })
                    return response;
                }

                function unableToResolve() {
                    if (request.destination === 'image') {
                        return caches.match('/static/sw/images/icon-192.png');
                    }
                    return new Response('Offline', {
                        status: 503,
                        statusText: 'Service Unavailable',
                        headers: new Headers({
                            'Content-Type': 'text/plain'
                        })
                    });
                }
            })
    );
});
self.addEventListener("activate", function (event) {
    console.log('WORKER: activate event in progress.');
    event.waitUntil(
        caches
            .keys()
            .then(function (keys) {
                return Promise.all(
                    keys
                        .filter(function (key) {
                            return !key.startsWith(version);
                        })
                        .map(function (key) {
                            return caches.delete(key);
                        })
                );
            })
            .then(function () {
                console.log('WORKER: activate completed.');
                return Promise.resolve(
                    self.registration.navigationPreload &&
                    self.registration.navigationPreload.enable &&
                    self.registration.navigationPreload.enable()
                ).then(function () {
                    return self.clients.claim();
                });
            })
    );
});
