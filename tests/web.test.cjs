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
    this.style = { setProperty() {} };
    this.isConnected = true;
    this.focusCount = 0;
  }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  emit(name, extra = {}) {
    const event = { type: name, target: this, currentTarget: this, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; }, ...extra };
    for (const fn of this.listeners[name] || []) fn(event);
    return event;
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  getAttribute(key) { return this.attributes[key] ?? null; }
  removeAttribute(key) { delete this.attributes[key]; }
  click() { this.emit('click'); this.clicked = true; }
  remove() { this.removed = true; }
  append(...nodes) { this.children.push(...nodes); }
  get textContent() {
    return (this._textContent || '') + this.children.map(node => typeof node === 'string' ? node : node.textContent).join('');
  }
  set textContent(value) { this._textContent = value; this.children = []; }
  replaceChildren(...nodes) { this._textContent = ''; this.children = nodes; }
  querySelector(selector) {
    if (selector === '[data-follow-label]') return this.children.find(n => n.dataset?.followLabel !== undefined) || null;
    if (selector === '[data-theme-label]') return this.children.find(n => n.dataset?.themeLabel !== undefined) || null;
    return null;
  }
  querySelectorAll(selector) {
    if (selector === '[data-watch-stock]') return this.children.filter(n => n.dataset?.watchStock !== undefined);
    return selector === 'a' ? this.children : this.children.filter(n => n.attributes?.role === 'option');
  }
  contains() { return false; }
  focus() { this.focusCount++; }
  showModal() { this.open = true; }
  close() { this.open = false; if (!this.deferCloseEvent) this.emit('close'); }
  scrollIntoView() {}
  getBoundingClientRect() {
    return { left: this.left || 0, width: this.width || 0,
      top: this.top || 0, bottom: (this.top || 0) + (this.height || 0) };
  }
}

