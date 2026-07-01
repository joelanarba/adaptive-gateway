/**
 * Adaptive Gateway Client SDK
 * 
 * A wrapper around native `fetch` that automatically measures network quality
 * (RTT and ECT) and injects the corresponding headers so the gateway can optimize payloads.
 */

let lastRtt = null;
let lastPingTime = 0;
const PING_INTERVAL_MS = 60000; // Ping once a minute max

/**
 * Perform a lightweight HEAD request to measure Round-Trip Time.
 * @param {string} pingUrl - The URL to ping
 * @returns {Promise<number>} - RTT in milliseconds
 */
async function measureRtt(pingUrl) {
  const start = performance.now();
  try {
    await fetch(pingUrl, { method: "HEAD", cache: "no-cache" });
    const end = performance.now();
    return Math.round(end - start);
  } catch (err) {
    console.warn("[AdaptiveGateway] Failed to measure RTT:", err);
    return null;
  }
}

/**
 * Factory to create an adaptive fetch instance.
 * 
 * @param {Object} config
 * @param {string} config.gatewayUrl - The base URL of your Adaptive Gateway.
 * @param {string} [config.pingEndpoint="/health"] - A lightweight endpoint to measure RTT.
 * @param {boolean} [config.autoPing=true] - Whether to automatically measure RTT.
 * @returns {Function} - A patched fetch function.
 */
function createAdaptiveFetch({ gatewayUrl, pingEndpoint = "/health", autoPing = true }) {
  const pingUrl = `${gatewayUrl.replace(/\/$/, "")}${pingEndpoint}`;

  return async function adaptiveFetch(input, init = {}) {
    const headers = new Headers(init.headers || {});

    // 1. Inject ECT (Effective Connection Type) if available
    if (typeof navigator !== "undefined" && navigator.connection && navigator.connection.effectiveType) {
      headers.set("ECT", navigator.connection.effectiveType);
    }
    
    // 2. Inject Save-Data if available
    if (typeof navigator !== "undefined" && navigator.connection && navigator.connection.saveData) {
      headers.set("Save-Data", "on");
    }

    // 3. Inject X-Client-RTT (either cached or eagerly measured)
    if (autoPing) {
      const now = Date.now();
      if (lastRtt === null || now - lastPingTime > PING_INTERVAL_MS) {
        // Run asynchronously so we don't block this request, but we'll have it for the next one.
        // Alternatively, we could await it here for the *first* request.
        lastPingTime = now;
        measureRtt(pingUrl).then((rtt) => {
          if (rtt !== null) lastRtt = rtt;
        });
      }
      
      if (lastRtt !== null) {
        headers.set("X-Client-RTT", lastRtt.toString());
      }
    }

    // Pass the modified headers to the underlying fetch
    const newInit = { ...init, headers };
    return fetch(input, newInit);
  };
}

module.exports = {
  createAdaptiveFetch,
  measureRtt
};
