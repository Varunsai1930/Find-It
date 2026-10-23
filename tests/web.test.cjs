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
  select() { this.selected = true; }
  showModal() { this.open = true; }
  close() { this.open = false; if (!this.deferCloseEvent) this.emit('close'); }
  scrollIntoView() {}
  getBoundingClientRect() {
    return { left: this.left || 0, width: this.width || 0,
      top: this.top || 0, bottom: (this.top || 0) + (this.height || 0) };
  }
}

function boot({ schemes = false, controls = false, hash = '', watch = null, search = false, guide = false,
  theme = false, savedTheme = null, storageBlocked = false, coverage = false, notable = false,
  savedNotable = null } = {}) {
  const ids = ['month-view', 'view-status', 'stock-dialog', 'stock-box', 'stock-dialog-close',
    'month-select', 'stock-form', 'stock-search', 'stock-options', 'stock-search-status', 'section-nav'];
  if (schemes && schemes !== 'lazy') ids.push('scheme-data');
  if (schemes) ids.push('scheme-search', 'scheme-options', 'scheme-search-status',
    'summary-box', 'summary-form');
  if (controls) ids.push('view-controls', 'equity-only', 'active-only');
  if (watch) ids.push('watchlist-status', 'watchlist-results', 'watchlist-report', 'watchlist-export', 'watchlist-import', 'active-only');
  if (theme) ids.push('theme-toggle');
  if (search) ids.push('search-dialog', 'search-open');
  if (guide) ids.push('guide-dialog', 'guide-box', 'guide-notes', 'guide-dialog-close');
  if (notable) ids.push('notable');
  ids.push('page-title', 'page-description');
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
  const views = [['#monthly-activity', 'activity', 'Monthly fund activity'],
    ['#monthly-summary', 'summary', 'Monthly summary'], ['#watchlist', 'watchlist', 'My watchlist'],
    ['#scheme-summary', 'funds', 'Fund summary']];
  const navLinks = views.map(([href, view, title]) => {
    const link = new Element(); link.setAttribute('href', href);
    Object.assign(link.dataset, { view, title, description: title + ' description' });
    link.closest = selector => selector === "a[href^='#']" ? link : null;
    return link;
  });
  elements['section-nav'].append(...navLinks);
  if (notable) {
    // Five cards in a row narrower than they are: the last one is cut off.
    const grid = Object.assign(new Element(), { scrollLeft: 0, clientWidth: 958, scrollWidth: 1204 });
    elements.notable.grid = grid;
    elements.notable.querySelector = selector => selector === '.notable__grid' ? grid : null;
  }
  const coverageLink = coverage ? new Element() : null;
  if (coverageLink) coverageLink.href = 'http://localhost/coverage?month=2026-08&active_only=0';
  const documentRoot = new Element(); documentRoot.scrollHeight = 5000;
  documentRoot.dataset.theme = savedTheme || 'light';
  const location = { href: 'http://localhost/' + hash };
  const window = new Element();
  window.scrollY = 0; window.innerHeight = 800;
  elements['month-select'].value = '2026-08';
  elements['month-select'].options = [
    { value: '2026-08', text: 'Aug 2026' }, { value: '2026-07', text: 'Jul 2026' }
  ];
  Object.defineProperty(elements['month-select'], 'selectedIndex', { get() {
    return this.options.findIndex(option => option.value === this.value);
  } });
  if (controls) {
    const fields = { month: elements['month-select'] };
    const params = new URL(location.href).searchParams;
    for (const [name, fallback] of Object.entries({ side: 'buy', limit: '25', cols: 'core', page: '1' })) {
      fields[name] = new Element(); fields[name].name = name;
      fields[name].value = params.get(name) || fallback;
    }
    fields.limit.options = ['25', '50', '0'].map(value => ({ value }));
    fields.month.value = params.get('month') || '2026-08';
    elements['view-controls'].elements = fields;
    elements['equity-only'].checked = params.get('equity_only') !== '0';
    elements['active-only'].checked = params.get('active_only') !== '0';
  }
  if (schemes && schemes !== 'lazy') elements['scheme-data'].textContent = JSON.stringify(Array.isArray(schemes) ? schemes : [
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
  if (savedNotable) saved.set('findit-notable-open', savedNotable);
  let timerId = 0;
  const pushes = [], scrolls = [];
  window.scrollTo = (x, y) => scrolls.push([x, y]);
  runInNewContext(script, {
    document: {
      getElementById: id => elements[id] || null,
      querySelector: selector => selector === '[data-coverage-link]' ? coverageLink : null,
      documentElement: documentRoot, activeElement: elements['stock-search'],
      body, head,
      createElement: tag => { const element = new Element(); element.tagName = tag.toUpperCase(); created.push(element); return element; },
      addEventListener: documentEvents.addEventListener.bind(documentEvents),
    },
    location, window, history: {
      replaceState(_state, _title, url) { location.href = String(url); },
      pushState(_state, _title, url) { location.href = String(url); pushes.push(String(url)); },
    },
    URL, URLSearchParams, AbortController, DOMException, Blob,
    localStorage: {
      getItem(key) { if (storageBlocked) throw new Error('Storage unavailable'); return saved.get(key) || null; },
      setItem(key, value) { if (storageBlocked) throw new Error('Storage unavailable'); saved.set(key, value); },
    },
    FormData: class {
      constructor(form) { this.form = form; }
      get(name) { return this.form?.elements[name]?.value ?? null; }
    },
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
    elements, requests, navLinks, location, window, frames, timers, coverageLink, pushes, scrolls,
    frame() { for (const fn of frames.splice(0)) fn(); },
    runTimers() { for (const [id, fn] of timers) { timers.delete(id); fn(); } },
     async type(id, value) {
    elements[id].value = value;
    elements[id].emit('input');
    for (const [id, fn] of timers) { timers.delete(id); fn(); }
    await settle();
  } };
}

const stockResponse = name => JSON.stringify({ results: [{ name, isin: 'INE002A01018' }] });
const current = ui => ui.navLinks.filter(link => link.getAttribute('aria-current') === 'page');
const kinds = ui => ui.elements['stock-options'].children
  .filter(option => option.attributes.role === 'option' && !option.attributes['aria-disabled'])
  .map(option => option.children.at(-1).textContent);

test('the loaded hash and later hash changes choose one view and its heading', () => {
  const ui = boot({ hash: '#scheme-summary' });
  assert.equal(ui.body.dataset.currentView, 'funds');
  assert.deepEqual(current(ui), [ui.navLinks[3]]);
  assert.equal(ui.elements['page-title'].textContent, 'Fund summary');
  assert.equal(ui.elements['page-description'].textContent, 'Fund summary description');
  ui.location.href = 'http://localhost/#monthly-summary';
  ui.window.emit('hashchange');
  assert.equal(ui.body.dataset.currentView, 'summary');
  assert.deepEqual(current(ui), [ui.navLinks[1]]);
  assert.deepEqual(ui.scrolls.at(-1), [0, 0]); // A view starts from the top.
});

test('a hash outside the views keeps the first view without scrolling', () => {
  const ui = boot({ hash: '#consensus' });
  assert.equal(ui.body.dataset.currentView, 'activity');
  assert.deepEqual(current(ui), [ui.navLinks[0]]);
  ui.location.href = 'http://localhost/#coverage-details';
  ui.window.emit('hashchange');
  assert.equal(ui.body.dataset.currentView, 'activity');
  assert.equal(ui.scrolls.length, 0);
});

test('a view link switches in place and records one history entry', () => {
  const ui = boot();
  const click = ui.documentEvents.emit('click', { target: ui.navLinks[2] });
  assert.equal(click.defaultPrevented, true);
  assert.equal(ui.body.dataset.currentView, 'watchlist');
  assert.deepEqual(ui.pushes, ['http://localhost/#watchlist']);
  ui.documentEvents.emit('click', { target: ui.navLinks[2] });
  assert.equal(ui.pushes.length, 1); // The current view adds no entry.
  // Back and forward restore the view from the URL.
  ui.location.href = 'http://localhost/#monthly-activity';
  ui.window.emit('popstate');
  assert.equal(ui.body.dataset.currentView, 'activity');
});

test('a modified click on a view link keeps the browser default', () => {
  const ui = boot();
  for (const modifier of ['metaKey', 'ctrlKey', 'shiftKey']) {
    const click = ui.documentEvents.emit('click', { target: ui.navLinks[1], [modifier]: true });
    assert.equal(click.defaultPrevented, false, modifier);
  }
  assert.equal(ui.body.dataset.currentView, 'activity');
  assert.deepEqual(ui.pushes, []);
});

test('the notable lists start closed, stay open once opened and fade a cut-off edge', () => {
  const ui = boot({ notable: true });
  const panel = ui.elements.notable, grid = panel.grid;
  assert.notEqual(panel.open, true);
  assert.equal(grid.dataset.overflow.trim(), 'end');
  panel.open = true;
  panel.emit('toggle');
  assert.equal(ui.saved.get('findit-notable-open'), '1');
  grid.scrollLeft = 400;
  grid.emit('scroll');
  assert.equal(grid.dataset.overflow.trim(), 'start');
  // The next visit, or the next month rendered, opens it again.
  assert.equal(boot({ notable: true, savedNotable: '1' }).elements.notable.open, true);
  assert.notEqual(boot({ notable: true, savedNotable: '0' }).elements.notable.open, true);
  // Without device storage the panel still opens and closes; it just starts closed.
  const blocked = boot({ notable: true, storageBlocked: true });
  assert.notEqual(blocked.elements.notable.open, true);
  blocked.elements.notable.open = true;
  assert.doesNotThrow(() => blocked.elements.notable.emit('toggle'));
});

test('Back to an entry without a hash restores the default view', () => {
  const ui = boot();
  ui.documentEvents.emit('click', { target: ui.navLinks[1] });
  const forwardUrl = ui.location.href;
  ui.location.href = 'http://localhost/';
  ui.window.emit('popstate');
  assert.equal(ui.body.dataset.currentView, 'activity');
  assert.deepEqual(current(ui), [ui.navLinks[0]]);
  assert.equal(ui.elements['page-title'].textContent, 'Monthly fund activity');
  ui.location.href = forwardUrl;
  ui.window.emit('popstate');
  assert.equal(ui.body.dataset.currentView, 'summary');
  assert.equal(ui.pushes.length, 1);
});

test('Back and Forward restore the month, every filter and the displayed data', async () => {
  const ui = boot({ controls: true, hash: '?month=2026-08#monthly-activity', coverage: true });
  const backUrl = ui.location.href, form = ui.elements['view-controls'];
  ui.documentEvents.emit('click', { target: ui.navLinks[1] });
  Object.entries({ month: '2026-07', side: 'sell', limit: '50', cols: 'all' })
    .forEach(([name, value]) => { form.elements[name].value = value; });
  ui.elements['equity-only'].checked = false;
  ui.elements['active-only'].checked = false;
  form.emit('change', { target: ui.elements['month-select'] });
  ui.requests.at(-1).respond('July selling, all funds'); await settle();
  const forwardUrl = ui.location.href;

  for (const [url, expected, view, result] of [
    [backUrl, { month: '2026-08', side: 'buy', limit: '25', cols: 'core', equity_only: '1', active_only: '1' }, 'activity', 'August buying, active equity'],
    [forwardUrl, { month: '2026-07', side: 'sell', limit: '50', cols: 'all', equity_only: '0', active_only: '0' }, 'summary', 'July selling, all funds'],
  ]) {
    const before = ui.requests.length;
    ui.location.href = url;
    ui.window.emit('popstate');
    for (const name of ['month', 'side', 'limit', 'cols']) assert.equal(form.elements[name].value, expected[name]);
    assert.equal(ui.elements['equity-only'].checked, expected.equity_only === '1');
    assert.equal(ui.elements['active-only'].checked, expected.active_only === '1');
    assert.equal(ui.body.dataset.currentView, view);
    assert.equal(ui.requests.length, before + 1);
    const request = new URL(ui.requests.at(-1).url, ui.location.href);
    assert.equal(request.pathname, '/fragments/month/' + expected.month);
    for (const name of ['side', 'limit', 'cols', 'equity_only', 'active_only']) {
      assert.equal(request.searchParams.get(name), expected[name]);
    }
    ui.requests.at(-1).respond(result); await settle();
    assert.equal(ui.elements['month-view'].innerHTML, result);
    assert.equal(new URL(ui.location.href).hash, new URL(url).hash);
    assert.equal(new URL(ui.coverageLink.href).searchParams.get('month'), expected.month);
  }
  assert.equal(ui.pushes.length, 1);
});

test('Back restores default controls and prevents an older response from replacing them', async () => {
  const ui = boot({ controls: true });
  ui.documentEvents.emit('click', { target: ui.navLinks[1] });
  ui.elements['month-select'].value = '2026-07';
  ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
  const stale = ui.requests.at(-1);
  ui.location.href = 'http://localhost/';
  ui.window.emit('popstate');
  assert.equal(ui.elements['month-select'].value, '2026-08');
  assert.equal(stale.signal.aborted, true);
  ui.requests.at(-1).respond('August'); await settle();
  stale.respond('July'); await settle();
  assert.equal(ui.elements['month-view'].innerHTML, 'August');
  assert.equal(new URL(ui.location.href).searchParams.get('month'), '2026-08');
  assert.equal(ui.body.dataset.currentView, 'activity');
});

test('the search shortcut opens the palette, but / does not while typing', () => {
  const ui = boot({ search: true });
  const dialog = ui.elements['search-dialog'], input = new Element(); input.tagName = 'INPUT';
  assert.equal(ui.elements['search-open'].hidden, false);
  ui.documentEvents.emit('keydown', { key: '/', target: input });
  assert.equal(dialog.open, undefined);
  const shortcut = ui.documentEvents.emit('keydown', { key: 'k', ctrlKey: true, target: input });
  assert.equal(shortcut.defaultPrevented, true);
  assert.equal(dialog.open, true);
  assert.equal(ui.elements['stock-search'].selected, true);
});

test('search lists matching views and funds without a stock request for one character', async () => {
  const ui = boot({ search: true, schemes: true });
  ui.elements['search-open'].emit('click');
  await ui.type('stock-search', 'm');
  assert.equal(ui.requests.length, 0);
  assert.deepEqual(kinds(ui), ['Fund', 'View', 'View', 'View', 'View']);
  await ui.type('stock-search', 'monthly s');
  ui.requests[0].respond(JSON.stringify({ results: [] })); await settle();
  assert.deepEqual(kinds(ui), ['View']);
  const options = ui.elements['stock-options'].children;
  assert.equal(options[0].children[0].textContent, 'Monthly summary');
  options[0].emit('click');
  assert.equal(ui.elements['search-dialog'].open, false);
  assert.equal(ui.body.dataset.currentView, 'summary');
  // A picked fund opens the fund view with its summary.
  ui.elements['search-open'].emit('click');
  await ui.type('stock-search', 'test fund');
  ui.requests.at(-1).respond(JSON.stringify({ results: [] })); await settle();
  ui.elements['stock-options'].children[0].emit('click');
  assert.equal(ui.body.dataset.currentView, 'funds');
  assert.equal(ui.elements['scheme-search'].value, 'Test Fund');
  assert.equal(ui.requests.at(-1).url, '/api/summary/1/2026-08');
});

test('stocks lead the search results and a stock pick closes the palette first', async () => {
  const ui = boot({ search: true, schemes: true });
  ui.elements['search-open'].emit('click');
  await ui.type('stock-search', 'test');
  ui.requests[0].respond(stockResponse('Test Industries'));
  await settle();
  const kinds = ui.elements['stock-options'].children.map(o => o.children.at(-1).textContent);
  assert.equal(ui.elements['stock-options'].children[0].children[0].textContent, 'Test Industries');
  assert.equal(kinds[1], 'Fund');
  ui.elements['stock-options'].children[0].emit('click');
  assert.equal(ui.elements['search-dialog'].open, false);
  assert.equal(ui.elements['stock-dialog'].open, true);
  assert.match(ui.requests.at(-1).url, /^\/fragments\/stock\?q=INE002A01018/);
});

for (const underlying of ['stock', 'guide']) {
  for (const destination of ['view', 'fund']) {
    test(`search opens a ${destination} without leaving the ${underlying} drawer on top`, async () => {
      const ui = boot({ search: true, schemes: true, guide: true });
      const drawer = ui.elements[underlying + '-dialog'];
      if (underlying === 'stock') {
        ui.elements['stock-search'].value = 'INE002A01018';
        ui.elements['stock-form'].emit('submit');
      } else {
        const link = new Element(); link.setAttribute('href', '#notes-title');
        link.closest = selector => selector === "a[href='#notes-title']" ? link : null;
        ui.documentEvents.emit('click', { target: link });
      }
      assert.equal(drawer.open, true);
      drawer.deferCloseEvent = true; // Native close events can arrive after navigation.
      ui.documentEvents.emit('keydown', { key: 'k', ctrlKey: true });
      assert.equal(ui.elements['search-dialog'].open, true);
      await ui.type('stock-search', destination === 'view' ? 'monthly s' : 'test fund');
      ui.requests.at(-1).respond(JSON.stringify({ results: [] })); await settle();
      ui.elements['stock-options'].children[0].emit('click');
      assert.equal(ui.elements['search-dialog'].open, false);
      assert.equal(drawer.open, false);
      assert.equal(ui.body.dataset.currentView, destination === 'view' ? 'summary' : 'funds');
      if (underlying === 'stock') assert.equal(ui.requests[0].signal.aborted, true);
      drawer.emit('close');
      if (destination === 'fund') assert.equal(ui.requests.at(-1).url, '/api/summary/1/2026-08');
    });
  }
}

test('refreshing the month preserves the selected section in the URL', async () => {
  const ui = boot({ controls: true, hash: '#scheme-summary' });
  ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
  ui.requests[0].respond('<section>New month</section>');
  await settle();
  assert.equal(new URL(ui.location.href).hash, '#scheme-summary');
  assert.equal(new URL(ui.location.href).searchParams.get('month'), '2026-08');
});

test('the coverage link follows the successfully displayed month and filters', async () => {
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

test('history restores the selected fund summary and watchlist to the restored month', async () => {
  const ui = boot({ controls: true, schemes: true, watch: ['INE002A01018'], hash: '?month=2026-08#scheme-summary' });
  const backUrl = ui.location.href;
  await pickScheme(ui);
  ui.documentEvents.emit('click', { target: ui.navLinks[2] });
  ui.elements['month-select'].value = '2026-07';
  ui.elements['active-only'].checked = false;
  ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
  ui.requests.find(r => r.url.startsWith('/fragments/month/')).respond('July'); await settle();
  const oldSummary = ui.requests.at(-1);
  const oldWatchlist = ui.requests.filter(r => r.url === '/api/watchlist').at(-1);
  ui.location.href = backUrl;
  ui.window.emit('popstate');
  assert.equal(oldSummary.signal.aborted, true);
  assert.equal(oldWatchlist.signal.aborted, true);
  const summary = ui.requests.filter(r => r.url.startsWith('/api/summary/')).at(-1);
  const watchlist = ui.requests.filter(r => r.url === '/api/watchlist').at(-1);
  assert.equal(summary.url, '/api/summary/1/2026-08');
  assert.equal(JSON.parse(watchlist.options.body).month, '2026-08');
  assert.equal(JSON.parse(watchlist.options.body).active_only, true);
  summary.respond(JSON.stringify({ has_data: true, summary: 'August summary' })); await settle();
  oldSummary.respond(JSON.stringify({ has_data: true, summary: 'July summary' })); await settle();
  assert.equal(ui.elements['summary-box'].children[0].textContent, 'August summary');
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

// A ranking table with name, net and flow columns; rows start in rank order.
function rankingTable(ui, rows) {
  const headings = ['name', 'net', 'flow'].map((key, cellIndex) => {
    const th = new Element();
    Object.assign(th.dataset, { sortKey: key, sortLabel: key });
    th.cellIndex = cellIndex; th.childNodes = [];
    return th;
  });
  headings[1].setAttribute('aria-sort', 'descending');
  const body = new Element();
  body.rows = rows.map(([name, net, flow], i) => {
    const row = new Element(); row.dataset.rank = String(i + 1);
    row.cells = [name, net, flow].map(sort => ({ dataset: { sort } }));
    return row;
  });
  body.append = (...nodes) => { body.rows = nodes; };
  const table = new Element('consensus-table');
  table.tBodies = [body];
  table.querySelector = selector => selector === 'th[aria-sort]' ? headings.find(h => h.getAttribute('aria-sort')) :
    headings.find(h => selector === `th[data-sort-key="${h.dataset.sortKey}"]`) || null;
  table.querySelectorAll = selector => selector === 'th[data-sort-key]' ? headings : [];
  const select = new Element('sort-select'), label = new Element();
  select.closest = () => label;
  Object.assign(ui.elements, { 'consensus-table': table, 'sort-select': select });
  const order = () => body.rows.map(row => row.dataset.rank).join('');
  const reset = () => {
    body.rows.sort((a, b) => a.dataset.rank - b.dataset.rank);
    headings.forEach(h => h.removeAttribute('aria-sort'));
    headings[1].setAttribute('aria-sort', 'descending');
  };
  return { headings, select, label, order, reset };
}

async function refresh(ui, target = ui.elements['month-select']) {
  ui.elements['view-controls'].emit('change', { target });
  ui.requests.filter(r => r.url.startsWith('/fragments/month/')).at(-1).respond('ranking');
  await settle();
}

for (const cancelledFirst of [true, false]) {
  test(`cancelled ranking responses do not rewire sort controls (${cancelledFirst ? 'before' : 'after'} current response)`, async () => {
    const ui = boot({ controls: true });
    const { headings, select } = rankingTable(ui, [['Beta', '3', '1'], ['Alpha', '2', '2']]);
    ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
    const stale = ui.requests.at(-1);
    ui.elements['month-select'].value = '2026-07';
    ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
    const current = ui.requests.at(-1);
    assert.equal(stale.signal.aborted, true);
    if (cancelledFirst) {
      stale.respond('Stale August ranking'); await settle();
      assert.equal(select.children.length, 0);
      assert.equal(ui.elements['month-view'].getAttribute('aria-busy'), 'true');
    }
    current.respond('July ranking'); await settle();
    if (!cancelledFirst) { stale.respond('Stale August ranking'); await settle(); }
    assert.equal(ui.elements['month-view'].innerHTML, 'July ranking');
    assert.equal(select.children.length, 6);
    assert.equal(select.listeners.change.length, 1);
    for (const heading of headings) assert.equal(heading.children.length, 1);
  });
}

test('sorting reorders shown rows, keeps rank order for ties and puts missing values last', async () => {
  const ui = boot({ controls: true });
  const { headings, select, label, order, reset } = rankingTable(ui,
    [['Beta', '3', ''], ['alpha', '2', '5'], ['Gamma', '2', '5'], ['Delta', '1', '-2']]);
  await refresh(ui);
  assert.equal(label.hidden, false);
  assert.equal(select.value, 'net:descending');
  assert.equal(select.children.length, 6);
  const sortButton = key => headings.find(h => h.dataset.sortKey === key).children[0];
  sortButton('flow').emit('click');
  assert.equal(order(), '2341');
  assert.equal(headings[2].getAttribute('aria-sort'), 'descending');
  assert.equal(headings[1].getAttribute('aria-sort'), null);
  sortButton('flow').emit('click');
  assert.equal(order(), '4231');
  assert.equal(select.value, 'flow:ascending');
  sortButton('name').emit('click'); // Text sorts A to Z first, ignoring case.
  assert.equal(order(), '2143');
  select.value = 'net:ascending';
  select.emit('change');
  assert.equal(order(), '4231');
  // A month refresh keeps the reader's sort; switching buying and selling drops it.
  reset();
  await refresh(ui);
  assert.equal(order(), '4231');
  reset();
  await refresh(ui, { name: 'side' });
  assert.equal(order(), '1234');
  assert.equal(select.value, 'net:descending');
});

test('a click elsewhere on a ranking row opens its stock, but its own controls keep their action', () => {
  const ui = boot();
  const link = new Element(), row = new Element(), cell = new Element(), follow = new Element();
  link.dataset.isin = 'INE002A01018';
  row.querySelector = selector => selector === '.stock-link' ? link : null;
  cell.closest = selector => selector === '#consensus-table tbody tr' ? row : null;
  follow.closest = selector => selector === '#consensus-table tbody tr' ? row :
    selector.startsWith('a, button') ? follow : null;
  ui.documentEvents.emit('click', { target: follow });
  assert.equal(ui.requests.length, 0);
  ui.documentEvents.emit('click', { target: cell });
  assert.equal(link.focusCount, 1); // Focus returns to the row's link when the drawer closes.
  assert.equal(ui.elements['stock-dialog'].open, true);
  assert.match(ui.requests.at(-1).url, /^\/fragments\/stock\?q=INE002A01018/);
});

test('row bookmarks show state without text, and an empty watchlist links to the ranking', () => {
  const ui = boot({ watch: [], controls: true });
  const empty = ui.elements['watchlist-results'].children[0];
  assert.match(empty.textContent, /Your watchlist is empty/);
  assert.equal(empty.children[1].href, '#monthly-activity');
  assert.equal(ui.requests.length, 0);
  const bookmark = new Element(); bookmark.dataset.watchStock = 'INE002A01018';
  bookmark.closest = selector => selector === '[data-watch-stock]' ? bookmark : null;
  ui.elements['month-view'].append(bookmark);
  ui.documentEvents.emit('click', { target: bookmark });
  assert.equal(bookmark.getAttribute('aria-pressed'), 'true');
  assert.equal(bookmark.hidden, false);
  assert.equal(bookmark.textContent, '');
  assert.deepEqual(JSON.parse(ui.saved.get('findit-watchlist-v1')).stocks, ['INE002A01018']);
});


test('fund suggestions cap rendered options and let typing find later matches', async () => {
  const schemes = Array.from({ length: 200 }, (_, i) => ({ id: i + 1, label: 'Fund ' + i, amc: 'Test', votes: true }));
  const ui = boot({ schemes });
  await ui.type('scheme-search', 'Fund');
  const options = ui.elements['scheme-options'].children;
  assert.equal(options.filter(n => !n.getAttribute('aria-disabled')).length, 12);
  assert.match(ui.elements['scheme-search-status'].textContent, /first 12 of 200/);
  await ui.type('scheme-search', 'Fund 199');
  assert.equal(ui.elements['scheme-options'].children.length, 1);
  ui.elements['scheme-search'].emit('keydown', { key: 'Enter' });
  assert.match(ui.requests.at(-1).url, /summary\/200\/2026-08/);
});


test('fund catalog loads on demand, shares pending work and retries failure', async () => {
  const ui = boot({ schemes: 'lazy' });
  assert.equal(ui.requests.length, 0);
  await ui.type('scheme-search', 'Fund');
  assert.equal(ui.requests[0].url, '/api/schemes');
  await ui.type('scheme-search', 'Fund B');
  assert.equal(ui.requests.length, 1);
  ui.requests[0].respond('unavailable', { ok: false, status: 503 }); await settle();
  await ui.type('scheme-search', 'Fund B');
  assert.equal(ui.requests.length, 2);
  ui.requests[1].respond(JSON.stringify({ schemes: [{ scheme_id: 9, label: 'Fund B', amc_name: 'Test', votes: true }] }));
  await settle();
  assert.match(ui.elements['scheme-options'].textContent, /Fund B/);
  await ui.type('scheme-search', 'Fund');
  assert.equal(ui.requests.length, 2);
});


test('ranking page links preserve filters and a filter change returns to page one', async () => {
  const ui = boot({ controls: true });
  const link = new Element();
  link.href = 'http://localhost/?limit=0&cols=all&page=2';
  link.closest = selector => selector === '[data-view-link]' ? link : null;
  ui.documentEvents.emit('click', { target: link });
  assert.match(ui.requests.at(-1).url, /page=2/);
  assert.equal(ui.elements['view-controls'].elements.limit.value, '0');
  ui.requests.at(-1).respond('page two'); await settle();
  ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
  assert.doesNotMatch(ui.requests.at(-1).url, /page=/);
});


test('failed filters retain existing results with an explicit warning and recover on retry', async () => {
  const ui = boot({ controls: true });
  const view = ui.elements['month-view'];
  const previous = new Element(); previous.textContent = 'Previous ranked stocks';
  view.append(previous);
  ui.elements['month-select'].value = '2026-07';
  ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
  ui.requests.at(-1).respond('temporarily unavailable', { ok: false, status: 503 }); await settle();
  assert.equal(view.children[0], previous);
  assert.equal(view.dataset.stale, 'true');
  assert.match(ui.elements['view-status'].textContent, /temporarily unavailable/);
  assert.match(ui.elements['view-status'].textContent, /Previous results remain/);
  const warning = ui.elements['view-status'].children[0];
  warning.children.at(-1).emit('click');
  assert.match(ui.requests.at(-1).url, /2026-07/);
  ui.requests.at(-1).respond('July results'); await settle();
  assert.equal(view.innerHTML, 'July results');
  assert.equal(view.dataset.stale, undefined);
  assert.equal(view.getAttribute('aria-busy'), null);
});

test('watchlist refresh failure keeps its previous report and list', async () => {
  const ui = boot({ controls: true, watch: ['INE002A01018'] });
  const report = JSON.stringify({ scope: 'Active funds', market_completeness: 'unknown',
    release_id: 'a'.repeat(64), rule_version: 'test', stocks: [] });
  ui.requests.at(-1).respond(report); await settle();
  const previous = ui.elements['watchlist-results'].children[0];
  ui.elements['view-controls'].emit('change', { target: ui.elements['month-select'] });
  ui.requests.filter(r => r.url === '/api/watchlist').at(-1).respond('', { ok: false, status: 503 });
  await settle();
  assert.equal(ui.elements['watchlist-results'].children[0], previous);
  assert.match(ui.elements['watchlist-status'].textContent, /previous results below/);
  assert.equal(ui.elements['watchlist-report'].hidden, true);
  assert.deepEqual(JSON.parse(ui.saved.get('findit-watchlist-v1')).stocks, ['INE002A01018']);
});


test('keyboard view navigation moves focus to its heading', () => {
  const ui = boot();
  ui.documentEvents.emit('keydown', { key: 'Enter' });
  ui.documentEvents.emit('click', { target: ui.navLinks[2] });
  assert.equal(ui.elements['page-title'].focusCount, 1);
  assert.equal(ui.elements['page-title'].textContent, 'My watchlist');
});

test('autocomplete announces loading and clears busy on completion or dismissal', async () => {
  const ui = boot();
  await ui.type('stock-search', 'Reliance');
  assert.equal(ui.elements['stock-search'].getAttribute('aria-busy'), 'true');
  assert.match(ui.elements['stock-search-status'].textContent, /Loading suggestions/);
  ui.requests.at(-1).respond(stockResponse('Reliance')); await settle();
  assert.equal(ui.elements['stock-search'].getAttribute('aria-busy'), null);
  assert.match(ui.elements['stock-search-status'].textContent, /1 suggestion/);
  await ui.type('stock-search', 'Infosys');
  ui.elements['stock-search'].emit('blur');
  assert.equal(ui.elements['stock-search'].getAttribute('aria-busy'), null);
});

test('completed filter changes announce the selected month', async () => {
  const ui = boot({ controls: true });
  ui.elements['month-select'].value = '2026-07';
  await refresh(ui);
  assert.equal(ui.elements['view-status'].textContent, 'Jul 2026 results updated.');
});


test('storage failure warnings survive successful watchlist refreshes', async () => {
  const ui = boot({ controls: true, watch: [], storageBlocked: true });
  const bookmark = new Element(); bookmark.dataset.watchStock = 'INE002A01018';
  bookmark.closest = selector => selector === '[data-watch-stock]' ? bookmark : null;
  ui.elements['month-view'].append(bookmark);
  ui.documentEvents.emit('click', { target: bookmark });
  assert.match(ui.elements['watchlist-status'].textContent, /Device storage is unavailable/);
  ui.requests.at(-1).respond(JSON.stringify({ scope: 'Active funds', market_completeness: 'unknown',
    release_id: 'a'.repeat(64), rule_version: 'test', stocks: [] })); await settle();
  assert.match(ui.elements['watchlist-status'].textContent, /Export your watchlist before leaving/);
});
