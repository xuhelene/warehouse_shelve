const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

(async () => {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {value: '', disabled: false, textContent: '',
      setAttribute() {}, reportValidity() { return true; }});
    return elements.get(id);
  };
  const tabs = ['slotting', 'picking_path'].map(module => ({id: module, dataset: {module}, setAttribute() {}}));
  const initial = {backend: 'heuristic', time_limit_s: 60, threads: 8, mip_gap: 0.005};
  const data = {slotting: {...initial}, picking_path: {...initial, heuristic_algorithm: 'VNS'}};
  vm.runInNewContext(fs.readFileSync('apps/static/settings.js', 'utf8'), {
    document: {querySelector: selector => get(selector.split('#').at(-1)), querySelectorAll: () => tabs},
    fetch: async (url, request) => {
      if (request) data[url.split('/').at(-1)] = JSON.parse(request.body);
      return {ok: true, json: async () => ({...data, version: 1, persisted: true})};
    }
  });
  await new Promise(setImmediate);
  tabs[1].onclick();
  assert.equal(get('heuristic-algorithm').value, 'VNS');
  assert.equal(get('heuristic-algorithm-field').hidden, false);
  assert.equal(get('threads-field').hidden, true);
  assert.equal(get('threads').disabled, true);
  assert.equal(get('time-limit').disabled, false);
  get('heuristic-algorithm').value = 'ALNS'; get('settings-form').oninput();
  assert(get('heuristic-algorithm-help').textContent.startsWith('ALNS'));
  await get('settings-form').onsubmit({preventDefault() {}});
  assert.equal(data.picking_path.heuristic_algorithm, 'ALNS');
  get('backend').value = 'copt'; get('backend').onchange();
  assert.equal(get('heuristic-algorithm-field').hidden, true);
  assert.equal(get('threads-field').hidden, false);
  assert.equal(get('threads').disabled, false);
  tabs[0].onclick();
  assert.equal(get('heuristic-algorithm-field').hidden, true);
  tabs[1].onclick(); get('defaults').onclick();
  assert.equal(get('heuristic-algorithm').value, 'VNS');
  console.log('Picking algorithm selection UI checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
