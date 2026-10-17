/**
 * Adaptive Gateway Express Middleware
 * 
 * Automatically strips specified JSON fields if the client connection
 * is detected as degraded based on incoming headers (X-Client-RTT, ECT, Save-Data).
 */

const DEFAULT_OPTIONS = {
  rttDegradedThresholdMs: 150,
  stripFields: [], // Array of dot-notation paths (e.g., ['data.largeImage', 'comments'])
  simulateDegraded: false, // For testing
};

/**
 * Helper to safely delete a nested property from an object using a dot-notation path.
 */
function deleteNestedPath(obj, path) {
  if (!obj || typeof obj !== 'object') return;
  const keys = path.split('.');
  if (keys.some(key => ['__proto__', 'prototype', 'constructor'].includes(key))) return;
  if (Array.isArray(obj)) {
    obj.forEach(item => deleteNestedPath(item, path));
    return;
  }
  const [head, ...tail] = keys;
  if (!Object.prototype.hasOwnProperty.call(obj, head)) return;
  if (tail.length) deleteNestedPath(obj[head], tail.join('.'));
  else delete obj[head];
}

/**
 * Helper to strip fields from an array or an object
 */
function stripPayload(payload, fields) {
  // We need to mutate a copy to avoid side-effects on shared references, 
  // but for performance in middleware, a deep clone is safer but slower.
  // Using JSON parse/stringify is a quick deep clone hack.
  let cleanPayload = payload;
  try {
    cleanPayload = JSON.parse(JSON.stringify(payload));
  } catch (e) {
    return payload; // Fallback if not serializable
  }

  if (Array.isArray(cleanPayload)) {
    cleanPayload.forEach(item => {
      fields.forEach(field => deleteNestedPath(item, field));
    });
  } else {
    fields.forEach(field => deleteNestedPath(cleanPayload, field));
  }

  return cleanPayload;
}

/**
 * Creates the express middleware
 * @param {Object} options 
 * @returns {Function} Express middleware function
 */
function adaptiveGateway(options = {}) {
  const config = { ...DEFAULT_OPTIONS, ...options };

  return function(req, res, next) {
    // 1. Detect Network Quality
    let isDegraded = config.simulateDegraded;

    if (!isDegraded) {
      const ect = (req.get('ECT') || '').trim().toLowerCase();
      const rttRaw = req.get('X-Client-RTT');
      const saveData = (req.get('Save-Data') || '').trim().toLowerCase();

      const rtt = rttRaw && rttRaw.trim() ? Number(rttRaw) : NaN;
      if (Number.isFinite(rtt) && rtt >= 0) {
        isDegraded = rtt >= config.rttDegradedThresholdMs;
      } else if (['4g', '3g', '2g', 'slow-2g'].includes(ect)) {
        isDegraded = ect !== '4g';
      } else {
        isDegraded = saveData === 'on';
      }
    }

    // Attach to request so other routes can read it if they want
    req.networkQuality = isDegraded ? 'DEGRADED' : 'GOOD';

    // 2. Intercept res.json() to modify the payload
    if (isDegraded && config.stripFields && config.stripFields.length > 0) {
      const originalJson = res.json;
      res.json = function(body) {
        const optimizedBody = stripPayload(body, config.stripFields);
        res.set('X-Adaptive-Optimized', 'true');
        return originalJson.call(this, optimizedBody);
      };
    }

    next();
  };
}

module.exports = adaptiveGateway;