function boot({ schemes = false, controls = false, hash = '', sections = false, watch = null,
  theme = false, savedTheme = null, storageBlocked = false, coverage = false } = {}) {
  const ids = ['month-view', 'view-status', 'stock-dialog', 'stock-box', 'stock-dialog-close',
    'month-select', 'stock-form', 'stock-search', 'stock-options', 'stock-search-status', 'section-nav'];
  if (schemes) ids.push('scheme-data', 'scheme-search', 'scheme-options', 'scheme-search-status',
    'summary-box', 'summary-form');
  if (controls) ids.push('view-controls', 'equity-only', 'active-only');
  if (watch) ids.push('watchlist-status', 'watchlist-results', 'watchlist-report', 'watchlist-export', 'watchlist-import', 'active-only');
  if (theme) ids.push('theme-toggle');
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
  const navLinks = ['#monthly-summary', '#monthly-activity', '#scheme-summary', '#notes-title'].map(href => {
    const link = new Element(); link.setAttribute('href', href); return link;
  });
  elements['section-nav'].append(...navLinks);
  const sectionPositions = { 'monthly-summary': 400, 'monthly-activity': 1200,
    'scheme-summary': 3200, 'notes-title': 4600 };
  if (sections) for (const [id, top] of Object.entries(sectionPositions)) {
    elements[id] = new Element(id); elements[id].top = top;
  }
  const topbar = new Element(); topbar.height = 80;
  const coverageLink = coverage ? new Element() : null;
  if (coverageLink) coverageLink.href = 'http://localhost/coverage?month=2026-08&active_only=0';
  const documentRoot = new Element(); documentRoot.scrollHeight = 5000;
  documentRoot.dataset.theme = savedTheme || 'light';
  const location = { href: 'http://localhost/' + hash };
  const window = new Element();
  window.scrollY = 0; window.innerHeight = 800;
  elements['month-select'].value = '2026-08';
  elements['month-select'].options = [{ text: 'Aug 2026' }];
  elements['month-select'].selectedIndex = 0;
  if (schemes) elements['scheme-data'].textContent = JSON.stringify([
    { id: 1, label: 'Test Fund', amc: 'Test AMC', votes: true }
  ]);
  const timers = new Map(), requests = [], frames = [], created = [];
  const body = new Element(), head = new Element(), documentEvents = new Element(), systemTheme = new Element();
  window.matchMedia = () => systemTheme;
  const themeFlushes = [];
  Object.defineProperty(documentRoot, 'offsetHeight', { get() {
    themeFlushes.push({ theme: documentRoot.dataset.theme,
      overrides: head.children.filter(node => !node.removed) });
    return 0;
  } });
  if (theme) {
    const icon = new Element(); icon.tagName = 'SVG';
    const label = new Element(); label.tagName = 'SPAN'; label.dataset.themeLabel = '';
    elements['theme-toggle'].append(icon, label);
  }
  const saved = new Map();
  if (watch) saved.set('findit-watchlist-v1', JSON.stringify({ version: 1, stocks: watch }));
  if (savedTheme) saved.set('findit-theme', savedTheme);
  let timerId = 0;
  runInNewContext(script, {
    document: {
      getElementById: id => elements[id] || null,
      querySelector: selector => selector === '.topbar' ? topbar : selector === '[data-coverage-link]' ? coverageLink : null,
      documentElement: documentRoot, activeElement: elements['stock-search'],
      body, head,
      createElement: tag => { const element = new Element(); element.tagName = tag.toUpperCase(); created.push(element); return element; },
      addEventListener: documentEvents.addEventListener.bind(documentEvents),
    },
    location, window, history: { replaceState(_state, _title, url) { location.href = String(url); } },
    URL, URLSearchParams, AbortController, DOMException, Blob,
    localStorage: {
      getItem(key) { if (storageBlocked) throw new Error('Storage unavailable'); return saved.get(key) || null; },
      setItem(key, value) { if (storageBlocked) throw new Error('Storage unavailable'); saved.set(key, value); },
    },
    getComputedStyle(element) { return { scrollMarginTop: (element.scrollMargin || 0) + 'px' }; },
    FormData: class { get() { return null; } },
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    requestAnimationFrame(fn) { frames.push(fn); },
    fetch(url, options) {
      const { signal } = options;
      return new Promise(resolve => requests.push({ url, signal, options,
        respond(body, { ok = true, status = 200 } = {}) { resolve({ ok, status, text: async () => body }); }
      }));
    },
  });
  return { created, body, head, documentRoot, documentEvents, systemTheme, themeFlushes, saved,
    elements, requests, navLinks, location, window, frames, timers, topbar, coverageLink,
    frame() { for (const fn of frames.splice(0)) fn(); },
    runTimers() { for (const [id, fn] of timers) { timers.delete(id); fn(); } },
    scrollTo(y) {
      window.scrollY = y;
      for (const [id, top] of Object.entries(sectionPositions)) if (elements[id]) elements[id].top = top - y;
      window.emit('scroll');
    }, async type(id, value) {
    elements[id].value = value;
    elements[id].emit('input');
    for (const [id, fn] of timers) { timers.delete(id); fn(); }
    await settle();
  } };
}

test('selected navigation follows the loaded hash and later section changes', () => {
  const ui = boot({ hash: '#scheme-summary' });
  assert.equal(ui.navLinks[2].getAttribute('aria-current'), 'location');
  assert.equal(ui.navLinks[0].getAttribute('aria-current'), null);
  ui.location.href = 'http://localhost/#notes-title';
  ui.window.emit('hashchange');
  assert.equal(ui.navLinks[3].getAttribute('aria-current'), 'location');
  assert.equal(ui.navLinks[2].getAttribute('aria-current'), null);
});

test('manual scrolling tracks sections in both directions without changing the URL', () => {
  const ui = boot({ sections: true, hash: '#monthly-summary' });
  ui.frame();
  for (const [y, active] of [[1300, 1], [3400, 2], [1300, 1], [350, 0]]) {
    ui.scrollTo(y); ui.frame();
    assert.equal(ui.navLinks[active].getAttribute('aria-current'), 'location');
    assert.equal(ui.navLinks.filter(link => link.getAttribute('aria-current')).length, 1);
    assert.equal(new URL(ui.location.href).hash, '#monthly-summary');
  }
});

