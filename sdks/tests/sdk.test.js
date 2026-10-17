const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createAdaptiveFetch } = require('../client-js');
const adaptiveGateway = require('../express-middleware');

test('fetch wrappers preserve Request credentials and isolate RTT between gateways', async () => {
  const original = global.fetch;
  const calls = [];
  global.fetch = async (input, init) => {
    calls.push({ input, init });
    return new Response('{}');
  };
  try {
    const first = createAdaptiveFetch({ gatewayUrl: 'http://first.test' });
    const second = createAdaptiveFetch({ gatewayUrl: 'http://second.test', autoPing: false });
    await first('/proxy/service/x');
    await new Promise(resolve => setImmediate(resolve));
    await first('/proxy/service/x');
    const request = new Request('http://second.test/x', { headers: { Authorization: 'Bearer test' } });
    await second(request);
    assert.equal(calls.filter(call => call.init.method === 'HEAD').length, 1);
    assert.equal(calls[0].input, 'http://first.test/health');
    assert.equal(calls[1].input, 'http://first.test/proxy/service/x');
    assert.ok(calls[2].init.headers.has('X-Client-RTT'));
    assert.equal(calls[3].init.headers.get('Authorization'), 'Bearer test');
    assert.equal(calls[3].init.headers.get('X-Client-RTT'), null);
  } finally { global.fetch = original; }
});

test('Express hints validate complete RTT values and preserve explicit precedence', () => {
  for (const [headers, expected] of [
    [{ 'X-Client-RTT': '0', ECT: '2g' }, 'GOOD'],
    [{ 'X-Client-RTT': 'NaN', ECT: ' 3G ' }, 'DEGRADED'],
    [{ 'X-Client-RTT': '150ms' }, 'GOOD'],
    [{ 'X-Client-RTT': '-10', 'Save-Data': 'ON' }, 'DEGRADED'],
    [{ 'X-Client-RTT': '150' }, 'DEGRADED'],
  ]) {
    const request = { get: name => headers[name] };
    adaptiveGateway()(request, {}, () => {});
    assert.equal(request.networkQuality, expected);
  }
});

test('Express removes nested configured fields without mutating the source', () => {
  const original = { data: [{ debug: 'remove', title: 'keep' }] };
  let output;
  const response = { json: body => { output = body; }, set: () => {} };
  adaptiveGateway({ simulateDegraded: true, stripFields: ['data.debug'] })(
    { get: () => undefined }, response, () => {}
  );
  response.json(original);
  assert.deepEqual(output, { data: [{ title: 'keep' }] });
  assert.equal(original.data[0].debug, 'remove');
});
