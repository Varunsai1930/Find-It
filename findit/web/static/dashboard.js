(function () {
  "use strict";
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
  var viewStatus = document.getElementById("view-status");
  var summaryBox = document.getElementById("summary-box");
  var dialog = document.getElementById("stock-dialog");
  var stockBox = document.getElementById("stock-box");
  var inflight = {};
  var stockOpener = null;

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
    var details = view.querySelector("details.coverage");
    var coverageOpen = details && details.open;
    var hadFocus = view.contains(document.activeElement);
    view.setAttribute("aria-busy", "true");
    viewStatus.className = "controls__status is-loading";
    viewStatus.textContent = "Loading " + monthLabel() + "…";
    try {
      var r = await load("view", "/fragments/month/" + encodeURIComponent(month) + "?" + q);
      if (!r.ok) throw new Error("HTTP " + r.status);
      view.innerHTML = r.body;
      if (coverageOpen) view.querySelector("details.coverage").open = true;
      viewStatus.className = "controls__status";
      viewStatus.textContent = "";
      q.set("month", month);
      history.replaceState(null, "", "/?" + q);
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
    summaryBox.replaceChildren(stateNode("empty", "Pick a scheme to read its monthly change summary."));
  }

  async function showSummary() {
    if (!pickedScheme || !currentMonth()) return;
    summaryBox.replaceChildren(stateNode("loading", "Loading summary for " + monthLabel() + "…"));
    try {
      var r = await load("summary", "/api/summary/" + encodeURIComponent(pickedScheme) + "/" +
                         encodeURIComponent(currentMonth()));
      if (r.status === 404) {
        summaryBox.replaceChildren(stateNode("empty",
          "No holding-change data available for this scheme and month yet."));
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
      emptyText: function (query) { return "No scheme matches “" + query + "”."; },
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
          var held = s.schemes_holding ? "held by " + s.schemes_holding + " scheme" + (s.schemes_holding === 1 ? "" : "s")
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
      // The summary follows the month once the reader has opened one.
      if (e.target.id === "month-select") {
        if (stockPicker) stockPicker.close();
        if (pickedScheme) showSummary();
      }
    });
    form.addEventListener("submit", function (e) { e.preventDefault(); refreshView(); });
  }

  document.addEventListener("click", function (e) {
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