test('navigation accounts for sticky headers, short final sections and changed targets', () => {
  const ui = boot({ sections: true });
  ui.frame();
  ui.scrollTo(1100); ui.frame(); // Activity starts 100px down, just below the 80px header.
  assert.equal(ui.navLinks[1].getAttribute('aria-current'), 'location');
  ui.elements['monthly-activity'] = new Element('monthly-activity');
  ui.elements['monthly-activity'].top = 500; // A refreshed/expanded summary moves the target down.
  ui.window.emit('resize'); ui.frame();
  assert.equal(ui.navLinks[0].getAttribute('aria-current'), 'location');
  ui.elements['scheme-summary'].top = 180;
  ui.elements['scheme-summary'].scrollMargin = 84; // Panel margin adds to the page's anchor padding.
  ui.window.emit('hashchange'); ui.frame();
  assert.equal(ui.navLinks[2].getAttribute('aria-current'), 'location');
  ui.scrollTo(4200); ui.frame(); // Reading-guide heading cannot reach the top at document end.
  assert.equal(ui.navLinks[3].getAttribute('aria-current'), 'location');
});

test('scroll events are coalesced into one animation-frame update', () => {
  const ui = boot({ sections: true });
  ui.frame();
  ui.scrollTo(1300); ui.scrollTo(1400); ui.scrollTo(3400);
  assert.equal(ui.frames.length, 1);
  ui.frame();
  assert.equal(ui.navLinks[2].getAttribute('aria-current'), 'location');
});

test('refreshing the month preserves the selected section in the URL', async () => {
  const ui = boot({ controls: true, hash: '#scheme-summary' });
  ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
  ui.requests[0].respond('<section>New month</section>');
  await settle();
  assert.equal(new URL(ui.location.href).hash, '#scheme-summary');
  assert.equal(new URL(ui.location.href).searchParams.get('month'), '2026-08');
});

test('sidebar coverage follows the successfully displayed month and filters', async () => {
  const ui = boot({ controls: true, coverage: true, hash: '#monthly-activity' });
  const original = ui.coverageLink.href;
  ui.elements['month-select'].value = '2026-07';
  ui.elements['active-only'].checked = true;
  ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
  ui.requests[0].respond('Unavailable', { ok: false, status: 503 }); await settle();
  assert.equal(ui.coverageLink.href, original);
  assert.equal(new URL(ui.location.href).searchParams.has('month'), false);
  ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
  ui.requests[1].respond('July active funds'); await settle();
  const coverageUrl = new URL(ui.coverageLink.href), dashboardUrl = new URL(ui.location.href);
  assert.equal(coverageUrl.pathname, '/coverage');
  assert.equal(coverageUrl.searchParams.get('month'), '2026-07');
  assert.equal(coverageUrl.searchParams.get('active_only'), '1');
  assert.equal(dashboardUrl.searchParams.get('month'), coverageUrl.searchParams.get('month'));
  assert.equal(dashboardUrl.searchParams.get('active_only'), coverageUrl.searchParams.get('active_only'));
  assert.equal(dashboardUrl.hash, '#monthly-activity');
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
  assert.match(ui.elements['summary-box'].children[0].textContent, /Pick a fund/);
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


test('watchlist downloads pin the displayed release and clearing aborts stale work', async () => {
  const ui = boot({ watch: ['INE002A01018'], controls: true });
  const report = { release_id: 'a'.repeat(64), rule_version: 'rules-a', scope: 'Test', stocks: [
    { isin: 'INE002A01018', name: 'Reliance', evidence_url: '/evidence/test', status: 'changed',
      houses_buying: 1, houses_selling: 0, net_share_change: 10, additions: 0, exits: 0,
      largest_compared_changes: [] } ] };
  ui.requests[0].respond(JSON.stringify(report)); await settle();
  assert.equal(ui.requests[0].url, '/api/watchlist');
  assert.equal(ui.requests[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(ui.requests[0].options.body).stocks, ['INE002A01018']);
  assert.equal(ui.elements['watchlist-report'].href, '/watchlist/report');
  const pinned = JSON.parse(ui.elements['watchlist-report'].dataset.reportRequest);
  assert.equal(pinned.release, report.release_id);
  assert.equal(pinned.rules, report.rule_version);
  ui.elements['watchlist-report'].emit('click');
  assert.match(ui.elements['watchlist-status'].textContent, /Preparing/);
  const download = ui.requests.at(-1);
  assert.equal(download.url, '/watchlist/report');
  assert.equal(download.options.method, 'POST');
  assert.deepEqual(JSON.parse(download.options.body), pinned);
  download.respond('# matching release report'); await settle();
  const generated = ui.created.at(-1);
  assert.equal(generated.download, 'findit-2026-08-' + report.release_id.slice(0, 12) + '.md');
  assert.equal(generated.clicked, true);
  assert.equal(generated.removed, true);
  assert.match(ui.elements['watchlist-status'].textContent, /Report ready/);
  const row = ui.elements['watchlist-results'].children[1];
  ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] }); await settle();
  assert.equal(ui.elements['watchlist-report'].hidden, true);
  const pending = ui.requests.filter(r => r.url.startsWith('/api/watchlist')).at(-1);
  // A queued activation can target a row detached by the loading state.
  row.children.at(-1).emit('click');
  assert.equal(pending.signal.aborted, true);
  pending.respond(JSON.stringify(report)); await settle();
  assert.match(ui.elements['watchlist-results'].textContent, /empty/);
  assert.equal(ui.elements['watchlist-report'].hidden, true);
});

