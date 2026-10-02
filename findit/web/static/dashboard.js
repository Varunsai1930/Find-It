(function () {
  "use strict";
  var sectionNav = document.getElementById("section-nav");
  var navigationQueued = false;
  function selectNavigation(selected) {
    if (!sectionNav) return;
    var links = sectionNav.querySelectorAll("a");
    links.forEach(function (link) {
      if (link === selected) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
    if (selected) {
      var navBounds = sectionNav.getBoundingClientRect();
      var linkBounds = selected.getBoundingClientRect();
      sectionNav.style.setProperty("--nav-x", (linkBounds.left - navBounds.left) + "px");
      sectionNav.style.setProperty("--nav-y", (linkBounds.bottom - navBounds.top - 2) + "px");
      sectionNav.style.setProperty("--nav-width", linkBounds.width + "px");
      sectionNav.dataset.indicator = "ready";
    }
  }
  function updateNavigation() {
    if (!sectionNav) return;
    var sections = Array.from(sectionNav.querySelectorAll("a")).map(function (link) {
      return { link: link, target: document.getElementById(link.getAttribute("href").slice(1)) };
    }).filter(function (section) { return section.target; });
    if (!sections.length) return;
    var topbar = document.querySelector(".topbar");
    var readingLine = (topbar ? Math.max(0, topbar.getBoundingClientRect().bottom) : 0) + 24;
    var selected = sections[0].link;
    sections.forEach(function (section) {
      var anchorMargin = parseFloat(getComputedStyle(section.target).scrollMarginTop) || 0;
      if (section.target.getBoundingClientRect().top <= readingLine + anchorMargin) selected = section.link;
    });
    // The short reading guide cannot always reach the top of the viewport.
    if (window.scrollY > 0 && Math.ceil(window.scrollY + window.innerHeight) >=
        document.documentElement.scrollHeight - 1) selected = sections[sections.length - 1].link;
    selectNavigation(selected);
  }
  function scheduleNavigation() {
    if (!sectionNav || navigationQueued) return;
    navigationQueued = true;
    requestAnimationFrame(function () {
      navigationQueued = false;
      updateNavigation();
    });
  }
  function hashNavigation() {
    var links = sectionNav.querySelectorAll("a");
    var hash = new URL(location.href).hash;
    selectNavigation(Array.from(links).find(function (link) {
      return link.getAttribute("href") === hash;
    }) || links[0]);
    scheduleNavigation();
  }
  if (sectionNav) {
    window.addEventListener("hashchange", hashNavigation);
    window.addEventListener("scroll", scheduleNavigation, { passive: true });
    window.addEventListener("resize", scheduleNavigation);
    window.addEventListener("load", scheduleNavigation);
    window.addEventListener("pageshow", scheduleNavigation);
    document.addEventListener("toggle", scheduleNavigation, true);
    hashNavigation();
  }
  var themeToggle = document.getElementById("theme-toggle");
  if (themeToggle) {
    function themeLabel() {
      var next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      themeToggle.textContent = next === "light" ? "Light mode" : "Dark mode";
      themeToggle.setAttribute("aria-label", "Switch to " + next + " mode");
    }
    themeToggle.hidden = false;
    themeLabel();
    themeToggle.addEventListener("click", function () {
      var next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("findit-theme", next); } catch (e) { /* Optional storage. */ }
      themeLabel();
    });
  }
  var form = document.getElementById("view-controls");
  var view = document.getElementById("month-view");
  // Standalone evidence and coverage pages only use the appearance control.
  if (!view) return;
  var viewStatus = document.getElementById("view-status");
  var summaryBox = document.getElementById("summary-box");
  var dialog = document.getElementById("stock-dialog");
  var stockBox = document.getElementById("stock-box");
  var inflight = {};
  var stockOpener = null;
  var watchlist = [];
  var watchStatus = document.getElementById("watchlist-status");
  var watchResults = document.getElementById("watchlist-results");
  function validateWatchlist(value) {
    if (!value || value.version !== 1 || !Array.isArray(value.stocks) || value.stocks.length > 100 ||
        value.stocks.some(function (s) { return typeof s !== "string" || !/^[A-Z]{2}[A-Z0-9]{9}\d$/.test(s); })) {
      throw new Error("Use a FindIt watchlist export with up to 100 valid ISINs.");
    }
    return Array.from(new Set(value.stocks)).sort();
  }
  try {
    var saved = localStorage.getItem("findit-watchlist-v1");
    if (saved) watchlist = validateWatchlist(JSON.parse(saved));
  } catch (e) {
    if (watchStatus) watchStatus.textContent = "Saved watchlist could not be read. Import a valid export to recover it.";
  }
  function saveWatchlist() {
    try {
      localStorage.setItem("findit-watchlist-v1", JSON.stringify({ version: 1, stocks: watchlist }));
      if (watchStatus) watchStatus.textContent = "Saved on this device.";
    } catch (e) {
      if (watchStatus) watchStatus.textContent = "Device storage is unavailable. Export your watchlist before leaving.";
    }
  }
  var watchlistSequence = 0;
  async function refreshWatchlist() {
    if (!watchResults || !currentMonth()) return;
    var sequence = ++watchlistSequence;
    if (inflight.watchlist) inflight.watchlist.abort();
    var download = document.getElementById("watchlist-report");
    download.hidden = true;
    if (!watchlist.length) {
      watchResults.textContent = "Your watchlist is empty. Open a stock and choose Follow stock.";
      download.hidden = true;
      return;
    }
    var q = new URLSearchParams({ month: currentMonth(), stocks: watchlist.join(","),
                                 active_only: document.getElementById("active-only").checked ? "1" : "0" });
    watchResults.textContent = "Loading monthly changes…";
    try {
      var result = await load("watchlist", "/api/watchlist?" + q);
      if (sequence !== watchlistSequence) return;
      if (!result.ok) throw new Error("HTTP " + result.status);
      var report = JSON.parse(result.body);
      watchResults.replaceChildren();
      var note = document.createElement("p");
      note.textContent = report.scope + "; market completeness unknown. Release " + report.release_id.slice(0, 12) + ". House directions net the compared individual funds’ share changes within each house; the stock detail counts houses with any buying or selling fund, so a house can appear on both sides there.";
      watchResults.append(note);
      report.stocks.forEach(function (stock) {
        var row = document.createElement("p"), link = document.createElement("a"), remove = document.createElement("button");
        link.href = stock.evidence_url; link.textContent = stock.name + " · View evidence";
        row.append(link, " — " + stock.status.replaceAll("_", " ") + "; " + (stock.houses_buying === null ? "unavailable" : stock.houses_buying) + " houses net adding shares / " + (stock.houses_selling === null ? "unavailable" : stock.houses_selling) + " net reducing shares. ");
        var number = function (v) { return v === null ? "unavailable" : new Intl.NumberFormat("en-IN").format(v); };
        row.append("Net change: " + number(stock.net_share_change) + " shares. Individual-fund additions / exits: " + number(stock.additions) + " / " + number(stock.exits) + ". ");
        if (stock.largest_compared_changes.length) {
          var largest = stock.largest_compared_changes[0];
          row.append("Largest compared fund change: " + largest.fund + ", " + number(largest.shares) + " shares. Independent review pending. ");
        }
        remove.type = "button"; remove.className = "btn btn--quiet"; remove.textContent = "Unfollow";
        remove.setAttribute("aria-label", "Unfollow " + stock.name);
        remove.addEventListener("click", function () { watchlist = watchlist.filter(function (s) { return s !== stock.isin; }); saveWatchlist(); refreshWatchlist(); });
        row.append(remove); watchResults.append(row);
      });
      q.set("release", report.release_id); q.set("rules", report.rule_version);
      download.href = "/watchlist/report?" + q; download.hidden = false;
    } catch (e) {
      if (sequence === watchlistSequence && e.name !== "AbortError") watchResults.replaceChildren(stateNode("error", "Could not load your watchlist. " + e.message, refreshWatchlist));
    }
  }
  if (watchResults) {
    document.getElementById("watchlist-export").addEventListener("click", function () {
      var url = URL.createObjectURL(new Blob([JSON.stringify({ version: 1, stocks: watchlist }, null, 2)], { type: "application/json" }));
      var link = document.createElement("a"); link.href = url; link.download = "findit-watchlist.json"; link.click(); URL.revokeObjectURL(url);
    });
    document.getElementById("watchlist-import").addEventListener("change", async function (event) {
      try {
        var file = event.target.files[0]; if (!file) return;
        if (file.size > 20000) throw new Error("Watchlist file is too large.");
        var imported = validateWatchlist(JSON.parse(await file.text()));
        var combined = Array.from(new Set(watchlist.concat(imported))).sort();
        if (combined.length > 100) throw new Error("Keep at most 100 stocks.");
        watchlist = combined;
        saveWatchlist(); refreshWatchlist();
      } catch (e) { watchStatus.textContent = "Import failed: " + e.message; }
      event.target.value = "";
    });
    refreshWatchlist();
  }

  function stateNode(kind, text, retry) {
    var p = document.createElement("p");
    p.className = "state state--" + kind;
    p.setAttribute("role", kind === "error" ? "alert" : "status");
    p.textContent = text;
    if (retry) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn--quiet state__retry";
      b.textContent = "Retry";
      b.addEventListener("click", retry);
      p.append(" ", b);
    }
    return p;
  }

  // Fetch text, cancelling any earlier request for the same slot so a slow
  // response can never overwrite a newer one.
  async function load(slot, url) {
    if (inflight[slot]) inflight[slot].abort();
    var ctrl = new AbortController();
    inflight[slot] = ctrl;
    try {
      var res = await fetch(url, { signal: ctrl.signal });
      var body = await res.text();
      if (ctrl.signal.aborted) throw new DOMException("Request cancelled", "AbortError");
      return { ok: res.ok, status: res.status, body: body };
    } finally {
      if (inflight[slot] === ctrl) inflight[slot] = null;
    }
  }

  function currentMonth() {
    var sel = document.getElementById("month-select");
    return sel ? sel.value : "";
  }

  function monthLabel() {
    var sel = document.getElementById("month-select");
    return sel ? sel.options[sel.selectedIndex].text : "";
  }

  // The view's filters as the fragment and the URL take them.
  function viewQuery() {
    var data = new FormData(form);
    var q = new URLSearchParams();
    q.set("side", data.get("side") || "buy");
    q.set("limit", data.get("limit") || "25");
    q.set("cols", data.get("cols") || "core");
    q.set("equity_only", document.getElementById("equity-only").checked ? "1" : "0");
    q.set("active_only", document.getElementById("active-only").checked ? "1" : "0");
    return q;
  }

  function setUrlParam(key, value) {
    var url = new URL(location.href);
    if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
    history.replaceState(null, "", url);
  }

  async function refreshView() {
    var month = currentMonth();
    if (!month) return;
    var q = viewQuery();
    var details = view.querySelector("#coverage-details");
    var coverageOpen = details && details.open;
    var hadFocus = view.contains(document.activeElement);
    view.setAttribute("aria-busy", "true");
    viewStatus.className = "controls__status is-loading";
    viewStatus.textContent = "Loading " + monthLabel() + "…";
    try {
      var r = await load("view", "/fragments/month/" + encodeURIComponent(month) + "?" + q);
      if (!r.ok) throw new Error("HTTP " + r.status);
      view.innerHTML = r.body;
      if (coverageOpen) view.querySelector("#coverage-details").open = true;
      viewStatus.className = "controls__status";
      viewStatus.textContent = "";
      q.set("month", month);
      var url = new URL(location.href);
      url.search = q.toString();
      history.replaceState(null, "", url);
      if (hadFocus) {
        var title = document.getElementById("consensus-title");
        if (title) title.focus();
      }
    } catch (e) {
      if (e.name === "AbortError") return;
      viewStatus.className = "controls__status";
      viewStatus.textContent = "";
      view.replaceChildren(stateNode("error",
        "Could not load " + monthLabel() + " (" + e.message + ").", refreshView));
    } finally {
      if (!inflight.view) view.removeAttribute("aria-busy");
      scheduleNavigation();
    }
  }

  // -- combobox: ARIA 1.2 list autocomplete over a list of {value, label, detail}.
  function combobox(opts) {
    var input = opts.input, list = opts.list, status = opts.status;
    var items = [], active = -1, seq = 0, timer = null;

    function close() {
      ++seq;
      clearTimeout(timer);
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    }

    function setActive(i) {
      var nodes = list.querySelectorAll("[role=option]:not([aria-disabled])");
      if (!nodes.length) return;
      active = (i + nodes.length) % nodes.length;
      nodes.forEach(function (n, k) { n.setAttribute("aria-selected", k === active ? "true" : "false"); });
      input.setAttribute("aria-activedescendant", nodes[active].id);
      nodes[active].scrollIntoView({ block: "nearest" });
    }

    function show(results, note) {
      items = results;
      active = -1;
      input.removeAttribute("aria-activedescendant");
      list.replaceChildren();
      results.forEach(function (item, i) {
        var li = document.createElement("li");
        li.id = list.id + "-" + i;
        li.className = "combo__option";
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", "false");
        var main = document.createElement("span");
        main.className = "combo__label";
        main.textContent = item.label;
        li.append(main);
        if (item.detail) {
          var sub = document.createElement("span");
          sub.className = "combo__detail";
          sub.textContent = item.detail;
          li.append(sub);
        }
        // mousedown would blur the input and close the list before the click lands.
        li.addEventListener("mousedown", function (e) { e.preventDefault(); });
        li.addEventListener("click", function () { pick(i); });
        list.append(li);
      });
      if (note) {
        var empty = document.createElement("li");
        empty.className = "combo__note";
        empty.setAttribute("role", "option");
        empty.setAttribute("aria-disabled", "true");
        empty.textContent = note;
        list.append(empty);
      }
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      status.textContent = note || (results.length + (results.length === 1 ? " suggestion" : " suggestions") +
        ". Use the up and down arrows to choose.");
    }

    async function update() {
      var query = input.value.trim();
      var mine = ++seq;
      if (query.length < opts.minChars) { close(); status.textContent = ""; return; }
      try {
        var results = await opts.source(query);
        if (mine !== seq) return;
        show(results, results.length ? "" : opts.emptyText(query));
      } catch (e) {
        if (e.name === "AbortError" || mine !== seq) return;
        show([], "Could not load suggestions (" + e.message + ").");
      }
    }

    function pick(i) {
      var item = items[i];
      close();
      input.value = item.label;
      opts.onPick(item);
    }

    input.addEventListener("input", function () {
      close();
      if (opts.onType) opts.onType();
      timer = setTimeout(update, opts.delay || 0);
    });
    input.addEventListener("blur", close);
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (list.hidden) { update(); return; }
        setActive(active < 0 ? (e.key === "ArrowDown" ? 0 : -1) : active + (e.key === "ArrowDown" ? 1 : -1));
      } else if (e.key === "Enter") {
        if (!list.hidden && active >= 0) { e.preventDefault(); pick(active); }
        else if (!list.hidden && items.length === 1) { e.preventDefault(); pick(0); }
      } else if (e.key === "Escape") {
        if (!list.hidden) { e.preventDefault(); close(); }
        else if (input.value) { e.preventDefault(); input.value = ""; if (opts.onType) opts.onType(); }
      }
    });
    return { close: close };
  }

  // -- stock detail dialog --------------------------------------------------
  async function loadStock(query) {
    var month = currentMonth();
    var q = new URLSearchParams({ q: query });
    if (month) q.set("month", month);
    if (form) {
      var filters = viewQuery();
      q.set("equity_only", filters.get("equity_only"));
      q.set("active_only", filters.get("active_only"));
    }
    stockBox.setAttribute("aria-busy", "true");
    stockBox.replaceChildren(stateNode("loading", "Loading " + query + (month ? " for " + monthLabel() : "") + "…"));
    try {
      var r = await load("stock", "/fragments/stock?" + q);
      if (!r.ok) throw new Error(r.status === 404 ? "not found" : "HTTP " + r.status);
      stockBox.innerHTML = r.body;
      var head = stockBox.querySelector("[data-stock-isin]");
      // The month goes in too, so the link keeps meaning this month after a newer one loads.
      if (month) setUrlParam("month", month);
      setUrlParam("stock", head ? head.dataset.stockIsin : "");
      var focusTarget = stockBox.querySelector("h3") || stockBox.querySelector(".stock-link");
      if (focusTarget) focusTarget.focus();
    } catch (e) {
      if (e.name === "AbortError") return;
      stockBox.replaceChildren(stateNode("error", "Could not load " + query + " (" + e.message + ").",
        function () { loadStock(query); }));
    } finally {
      if (!inflight.stock) stockBox.removeAttribute("aria-busy");
    }
  }

  function openStock(query) {
    query = (query || "").trim();
    if (!query) return;
    if (!dialog.open) {
      stockOpener = document.activeElement;
      dialog.showModal();
    }
    loadStock(query);
  }

  dialog.addEventListener("close", function () {
    if (inflight.stock) inflight.stock.abort();
    setUrlParam("stock", "");
    if (stockOpener && stockOpener.isConnected) stockOpener.focus();
    stockOpener = null;
  });
  document.getElementById("stock-dialog-close").addEventListener("click", function () { dialog.close(); });
  // A click on the backdrop lands on the dialog element itself.
  dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });

  // -- scheme summary -------------------------------------------------------
  var pickedScheme = null;

  function clearSummary() {
    pickedScheme = null;
    if (inflight.summary) inflight.summary.abort();
    summaryBox.replaceChildren(stateNode("empty", "Pick a fund to read its monthly change summary."));
  }

  async function showSummary() {
    if (!pickedScheme || !currentMonth()) return;
    summaryBox.replaceChildren(stateNode("loading", "Loading summary for " + monthLabel() + "…"));
    try {
      var r = await load("summary", "/api/summary/" + encodeURIComponent(pickedScheme) + "/" +
                         encodeURIComponent(currentMonth()));
      if (r.status === 404) {
        summaryBox.replaceChildren(stateNode("empty",
          "No holding-change data available for this fund and month yet."));
        return;
      }
      if (!r.ok) throw new Error("HTTP " + r.status);
      var data = JSON.parse(r.body);
      var p = document.createElement("p");
      p.className = data.has_data ? "summary-text" : "state state--empty";
      p.textContent = data.summary;
      summaryBox.replaceChildren(p);
    } catch (e) {
      if (e.name !== "AbortError")
        summaryBox.replaceChildren(stateNode("error", "Could not load summary (" + e.message + ").", showSummary));
    }
  }

  var schemeData = document.getElementById("scheme-data");
  if (schemeData) {
    var schemes = JSON.parse(schemeData.textContent).map(function (s) {
      return { value: String(s.id), label: s.label,
               detail: s.amc + (s.votes ? "" : " · index/debt, does not vote"),
               haystack: (s.label + " " + s.amc).toLowerCase() };
    });
    combobox({
      input: document.getElementById("scheme-search"),
      list: document.getElementById("scheme-options"),
      status: document.getElementById("scheme-search-status"),
      minChars: 0,
      source: function (query) {
        var words = query.toLowerCase().split(/\s+/).filter(Boolean);
        return Promise.resolve(schemes.filter(function (s) {
          return words.every(function (w) { return s.haystack.indexOf(w) !== -1; });
        }));
      },
      emptyText: function (query) { return "No fund matches “" + query + "”."; },
      onType: clearSummary,
      onPick: function (item) { pickedScheme = item.value; showSummary(); }
    });
    document.getElementById("summary-form").addEventListener("submit", function (e) { e.preventDefault(); });
  }

  // -- stock search -----------------------------------------------------------
  var stockForm = document.getElementById("stock-form");
  if (stockForm) {
    var stockInput = document.getElementById("stock-search");
    var stockPicker = combobox({
      input: stockInput,
      list: document.getElementById("stock-options"),
      status: document.getElementById("stock-search-status"),
      minChars: 2,
      delay: 150,
      source: async function (query) {
        var q = new URLSearchParams({ q: query });
        if (currentMonth()) q.set("month", currentMonth());
        var r = await load("suggest", "/api/stocks/search?" + q);
        if (!r.ok) throw new Error("HTTP " + r.status);
        return JSON.parse(r.body).results.map(function (s) {
          var held = s.schemes_holding ? "held by " + s.schemes_holding + " fund" + (s.schemes_holding === 1 ? "" : "s")
                                       : "not held";
          return { value: s.isin, label: s.name,
                   detail: s.isin + (s.industry ? " · " + s.industry : "") + " · " + held + " in " + monthLabel() };
        });
      },
      emptyText: function (query) { return "No stock matches “" + query + "”."; },
      onPick: function (item) { openStock(item.value); }
    });
    // Enter without choosing a suggestion looks the text up as typed.
    stockForm.addEventListener("submit", function (e) { e.preventDefault(); openStock(stockInput.value); });
  }

  // -- view controls ----------------------------------------------------------
  if (form) {
    form.addEventListener("change", function (e) {
      refreshView();
      refreshWatchlist();
      // The summary follows the month once the reader has opened one.
      if (e.target.id === "month-select") {
        if (stockPicker) stockPicker.close();
        if (pickedScheme) showSummary();
      }
    });
    form.addEventListener("submit", function (e) { e.preventDefault(); refreshView(); });
  }

  document.addEventListener("click", function (e) {
    var follow = e.target.closest("[data-watch-stock]");
    if (follow) {
      var isin = follow.dataset.watchStock;
      if (watchlist.includes(isin)) { if (watchStatus) watchStatus.textContent = "Already followed on this device."; return; }
      if (watchlist.length >= 100) { if (watchStatus) watchStatus.textContent = "Keep at most 100 followed stocks."; return; }
      watchlist.push(isin); watchlist.sort(); saveWatchlist(); refreshWatchlist();
      follow.textContent = "Following on this device";
      return;
    }
    var link = e.target.closest(".stock-link");
    if (link) { openStock(link.dataset.isin); return; }
    // "Show all" and the column toggle carry their view in the href; apply it
    // to the form so the URL and later filter changes keep it.
    var viewLink = e.target.closest("[data-view-link]");
    if (viewLink && form) {
      e.preventDefault();
      new URL(viewLink.href).searchParams.forEach(function (value, key) {
        if (key === "limit" || key === "cols") form.elements[key].value = value;
      });
      refreshView();
    }
  });

  var initialStock = new URL(location.href).searchParams.get("stock");
  if (initialStock) openStock(initialStock);
})();
