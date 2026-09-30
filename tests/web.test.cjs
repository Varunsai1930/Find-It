// Exercise the shipped script with controllable requests, without a browser dependency.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { runInNewContext } = require('node:vm');
const script = readFileSync('findit/web/static/dashboard.js', 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(id = '') {
    this.id = id;
    this.value = '';
    this.hidden = true;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.dataset = {};
  }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  emit(name, extra = {}) {
    for (const fn of this.listeners[name] || []) fn({ target: this, preventDefault() {}, ...extra });
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  getAttribute(key) { return this.attributes[key] ?? null; }
  removeAttribute(key) { delete this.attributes[key]; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  querySelector() { return null; }
  querySelectorAll(selector) {
    return selector === 'a' ? this.children : this.children.filter(n => n.attributes?.role === 'option');
  }
  contains() { return false; }
  focus() {}
  scrollIntoView() {}
}

function boot({ schemes = false, controls = false, hash = '' } = {}) {
  const ids = ['month-view', 'view-status', 'stock-dialog', 'stock-box', 'stock-dialog-close',
    'month-select', 'stock-form', 'stock-search', 'stock-options', 'stock-search-status', 'section-nav'];
  if (schemes) ids.push('scheme-data', 'scheme-search', 'scheme-options', 'scheme-search-status',
    'summary-box', 'summary-form');
  if (controls) ids.push('view-controls', 'equity-only', 'active-only');
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
  const navLinks = ['#month-view', '#scheme-summary', '#notes-title'].map(href => {
    const link = new Element(); link.setAttribute('href', href); return link;
  });
  elements['section-nav'].append(...navLinks);
  const location = { href: 'http://localhost/' + hash };
  const window = new Element();
  elements['month-select'].value = '2026-08';
  elements['month-select'].options = [{ text: 'Aug 2026' }];
  elements['month-select'].selectedIndex = 0;
  if (schemes) elements['scheme-data'].textContent = JSON.stringify([
    { id: 1, label: 'Test Fund', amc: 'Test AMC', votes: true }
  ]);
  const timers = new Map(), requests = [];
  let timerId = 0;
  runInNewContext(script, {
    document: {
      getElementById: id => elements[id] || null,
      createElement: () => new Element(),
      addEventListener() {},
    },
    location, window, history: { replaceState(_state, _title, url) { location.href = String(url); } },
    URL, URLSearchParams, AbortController, DOMException,
    FormData: class { get() { return null; } },
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, { signal }) {
      return new Promise(resolve => requests.push({ url, signal,
        respond(body) { resolve({ ok: true, status: 200, text: async () => body }); }
      }));
    },
  });
  return { elements, requests, navLinks, location, window, async type(id, value) {
    elements[id].value = value;
    elements[id].emit('input');
    for (const [id, fn] of timers) { timers.delete(id); fn(); }
    await settle();
  } };
}

test('navigation underline follows the loaded hash and later section changes', () => {
  const ui = boot({ hash: '#scheme-summary' });
  assert.equal(ui.navLinks[1].getAttribute('aria-current'), 'location');
  assert.equal(ui.navLinks[0].getAttribute('aria-current'), null);
  ui.location.href = 'http://localhost/#notes-title';
  ui.window.emit('hashchange');
  assert.equal(ui.navLinks[2].getAttribute('aria-current'), 'location');
  assert.equal(ui.navLinks[1].getAttribute('aria-current'), null);
});

test('refreshing the month preserves the selected section in the URL', async () => {
  const ui = boot({ controls: true, hash: '#scheme-summary' });
  ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
  ui.requests[0].respond('<section>New month</section>');
  await settle();
  assert.equal(new URL(ui.location.href).hash, '#scheme-summary');
  assert.equal(new URL(ui.location.href).searchParams.get('month'), '2026-08');
});
const stockResponse = name => JSON.stringify({ results: [{ name, isin: 'INE002A01018' }] });

for (const action of ['blur', 'input']) {
  test(`pending autocomplete cannot reopen after ${action}`, async () => {
    const ui = boot();
    await ui.type('stock-search', 'rel');
    assert.equal(ui.requests.length, 1);
    // A keystroke invalidates the previous request immediately, before debounce.
    ui.elements['stock-search'].emit(action);
    ui.requests[0].respond(stockResponse('Reliance'));
    await settle();
    assert.equal(ui.elements['stock-options'].hidden, true);
    assert.equal(ui.elements['stock-search'].attributes['aria-expanded'], 'false');
  });
}

test('newer autocomplete wins when the earlier request completes last', async () => {
  const ui = boot();
  await ui.type('stock-search', 'rel');
  await ui.type('stock-search', 'infos');
  ui.requests[1].respond(stockResponse('Infosys'));
  await settle();
  ui.requests[0].respond(stockResponse('Reliance'));
  await settle();
  assert.equal(ui.elements['stock-options'].children[0].children[0].textContent, 'Infosys');
});

async function pickScheme(ui) {
  await ui.type('scheme-search', 'Test');
  ui.elements['scheme-options'].children[0].emit('click');
  await settle();
}

test('editing a scheme clears and cancels its pending summary', async () => {
  const ui = boot({ schemes: true });
  await pickScheme(ui);
  ui.elements['scheme-search'].emit('input');
  assert.equal(ui.requests[0].signal.aborted, true);
  ui.requests[0].respond(JSON.stringify({ has_data: true, summary: 'Old summary' }));
  await settle();
  assert.match(ui.elements['summary-box'].children[0].textContent, /Pick a scheme/);
});

test('changing month refreshes a selected scheme while its summary is still loading', async () => {
  const ui = boot({ schemes: true, controls: true });
  await pickScheme(ui);
  ui.elements['month-select'].value = '2026-07';
  ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
  await settle();
  const summaries = ui.requests.filter(r => r.url.startsWith('/api/summary/'));
  assert.equal(summaries.length, 2);
  assert.equal(summaries[1].url, '/api/summary/1/2026-07');
  assert.equal(summaries[0].signal.aborted, true);
  summaries[1].respond(JSON.stringify({ has_data: false, summary: 'No comparison in July' }));
  await settle();
  summaries[0].respond(JSON.stringify({ has_data: true, summary: 'August summary' }));
  await settle();
  assert.equal(ui.elements['summary-box'].children[0].textContent, 'No comparison in July');
});