test('monthly reports download when the icon or its SVG path is clicked', async () => {
  for (const tagName of ['SVG', 'PATH']) {
    const ui = boot({ watch: ['INE002A01018'], controls: true });
    const report = { release_id: 'a'.repeat(64), rule_version: 'rules-a',
      scope: 'Test', market_completeness: 'unknown', stocks: [] };
    ui.requests[0].respond(JSON.stringify(report)); await settle();
    const link = ui.elements['watchlist-report'], child = new Element();
    child.tagName = tagName;
    link.append(child);
    const pinned = link.dataset.reportRequest;
    const before = ui.requests.length;
    link.emit('click', { target: child });
    assert.equal(ui.requests.length, before + 1, tagName + ' click starts a report request');
    const download = ui.requests.at(-1);
    assert.equal(download.url, '/watchlist/report');
    assert.equal(download.options.method, 'POST');
    assert.equal(download.options.body, pinned);
    download.respond('# matching release report'); await settle();
    const generated = ui.created.at(-1);
    assert.equal(generated.download, 'findit-2026-08-' + report.release_id.slice(0, 12) + '.md');
    assert.equal(generated.clicked, true);
  }
});

test('watchlist export keeps its file URL alive until the download has started', async () => {
  const { createObjectURL, revokeObjectURL } = URL;
  const blobs = [], revoked = [];
  URL.createObjectURL = blob => { blobs.push(blob); return 'blob:export-' + blobs.length; };
  URL.revokeObjectURL = url => revoked.push(url);
  try {
    const ui = boot({ watch: ['INE002A01018', 'INE040A01034'], controls: true });
    // ui.type also runs every pending timer; it is the harness's timer flush.
    await ui.type('stock-search', '');
    ui.elements['watchlist-export'].emit('click');
    const link = ui.created.at(-1);
    assert.equal(link.download, 'findit-watchlist.json');
    assert.equal(link.href, 'blob:export-1');
    assert.ok(ui.body.children.includes(link), 'link is attached before clicking');
    assert.equal(link.clicked, true);
    assert.equal(link.removed, true);
    // Revoking straight after click() cancelled the download in some browsers.
    assert.deepEqual(revoked, []);
    await ui.type('stock-search', '');
    assert.deepEqual(revoked, ['blob:export-1']);
    assert.deepEqual(JSON.parse(await blobs[0].text()),
      { version: 1, stocks: ['INE002A01018', 'INE040A01034'] });
  } finally {
    Object.assign(URL, { createObjectURL, revokeObjectURL });
  }
});

