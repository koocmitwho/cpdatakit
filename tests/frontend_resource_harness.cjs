// Execute the shipped browser logic with a small DOM/fetch boundary; no UI library is replaced.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Node {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.listeners = {};
    this.value = ''; this.textContent = ''; this.hidden = false;
  }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  prepend(node) { node.parent = this; this.children.unshift(node); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  async click() { await this.listeners.click?.({currentTarget: this, target: this}); }
  cloneNode() { const copy = new Node(this.tagName); Object.assign(copy, this, {children: [...this.children]}); return copy; }
  querySelectorAll(selector) {
    const all = this.children.flatMap(node => [node, ...node.querySelectorAll('*')]);
    if (selector === '*') return all;
    if (selector === 'option') return all.filter(node => node.tagName === 'option');
    const match = selector.match(/^\[data-([a-z-]+)(?:="(.*)")?\]$/);
    if (match) {
      const key = match[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      return all.filter(node => node.dataset[key] !== undefined && (!match[2] || node.dataset[key] === match[2]));
    }
    return all.filter(node => node.tagName === selector);
  }
  querySelector(selector) {
    if (selector === 'optgroup:last-child') return this.children.filter(node => node.tagName === 'optgroup').at(-1);
    if (selector === 'option:checked') return this.querySelectorAll('option').find(node => String(node.value) === String(this.value));
    return this.querySelectorAll(selector)[0] || null;
  }
  get options() { return this.querySelectorAll('option'); }
  get selectedOptions() { return this.options.filter(node => String(node.value) === String(this.value)); }
}

const kinds = ['datasets', 'artifacts', 'schemas', 'jobs'];
function page(overrides = {}, offset = 0, counts = {}) {
  const value = {project: {id: 1}, datasets: [], artifacts: [], schemas: [], jobs: [], ...overrides};
  const totals = Object.fromEntries(kinds.map(kind => [kind, counts[kind] ?? value[kind].length]));
  value.pagination = {limit: 50, offset, counts: totals,
    has_more: Object.fromEntries(kinds.map(kind => [kind, offset + value[kind].length < totals[kind]]))};
  return value;
}

function environment(initial, responder) {
  const nodes = new Map();
  for (const id of ['dataset', 'schema', 'artifacts', 'jobs', 'operation-result', 'result-title', 'result-content',
                    'project-resources', 'slice-image', 'slice-caption', 'slice-download', 'slice-preview']) {
    nodes.set('#' + id, new Node(['dataset', 'schema'].includes(id) ? 'select' : 'div'));
  }
  const schemaGroup = new Node('optgroup'); nodes.get('#schema').append(schemaGroup);
  nodes.get('#project-resources').textContent = JSON.stringify(initial);
  for (const item of initial.datasets) {
    const option = new Node('option'); option.value = String(item.id); option.textContent = item.relative_path;
    nodes.get('#dataset').append(option);
  }
  nodes.get('#dataset').value = String(initial.datasets[0]?.id || '');
  for (const job of initial.jobs) {
    const row = new Node('p'); row.dataset.jobId = job.id; row.dataset.jobStatus = job.status;
    nodes.get('#jobs').append(row);
  }
  const controls = kinds.map(kind => {
    const button = new Node('button'); button.dataset.loadMore = kind;
    nodes.set(`[data-load-more="${kind}"]`, button);
    nodes.set(`[data-resource-count="${kind}"]`, new Node('p'));
    return button;
  });
  const calls = [];
  const document = {
    body: {dataset: {projectId: '1'}},
    querySelector: selector => nodes.get(selector) || null,
    querySelectorAll: selector => {
      if (selector === '[data-load-more]') return controls;
      if (selector === '[data-job-id]') return nodes.get('#jobs').children;
      return [];
    },
    createElement: tag => new Node(tag), addEventListener() {}, dispatchEvent() {},
  };
  const context = vm.createContext({document, Event: class {}, FormData: class {}, CSS: {escape: value => value},
    setTimeout, clearTimeout, console, URLSearchParams,
    setupAuthoring() {}, setupFields() {},
    fetch: async (url, options) => { calls.push(url); return {ok: true, json: async () => responder(url, options)}; },
  });
  const directory = path.join(__dirname, '../src/cpdatakit/web/static');
  const fields = fs.readFileSync(path.join(directory, 'fields.js'), 'utf8').replace(/^export /gm, '');
  vm.runInContext(fields, context);
  const app = fs.readFileSync(path.join(directory, 'app.js'), 'utf8').replace(/^import .*;\r?\n/gm, '');
  vm.runInContext(app, context);
  return {nodes, calls, context, run: script => vm.runInContext(script, context)};
}

async function check(name) {
  if (name === 'active-history') {
    const active = {id: 'long', operation: 'report', status: 'running', active: true};
    const history = Array.from({length: 50}, (_, i) => ({id: `old-${i}`, status: 'succeeded'}));
    let finish, completed = false;
    const pending = new Promise(resolve => { finish = resolve; });
    const env = environment(page({jobs: history, active_jobs: [active]}, 0, {jobs: 62}), url => {
      if (url === '/api/jobs/long') return pending;
      if (url === '/api/jobs/long/cancel') return {status: 'running'};
      if (url.includes('offset=50')) return page({jobs: [active], active_jobs: [active]}, 50, {jobs: 62});
      return page({jobs: history, active_jobs: completed ? [] : [active]}, 0, {jobs: 62});
    });
    const rows = () => env.nodes.get('#jobs').querySelectorAll('[data-job-id="long"]');
    assert.equal(rows().length, 1, 'A fresh page must display an active job outside the history page');
    await rows()[0].querySelector('button').click();
    assert.ok(env.calls.includes('/api/jobs/long/cancel'));
    await env.run('refreshResources()');
    await env.nodes.get('[data-load-more="jobs"]').click();
    assert.equal(rows().length, 1, 'History and active rows must be deduplicated');
    assert.equal(env.calls.filter(url => url === '/api/jobs/long').length, 1);
    completed = true;
    finish({...active, status: 'cancelled', active: false});
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.equal(rows().length, 1);
    assert.equal(rows()[0].dataset.jobStatus, 'cancelled');
    assert.equal(env.run('watching.size'), 0, 'Completion must stop polling even outside the current history page');
  } else if (name === 'terminal-refresh-race') {
    const active = {id: 'long', operation: 'report', status: 'running', active: true};
    let releaseDetail, releasePage, pageEntered;
    const detail = new Promise(resolve => { releaseDetail = resolve; });
    const pageReady = new Promise(resolve => { pageEntered = resolve; });
    const pendingPage = new Promise(resolve => { releasePage = resolve; });
    const env = environment(page(), url => {
      if (url === '/api/jobs/long') return detail;
      pageEntered(); return pendingPage;
    });
    const following = env.run('followJob("long")');
    const refreshing = env.run('queueResourceRefresh()');
    await pageReady;
    releaseDetail({...active, status: 'succeeded', active: false});
    await new Promise(resolve => setImmediate(resolve));
    const row = () => env.nodes.get('#jobs').querySelector('[data-job-id="long"]');
    assert.equal(row().dataset.jobStatus, 'succeeded', 'The detail response establishes the terminal status');
    releasePage(page({jobs: [active], active_jobs: [active]}));
    await Promise.all([following, refreshing]);
    assert.equal(row().dataset.jobStatus, 'succeeded', 'An older active snapshot cannot regress a terminal row');
    assert.equal(row().querySelector('button').textContent, 'View details');
    assert.equal(env.run('watching.size'), 0);
    env.run('renderJobs()');
    assert.equal(row().dataset.jobStatus, 'succeeded', 'Re-rendering must retain the observed terminal status');
    assert.equal(env.calls.filter(url => url === '/api/jobs/long').length, 1, 'A terminal job must not restart polling');
  } else if (name === 'stale-running') {
    const stale = {id: 'old-session', operation: 'report', status: 'running', active: false};
    const env = environment(page({jobs: [stale], active_jobs: []}), () => ({...stale, status: 'failed'}));
    assert.deepEqual(env.calls, [], 'Persisted running rows must not start live polling');
    const button = env.nodes.get('#jobs').children[0].querySelector('button');
    assert.equal(button.textContent, 'View details');
    await button.click();
    assert.deepEqual(env.calls, ['/api/jobs/old-session']);
  } else if (name === 'historical') {
    const jobs = Array.from({length: 50}, (_, i) => ({id: `old-${i}`, operation: 'convert', status: 'succeeded'}));
    const env = environment(page({jobs}), url => ({...jobs[0], result: {message: 'Loaded details'}}));
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.deepEqual(env.calls, [], 'Opening project must not poll historical completed jobs');
    const button = env.nodes.get('#jobs').children[0].querySelector('button');
    assert.ok(button, 'Terminal job should offer on-demand details');
    await button.click();
    assert.deepEqual(env.calls, ['/api/jobs/old-0']);
  } else if (name === 'selection') {
    const initial = page({datasets: [{id: 1, relative_path: 'old.csv'}]});
    const recent = page({datasets: [{id: 100, relative_path: 'new.csv'}]}, 0, {datasets: 100});
    const env = environment(initial, () => recent);
    await env.run('refreshResources()');
    assert.equal(env.nodes.get('#dataset').value, '1');
    assert.ok(env.nodes.get('#dataset').options.some(option => String(option.value) === '1'));
    assert.match(env.calls[0], /limit=50/);
    assert.match(env.calls[0], /newest_first=true/);
  } else if (name === 'load-more') {
    const first = Array.from({length: 50}, (_, i) => ({id: 100-i, relative_path: `data-${100-i}.csv`}));
    const next = page({datasets: [{id: 50, relative_path: 'data-50.csv'}]}, 50, {datasets: 51});
    const env = environment(page({datasets: first}, 0, {datasets: 51}), () => next);
    const button = env.nodes.get('[data-load-more="datasets"]');
    await button.click();
    assert.match(env.calls[0] || '', /offset=50/);
    assert.equal(env.nodes.get('#dataset').options.length, 51);
    assert.equal(button.hidden, true);
  } else if (name === 'slice') {
    const env = environment(page(), () => page());
    const result = {operation: 'plot_scientific_slice', status: 'succeeded', provenance: {artifact_id: 99},
      artifact: 'results/old.png', value: {variable: 'temperature', unit: 'K', slice: {}, shape: [2, 2], cmap: 'viridis'}};
    env.context.result = result;
    env.run('showSliceResult(result, {project: {id: 1}, artifacts: []})');
    assert.equal(env.nodes.get('#slice-image').src, '/api/projects/1/artifacts/99');
    assert.equal(env.nodes.get('#slice-preview').hidden, false);
  } else if (name === 'coalesce') {
    const env = environment(page(), url => url.startsWith('/api/jobs/')
      ? {id: url.split('/').at(-1), operation: 'convert', status: 'succeeded', result: {message: 'finished'}}
      : page());
    await env.run('Promise.all([followJob("new-a"), followJob("new-b")])');
    assert.equal(env.calls.filter(url => url.startsWith('/api/projects/')).length, 1,
      'Simultaneous completions should share one bounded resource refresh');
  } else if (name === 'stale-page') {
    const first = Array.from({length: 50}, (_, i) => ({id: 100-i, relative_path: `data-${100-i}.csv`}));
    const latest = Array.from({length: 50}, (_, i) => ({id: 200-i, relative_path: `data-${200-i}.csv`}));
    let release;
    const env = environment(page({datasets: first}, 0, {datasets: 100}), url => url.includes('offset=50')
      ? new Promise(resolve => { release = resolve; })
      : page({datasets: latest}, 0, {datasets: 150}));
    const pending = env.nodes.get('[data-load-more="datasets"]').click();
    await Promise.resolve();
    await env.run('refreshResources()');
    release(page({datasets: [{id: 50, relative_path: 'old-page.csv'}]}, 50, {datasets: 100}));
    await pending;
    assert.ok(!env.nodes.get('#dataset').options.some(option => String(option.value) === '50'));
    assert.match(env.nodes.get('[data-resource-count="datasets"]').textContent, /of 150/);
  } else if (name === 'schema-selection') {
    const env = environment(page(), () => page());
    const selected = new Node('option'); selected.value = 'schema:99'; selected.textContent = 'Thermal · 2.0 · #99';
    env.nodes.get('#schema').querySelector('optgroup:last-child').append(selected);
    env.nodes.get('#schema').value = 'schema:99';
    await env.run('refreshResources()');
    assert.equal(env.nodes.get('#schema').value, 'schema:99');
    assert.equal(env.nodes.get('#schema').selectedOptions[0].textContent, 'Thermal · 2.0 · #99');
  } else if (name === 'slice-legacy') {
    const env = environment(page(), () => page());
    env.context.result = {operation: 'plot_scientific_slice', status: 'succeeded', artifact: 'results/old.png',
      value: {variable: 'temperature', unit: 'K', slice: {}, shape: [2, 2], cmap: 'viridis'}};
    env.run('showSliceResult(result, {project: {id: 1}, artifacts: [{id: 11, relative_path: "results/old.png"}]})');
    assert.equal(env.nodes.get('#slice-image').src, '/api/projects/1/artifacts/11');
  }
}
check(process.argv[2]).catch(error => { console.error(error); process.exitCode = 1; });
