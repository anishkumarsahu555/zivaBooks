(function () {
    var config = window.ZivaStaffLocationAutoTrack || {};
    if (!config.enabled || !config.pingUrl || !config.bulkUrl || !config.csrfToken) {
        return;
    }

    var intervalMs = parseInt(config.intervalMs || 300000, 10);
    var minPageLoadGapMs = parseInt(config.minPageLoadGapMs || 60000, 10);
    var dbName = "zivaStaffLocation";
    var storeName = "pendingLocations";
    var lastPushKey = "zivaStaffLocationLastAutoPush";
    var timer = null;

    function openDb() {
        return new Promise(function (resolve, reject) {
            var request = indexedDB.open(dbName, 1);
            request.onupgradeneeded = function () {
                if (!request.result.objectStoreNames.contains(storeName)) {
                    request.result.createObjectStore(storeName, {keyPath: "client_sample_id"});
                }
            };
            request.onsuccess = function () { resolve(request.result); };
            request.onerror = function () { reject(request.error); };
        });
    }

    function withStore(mode, callback) {
        return openDb().then(function (db) {
            return new Promise(function (resolve, reject) {
                var tx = db.transaction(storeName, mode);
                var store = tx.objectStore(storeName);
                var result = callback(store);
                tx.oncomplete = function () { resolve(result); };
                tx.onerror = function () { reject(tx.error); };
            });
        });
    }

    function queueLocation(sample) {
        return withStore("readwrite", function (store) {
            store.put(sample);
        }).catch(function () {});
    }

    function getQueuedLocations() {
        return openDb().then(function (db) {
            return new Promise(function (resolve, reject) {
                var tx = db.transaction(storeName, "readonly");
                var request = tx.objectStore(storeName).getAll();
                request.onsuccess = function () { resolve(request.result || []); };
                request.onerror = function () { reject(request.error); };
            });
        });
    }

    function deleteQueuedLocations(samples) {
        return withStore("readwrite", function (store) {
            samples.forEach(function (sample) {
                store.delete(sample.client_sample_id);
            });
        }).catch(function () {});
    }

    function getCookieToken() {
        var cookieValue = null;
        if (document.cookie && document.cookie !== "") {
            var cookies = document.cookie.split(";");
            for (var i = 0; i < cookies.length; i++) {
                var cookie = cookies[i].trim();
                if (cookie.substring(0, 10) === "csrftoken=") {
                    cookieValue = decodeURIComponent(cookie.substring(10));
                    break;
                }
            }
        }
        return cookieValue || config.csrfToken || "";
    }

    function postJson(url, payload) {
        return fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookieToken(),
                "X-Requested-With": "XMLHttpRequest"
            },
            body: JSON.stringify(payload)
        }).then(function (response) {
            if (!response.ok) {
                throw new Error("Location sync failed.");
            }
            return response.json();
        });
    }

    function getBatteryPercent() {
        if (!navigator.getBattery) {
            return Promise.resolve(null);
        }
        return navigator.getBattery().then(function (battery) {
            return Math.round((battery.level || 0) * 100);
        }).catch(function () {
            return null;
        });
    }

    function buildSample(position, batteryPercent) {
        var coords = position.coords || {};
        return {
            client_sample_id: Date.now() + "-" + Math.random().toString(36).slice(2),
            latitude: coords.latitude,
            longitude: coords.longitude,
            accuracy: coords.accuracy,
            speed: coords.speed,
            heading: coords.heading,
            battery_percent: batteryPercent,
            recorded_at: new Date(position.timestamp || Date.now()).toISOString(),
            source: "pwa_auto"
        };
    }

    function syncQueuedLocations() {
        return getQueuedLocations().then(function (items) {
            if (!items.length) {
                return;
            }
            return postJson(config.bulkUrl, {locations: items}).then(function (response) {
                if (response.message === "success") {
                    return deleteQueuedLocations(items);
                }
            });
        }).catch(function () {});
    }

    function shouldPushNow() {
        var lastPush = parseInt(localStorage.getItem(lastPushKey) || "0", 10);
        return !lastPush || Date.now() - lastPush >= minPageLoadGapMs;
    }

    function pushCurrentLocation() {
        if (!navigator.geolocation || !shouldPushNow()) {
            return;
        }
        localStorage.setItem(lastPushKey, String(Date.now()));
        navigator.geolocation.getCurrentPosition(function (position) {
            getBatteryPercent().then(function (batteryPercent) {
                var sample = buildSample(position, batteryPercent);
                postJson(config.pingUrl, sample).then(syncQueuedLocations).catch(function () {
                    queueLocation(sample);
                });
            });
        }, function () {}, {
            enableHighAccuracy: true,
            timeout: 20000,
            maximumAge: 30000
        });
    }

    function start() {
        syncQueuedLocations();
        pushCurrentLocation();
        timer = window.setInterval(pushCurrentLocation, intervalMs);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
    window.addEventListener("online", syncQueuedLocations);
    window.addEventListener("beforeunload", function () {
        if (timer) {
            window.clearInterval(timer);
        }
    });
})();