test('malformed watchlist imports preserve the existing list', async () => {
  const ui = boot({ watch: ['INE002A01018'], controls: true });
  const importer = ui.elements['watchlist-import'];
  importer.files = [{ size: 10, text: async () => '{bad json' }];
  importer.emit('change'); await settle();
  assert.match(ui.elements['watchlist-status'].textContent, /Import failed/);
  assert.equal(ui.requests.length, 1);
  importer.files = [{ size: 10, text: async () => JSON.stringify({ version: 1, stocks: ['invalid'] }) }];
  importer.emit('change'); await settle();
  assert.match(ui.elements['watchlist-status'].textContent, /valid ISINs/);
  assert.equal(ui.requests.length, 1);
});

test('keyboard stock opens immediately and Escape restores the opener and clears its URL', async () => {
  const ui = boot();
  ui.frame();
  const opener = ui.elements['stock-search'], dialog = ui.elements['stock-dialog'];
  ui.documentEvents.emit('keydown', { key: 'Enter' });
  opener.value = 'Reliance';
  ui.elements['stock-form'].emit('submit');
  assert.equal(dialog.open, true);
  assert.equal(ui.frames.length, 0); // Keyboard opening does not wait for an entrance frame.
  const heading = new Element(), stock = new Element(); stock.dataset.stockIsin = 'INE002A01018';
  ui.elements['stock-box'].querySelector = selector => selector === 'h3' ? heading : selector === '[data-stock-isin]' ? stock : null;
  ui.requests[0].respond('Reliance detail'); await settle();
  assert.equal(heading.focusCount, 1);
  assert.equal(new URL(ui.location.href).searchParams.get('stock'), stock.dataset.stockIsin);
  const escape = dialog.emit('cancel');
  assert.equal(escape.defaultPrevented, true);
  assert.equal(dialog.open, false);
  assert.equal(ui.timers.size, 0);
  assert.equal(opener.focusCount, 1);
  assert.equal(new URL(ui.location.href).searchParams.has('stock'), false);
});

test('pointer stock exit retains the dialog and reopening cancels the exit and stale response', async () => {
  const ui = boot();
  ui.frame();
  const dialog = ui.elements['stock-dialog'], opener = ui.elements['stock-search'];
  opener.value = 'First stock';
  ui.documentEvents.emit('pointerdown');
  ui.elements['stock-form'].emit('submit'); ui.frame();
  ui.elements['stock-dialog-close'].emit('click', { detail: 1 });
  assert.equal(dialog.open, true); // Native focus containment lasts through the exit.
  assert.equal(opener.focusCount, 0);
  assert.equal(ui.requests[0].signal.aborted, true);
  opener.value = 'Second stock';
  ui.elements['stock-form'].emit('submit');
  ui.runTimers();
  assert.equal(dialog.open, true);
  ui.requests[1].respond('Second stock detail'); await settle();
  ui.requests[0].respond('Stale first stock detail'); await settle();
  assert.equal(ui.elements['stock-box'].innerHTML, 'Second stock detail');
  ui.elements['stock-dialog-close'].emit('click', { detail: 1 });
  dialog.emit('transitionend', { propertyName: 'opacity' });
  assert.equal(dialog.open, false);
  assert.equal(ui.timers.size, 0);
  assert.equal(opener.focusCount, 1);
});

test('stock close completes without a transition event and ignores an older queued native close', () => {
  const ui = boot();
  const dialog = ui.elements['stock-dialog'];
  ui.elements['stock-search'].value = 'Reliance';
  ui.elements['stock-form'].emit('submit'); ui.frame();
  ui.elements['stock-dialog-close'].emit('click', { detail: 1 });
  ui.runTimers();
  assert.equal(dialog.open, false); // Background tabs or missing CSS cannot trap the dialog.
  dialog.deferCloseEvent = true;
  ui.documentEvents.emit('keydown', { key: 'Enter' });
  ui.elements['stock-form'].emit('submit');
  dialog.emit('cancel');
  ui.elements['stock-form'].emit('submit');
  const reopened = ui.requests.at(-1);
  dialog.emit('close'); // Browser queued this for the previous dialog instance.
  assert.equal(dialog.open, true);
  assert.equal(reopened.signal.aborted, false);
});

test('theme swaps preserve the icon and action label and restore transitions after paint', () => {
  const ui = boot({ theme: true });
  ui.frame();
  const button = ui.elements['theme-toggle'], icon = button.children[0], label = button.children[1];
  assert.equal(button.getAttribute('aria-label'), 'Switch to dark mode');
  button.emit('click');
  assert.equal(ui.documentRoot.dataset.theme, 'dark');
  assert.equal(ui.saved.get('findit-theme'), 'dark');
  assert.equal(button.children[0], icon);
  assert.equal(label.textContent, 'Light mode');
  assert.equal(button.getAttribute('aria-label'), 'Switch to light mode');
  assert.equal(ui.themeFlushes.at(-1).theme, 'dark');
  assert.equal(ui.themeFlushes.at(-1).overrides.length, 1);
  assert.match(ui.themeFlushes.at(-1).overrides[0].textContent, /transition\s*:\s*none/);
  ui.frame();
  assert.equal(ui.head.children.filter(node => !node.removed).length, 1);
  button.emit('click'); // A second swap before cleanup keeps both palette changes immediate.
  assert.equal(ui.documentRoot.dataset.theme, 'light');
  assert.equal(label.textContent, 'Dark mode');
  ui.frame(); ui.frame();
  assert.equal(ui.head.children.filter(node => !node.removed).length, 0);
  assert.equal(button.children[0], icon);
});

test('system appearance follows until a manual choice even when device storage is unavailable', () => {
  const ui = boot({ theme: true, storageBlocked: true });
  ui.systemTheme.emit('change', { matches: true });
  assert.equal(ui.documentRoot.dataset.theme, 'dark');
  ui.elements['theme-toggle'].emit('click');
  assert.equal(ui.documentRoot.dataset.theme, 'light');
  ui.systemTheme.emit('change', { matches: true });
  assert.equal(ui.documentRoot.dataset.theme, 'light');
  ui.frame(); ui.frame();
  assert.equal(ui.head.children.filter(node => !node.removed).length, 0);
});

test('follow toggle keeps its icons, shows the loaded state and unfollows from either place', async () => {
  const ui = boot({ watch: ['INE002A01018'], controls: true });
  const report = { release_id: 'a'.repeat(64), rule_version: 'rules-a', scope: 'Test',
    market_completeness: 'unknown', stocks: [
    { isin: 'INE002A01018', name: 'Reliance', evidence_url: '/evidence/test', status: 'changed',
      houses_buying: 1, houses_selling: 0, net_share_change: 10, additions: 0, exits: 0,
      largest_compared_changes: [], review_status: 'not_independently_reviewed' } ] };
  ui.requests[0].respond(JSON.stringify(report)); await settle();
  const item = ui.elements['watchlist-results'].children[1];
  assert.match(item.textContent, /Reliance/);
  assert.match(item.textContent, /\+10/);
  assert.match(ui.elements['watchlist-results'].children[0].textContent, /completeness: unknown\..*Independent review: pending/);
  ui.elements['stock-search'].value = 'Reliance';
  ui.elements['stock-form'].emit('submit');
  const button = new Element(); button.dataset.watchStock = 'INE002A01018';
  const icons = new Element(), label = new Element(); label.dataset.followLabel = ''; button.append(icons, label);
  button.closest = selector => selector === '[data-watch-stock]' ? button : null;
  ui.elements['stock-box'].append(button);
  ui.requests.at(-1).respond('Reliance detail'); await settle();
  assert.equal(button.getAttribute('aria-pressed'), 'true');
  assert.equal(label.textContent, 'Following on this device');
  assert.equal(button.children[0], icons);
  item.children.at(-1).emit('click');
  assert.equal(button.getAttribute('aria-pressed'), 'false');
  assert.equal(label.textContent, 'Follow stock on this device');
  assert.equal(button.children[0], icons);
  ui.documentEvents.emit('click', { target: button });
  assert.equal(button.getAttribute('aria-pressed'), 'true');
  assert.deepEqual(JSON.parse(ui.saved.get('findit-watchlist-v1')).stocks, ['INE002A01018']);
  ui.documentEvents.emit('click', { target: button });
  assert.equal(button.getAttribute('aria-pressed'), 'false');
  assert.deepEqual(JSON.parse(ui.saved.get('findit-watchlist-v1')).stocks, []);
});
