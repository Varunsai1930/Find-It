(function () {
  "use strict";
  // -- views --------------------------------------------------------------------
  // Each navigation link names one view. The page shows one view at a time:
  // CSS hides every [data-view] element that does not list the current view.
  // Without this script the links stay in-page anchors and every view shows.
  var sectionNav = document.getElementById("section-nav");
  var navLinks = sectionNav ? Array.from(sectionNav.querySelectorAll("a")) : [];
  var pageTitle = document.getElementById("page-title");
  var pageDescription = document.getElementById("page-description");
  function navLinkFor(hash) {
    return navLinks.find(function (link) { return link.getAttribute("href") === hash; }) || null;
  }
  function showView(link) {
    document.body.dataset.currentView = link.dataset.view;
    navLinks.forEach(function (other) {
      if (other === link) other.setAttribute("aria-current", "page");
      else other.removeAttribute("aria-current");
    });
    if (pageTitle && link.dataset.title) {
      pageTitle.textContent = link.dataset.title;
      document.title = "FindIt · " + link.dataset.title;
    }
    if (pageDescription && link.dataset.description) pageDescription.textContent = link.dataset.description;
    link.scrollIntoView({ block: "nearest", inline: "nearest" });
    updateOverflow(sectionNav);
  }
  // No hash means the default view; other non-view anchors keep the current view.
  function followHash() {
    var hash = new URL(location.href).hash;
    var link = hash ? navLinkFor(hash) : navLinks[0];
    if (!link) return false;
    showView(link);
    window.scrollTo(0, 0);
    return true;
  }
  function goToView(link) {
    var url = new URL(location.href);
    if (url.hash !== link.getAttribute("href")) {
      url.hash = link.getAttribute("href");
      history.pushState(null, "", url);
    }
    showView(link);
    window.scrollTo(0, 0);
  }
  // The nav links (on narrow screens) and the notable lists scroll sideways;
  // fade the edge that hides more.
  function updateOverflow(row) {
    if (!row) return;
    var start = row.scrollLeft > 1;
    var end = row.scrollLeft + row.clientWidth < row.scrollWidth - 1;
    row.dataset.overflow = (start ? "start " : "") + (end ? "end" : "");
  }
  if (navLinks.length) {
    showView(navLinkFor(new URL(location.href).hash) || navLinks[0]);
    window.addEventListener("hashchange", followHash);
    window.addEventListener("popstate", restoreHistory);
    // The browser scrolls to a view's anchor on load; a view starts at the top.
    window.addEventListener("load", followHash);
    window.addEventListener("resize", function () {
      updateOverflow(sectionNav);
      updateOverflow(notableGrid);
    });
    sectionNav.addEventListener("scroll", function () { updateOverflow(sectionNav); }, { passive: true });
    // Any in-page link to a view switches to it; modified clicks keep their default.
    document.addEventListener("click", function (event) {
      var anchor = event.target.closest && event.target.closest("a[href^='#']");
      var link = anchor && navLinkFor(anchor.getAttribute("href"));
      if (!link || event.defaultPrevented || event.button > 0 ||
          event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      goToView(link);
    });
  }
  var themeToggle = document.getElementById("theme-toggle");
  if (themeToggle) {
    var themeText = themeToggle.querySelector("[data-theme-label]") || themeToggle;
    var themeChosen = false;
    function themeLabel() {
      var next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      themeText.textContent = next === "light" ? "Light mode" : "Dark mode";
      themeToggle.setAttribute("aria-label", "Switch to " + next + " mode");
    }
    function applyTheme(next) {
      // Commit the palette as one change before restoring hover/press transitions.
      // The toggle's own icon pair keeps its cross-fade; it animates no colors.
      var override = document.createElement("style");
      override.textContent = "*:not(.icon-swap>.icon),*::before,*::after{transition:none!important}";
      document.head.append(override);
      document.documentElement.dataset.theme = next;
      void document.documentElement.offsetHeight;
      requestAnimationFrame(function () {
        requestAnimationFrame(function () { override.remove(); });
      });
      themeLabel();
    }
    themeToggle.hidden = false;
    themeLabel();
    themeToggle.addEventListener("click", function () {
      var next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      themeChosen = true;
      applyTheme(next);
      try { localStorage.setItem("findit-theme", next); } catch (e) { /* Optional storage. */ }
    });
    // Follow system appearance until the reader chooses a device preference.
    var systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
    systemTheme.addEventListener("change", function (event) {
      if (themeChosen) return;
      var preference;
      try { preference = localStorage.getItem("findit-theme"); } catch (e) { /* Optional storage. */ }
      if (preference !== "light" && preference !== "dark") applyTheme(event.matches ? "dark" : "light");
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
  var searchDialog = document.getElementById("search-dialog");
  var inflight = {};
  var keyboardAction = false;
  document.addEventListener("keydown", function () { keyboardAction = true; }, true);
  document.addEventListener("pointerdown", function () { keyboardAction = false; }, true);
  function isKeyboardAction(event) {
    return keyboardAction || !!(event && (event.type === "keydown" || event.detail === 0));
  }
  var watchlist = [];
  var watchStatus = document.getElementById("watchlist-status");
  var watchStorageWarning = "";
  function watchMessage(text) {
    if (watchStatus) watchStatus.textContent = [watchStorageWarning, text].filter(Boolean).join(" ");
  }
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
    watchStorageWarning = "Saved watchlist could not be read. Import a valid export to recover it.";
    watchMessage("");
  }
  function saveWatchlist() {
    try {
      localStorage.setItem("findit-watchlist-v1", JSON.stringify({ version: 1, stocks: watchlist }));
      watchStorageWarning = "";
      watchMessage("Saved on this device.");
    } catch (e) {
      watchStorageWarning = "Device storage is unavailable. Export your watchlist before leaving.";
      watchMessage("");
    }
  }
  // Follow buttons live in the stock detail, on each table row and in the
  // notable lists. A bookmark has no text: its pressed state carries the change.
  function updateFollowButtons() {
    [stockBox, view].forEach(function (root) {
      root.querySelectorAll("[data-watch-stock]").forEach(function (button) {
        var followed = watchlist.includes(button.dataset.watchStock);
        var label = button.querySelector("[data-follow-label]");
        if (label) label.textContent = followed ? "Following on this device" : "Follow stock on this device";
        button.setAttribute("aria-pressed", followed ? "true" : "false");
        button.hidden = false;
      });
    });
  }
  function setFollowed(isin, followed) {
    if (followed === watchlist.includes(isin)) return;
    if (followed && watchlist.length >= 100) {
      if (watchStatus) watchStatus.textContent = "Keep at most 100 followed stocks.";
      return;
    }
    watchlist = followed ? watchlist.concat(isin).sort() : watchlist.filter(function (s) { return s !== isin; });
    saveWatchlist(); updateFollowButtons(); refreshWatchlist();
  }
  function downloadText(contents, type, filename) {
    var url = URL.createObjectURL(new Blob([contents], { type: type }));
    var link = document.createElement("a");
    link.href = url; link.download = filename; link.hidden = true;
    document.body.append(link);
    link.click(); link.remove();
    // Give the browser time to start reading the Blob before releasing it.
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  var watchStates = { changed: "Changed", unchanged_in_compared_funds: "No change in compared funds",
    net_unchanged_with_opposing_changes: "No net change: additions and reductions cancel out",
    unavailable: "No usable comparison", unknown_stock: "Unknown stock" };
  function formatCount(value, signed) {
    if (value === null || value === undefined) return "unavailable";
    var digits = new Intl.NumberFormat("en-US").format(Math.abs(value));
    return (value < 0 ? "−" : signed && value > 0 ? "+" : "") + digits;
  }
  // One followed stock: name, comparison state, four facts, then Unfollow last.
  function watchItem(stock) {
    var item = document.createElement("article"), head = document.createElement("div");
    var name = document.createElement("strong"), state = document.createElement("span");
    var link = document.createElement("a"), facts = document.createElement("dl");
    var remove = document.createElement("button");
    var largest = stock.largest_compared_changes[0];
    item.className = "watch-item";
    head.className = "watch-item__head";
    name.textContent = stock.name;
    state.className = "tag" + (stock.status === "unavailable" ? " tag--stale" :
      stock.status === "unknown_stock" ? " tag--missing" : "");
    state.textContent = watchStates[stock.status] || stock.status.replaceAll("_", " ");
    link.href = stock.evidence_url; link.textContent = "View evidence";
    head.append(name, state, link);
    facts.className = "watch-item__facts";
    [["Fund houses", stock.houses_buying === null ? "unavailable" :
        stock.houses_buying + " adding · " + stock.houses_selling + " reducing"],
     ["Net share change", formatCount(stock.net_share_change, true)],
     ["Funds opening / exiting", formatCount(stock.additions) + " / " + formatCount(stock.exits)],
     ["Largest fund change", largest ? largest.fund + ": " + formatCount(largest.shares, true) : "none"]
    ].forEach(function (fact) {
      var row = document.createElement("div"), term = document.createElement("dt"), value = document.createElement("dd");
      term.textContent = fact[0]; value.textContent = fact[1];
      row.append(term, value); facts.append(row);
    });
    remove.type = "button"; remove.className = "btn btn--quiet"; remove.textContent = "Unfollow";
    remove.setAttribute("aria-label", "Unfollow " + stock.name);
    remove.addEventListener("click", function () { setFollowed(stock.isin, false); });
    item.append(head, facts, remove);
    return item;
  }
  function emptyWatchlist() {
    var box = document.createElement("div"), text = document.createElement("p"), browse = document.createElement("a");
    box.className = "watch-empty";
    text.textContent = "Your watchlist is empty. Select the bookmark next to any stock in Monthly activity," +
      " or open a stock and select Follow stock.";
    browse.className = "btn"; browse.href = "#monthly-activity"; browse.textContent = "Browse this month’s stocks";
    box.append(text, browse);
    return box;
  }
  var watchlistSequence = 0;
  async function refreshWatchlist() {
    if (!watchResults || !currentMonth()) return;
    var sequence = ++watchlistSequence;
    if (inflight.watchlist) inflight.watchlist.abort();
    var download = document.getElementById("watchlist-report");
    download.hidden = true;
    delete download.dataset.reportRequest;
    if (!watchlist.length) {
      watchResults.replaceChildren(emptyWatchlist());
      watchResults.removeAttribute("aria-busy");
      delete watchResults.dataset.stale;
      return;
    }
    var body = { month: currentMonth(), stocks: watchlist.slice(),
                 active_only: document.getElementById("active-only").checked };
    watchResults.setAttribute("aria-busy", "true");
    watchMessage("Loading monthly changes…");
    try {
      var result = await load("watchlist", "/api/watchlist", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (sequence !== watchlistSequence) return;
      if (!result.ok) throw responseError(result);
      var report = JSON.parse(result.body);
      var note = document.createElement("div"), context = document.createElement("p");
      note.className = "watchlist-note";
      context.className = "watchlist-note__facts";
      [report.scope + ".", "Market-wide completeness: " + report.market_completeness + ".",
        report.stocks.some(function (s) { return s.review_status === "not_independently_reviewed"; }) ?
          "Independent review: pending." : "", "Data release " + report.release_id.slice(0, 12) + "."
      ].filter(Boolean).forEach(function (text) {
        var fact = document.createElement("span");
        fact.textContent = text + " "; context.append(fact);
      });
      var method = document.createElement("details"), label = document.createElement("summary");
      var explanation = document.createElement("p");
      method.className = "watchlist-note__method";
      label.textContent = "How fund houses are counted here";
      explanation.textContent = "A fund house counts as adding shares when its compared funds together hold more shares" +
        " than in the previous month. The stock detail counts a fund house as buying if any one of its" +
        " funds bought, so one fund house can count as both buying and selling there.";
      method.append(label, explanation); note.append(context, method);
      watchResults.replaceChildren(note);
      delete watchResults.dataset.stale;
      watchMessage("");
      report.stocks.forEach(function (stock) { watchResults.append(watchItem(stock)); });
      body.release = report.release_id; body.rules = report.rule_version;
      download.dataset.reportRequest = JSON.stringify(body);
      download.href = "/watchlist/report"; download.hidden = false;
    } catch (e) {
      if (sequence === watchlistSequence && e.name !== "AbortError") {
        watchResults.dataset.stale = "true";
        watchStatus.replaceChildren(stateNode("error", "Could not refresh your watchlist. " + e.message +
          " Any previous results below have not been refreshed. " + watchStorageWarning, refreshWatchlist));
      }
    } finally {
      if (sequence === watchlistSequence) watchResults.removeAttribute("aria-busy");
    }
  }
  if (watchResults) {
    document.getElementById("watchlist-report").addEventListener("click", async function (event) {
      event.preventDefault();
      var payload = event.currentTarget.dataset.reportRequest;
      if (!payload) return;
      watchMessage("Preparing your monthly report…");
      try {
        var result = await load("watchlist-download", "/watchlist/report", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: payload });
        if (!result.ok) throw responseError(result);
        var report = JSON.parse(payload);
        downloadText(result.body, "text/markdown",
          "findit-" + report.month + "-" + report.release.slice(0, 12) + ".md");
        watchMessage("Report ready. Check your browser’s downloads.");
      } catch (e) {
        if (e.name !== "AbortError") watchMessage("Report download failed: " + e.message);
      }
    });
    document.getElementById("watchlist-export").addEventListener("click", function () {
      downloadText(JSON.stringify({ version: 1, stocks: watchlist }, null, 2), "application/json",
                   "findit-watchlist.json");
    });
    document.getElementById("watchlist-import").addEventListener("change", async function (event) {
      try {
        var file = event.target.files[0]; if (!file) return;
        if (file.size > 20000) throw new Error("Watchlist file is too large.");
        var imported = validateWatchlist(JSON.parse(await file.text()));
        var combined = Array.from(new Set(watchlist.concat(imported))).sort();
        if (combined.length > 100) throw new Error("Keep at most 100 stocks.");
        watchlist = combined;
        saveWatchlist(); updateFollowButtons(); refreshWatchlist();
      } catch (e) { watchMessage("Import failed: " + e.message); }
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

  function responseError(response) {
    var message = response.status === 503 ? "The data service is temporarily unavailable. Please retry." :
      response.status === 429 ? "Too many requests. Wait a moment and retry." :
      response.status === 409 ? "The data release changed. Reload the page to continue." :
      response.status === 404 ? "The requested data is unavailable for this selection." :
      "The request failed. Please retry.";
    return new Error(message);
  }

  // Fetch text, cancelling any earlier request for the same slot so a slow
  // response can never overwrite a newer one.
  async function load(slot, url, options) {
    if (inflight[slot]) inflight[slot].abort();
    var ctrl = new AbortController();
    inflight[slot] = ctrl;
    try {
      var res = await fetch(url, Object.assign({}, options, { signal: ctrl.signal }));
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
    if (data.get("page") && data.get("page") !== "1") q.set("page", data.get("page"));
    q.set("equity_only", document.getElementById("equity-only").checked ? "1" : "0");
    q.set("active_only", document.getElementById("active-only").checked ? "1" : "0");
    return q;
  }

  // Re-render after the reader changed the view, from the controls or history.
  function viewChanged(sideChanged, monthChanged) {
    // Buying and selling rank in opposite directions; each starts from its ranking.
    if (sideChanged) sorting = null;
    refreshView();
    refreshWatchlist();
    // The summary follows the month once the reader has opened one.
    if (monthChanged) {
      if (stockPicker) stockPicker.close();
      if (pickedScheme) showSummary();
    }
  }

  // The select's option for value, else its first option, as the server defaults.
  function optionValue(select, value) {
    var values = Array.from(select.options).map(function (option) { return option.value; });
    return values.includes(value) ? value : values[0] || "";
  }

  function restoreHistory() {
    followHash();
    if (!form) return;
    var previousMonth = currentMonth(), previousQuery = viewQuery().toString();
    var fields = form.elements, previousSide = fields.side.value;
    var q = new URL(location.href).searchParams;
    fields.month.value = optionValue(fields.month, q.get("month"));
    fields.side.value = q.get("side") === "sell" ? "sell" : "buy";
    fields.limit.value = optionValue(fields.limit, q.get("limit"));
    fields.cols.value = q.get("cols") === "all" ? "all" : "core";
    if (fields.page) fields.page.value = q.get("page") || "1";
    document.getElementById("equity-only").checked = !q.has("equity_only") || q.get("equity_only") === "1";
    document.getElementById("active-only").checked = !q.has("active_only") || q.get("active_only") === "1";
    var monthChanged = previousMonth !== currentMonth();
    if (monthChanged || previousQuery !== viewQuery().toString()) {
      viewChanged(previousSide !== fields.side.value, monthChanged);
    } else if (inflight.view) {
      // Supersede a pending request too, so it cannot rewrite this history entry.
      refreshView();
    }
  }

  function setUrlParam(key, value) {
    var url = new URL(location.href);
    if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
    history.replaceState(null, "", url);
  }

  // -- ranking table sort ---------------------------------------------------
  // Sorting reorders the rows on show. A row keeps its ranking number, ties keep
  // ranking order, and a cell without a value sorts last in either direction.
  var sorting = null;
  var textOrder = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });
  var firstDirection = { name: "ascending", filing: "ascending" };
  var directionWords = { name: ["A to Z", "Z to A"], filing: ["newest first", "oldest first"] };
  function sortRows(key, direction) {
    var table = document.getElementById("consensus-table");
    var heading = table && table.querySelector('th[data-sort-key="' + key + '"]');
    if (!heading) return false;
    var index = heading.cellIndex, body = table.tBodies[0], text = key === "name";
    var rows = Array.from(body.rows).map(function (row) {
      var raw = row.cells[index].dataset.sort || "", number = parseFloat(raw);
      return { row: row, rank: Number(row.dataset.rank),
               value: text ? raw : Number.isFinite(number) ? number : null };
    });
    rows.sort(function (a, b) {
      if ((a.value === null) !== (b.value === null)) return a.value === null ? 1 : -1;
      var order = a.value === null ? 0 : text ? textOrder.compare(a.value, b.value) : a.value - b.value;
      return (direction === "ascending" ? order : -order) || a.rank - b.rank;
    });
    body.append.apply(body, rows.map(function (entry) { return entry.row; }));
    table.querySelectorAll("th[data-sort-key]").forEach(function (th) {
      if (th === heading) th.setAttribute("aria-sort", direction);
      else th.removeAttribute("aria-sort");
    });
    var select = document.getElementById("sort-select");
    if (select) select.value = key + ":" + direction;
    return true;
  }
  function sortBy(key, direction) {
    sorting = { key: key, direction: direction };
    sortRows(key, direction);
  }

  // -- notable lists ----------------------------------------------------------
  // The panel starts closed so the ranking stays on the first screen; a reader
  // who opens it finds it open again on this device.
  var notableGrid = null;
  function enhanceNotable() {
    var panel = document.getElementById("notable");
    var grid = notableGrid = panel ? panel.querySelector(".notable__grid") : null;
    if (!panel) return;
    try {
      if (localStorage.getItem("findit-notable-open") === "1") panel.open = true;
    } catch (e) { /* Optional storage. */ }
    panel.addEventListener("toggle", function () {
      try { localStorage.setItem("findit-notable-open", panel.open ? "1" : "0"); } catch (e) { /* Optional storage. */ }
      updateOverflow(grid);
    });
    if (grid) grid.addEventListener("scroll", function () { updateOverflow(grid); }, { passive: true });
    updateOverflow(grid);
  }

  // A rendered month view: the notable lists, then the ranking table.
  function enhanceView() {
    enhanceNotable();
    enhanceTable();
  }

  // Headings become sort buttons, and the narrow-screen select lists the same
  // choices, because the card layout there hides the headings.
  function enhanceTable() {
    updateFollowButtons();
    var table = document.getElementById("consensus-table");
    if (!table) return;
    var select = document.getElementById("sort-select");
    table.querySelectorAll("th[data-sort-key]").forEach(function (th) {
      var key = th.dataset.sortKey, first = firstDirection[key] || "descending";
      var button = document.createElement("button");
      button.type = "button";
      button.className = "sort";
      button.append.apply(button, Array.from(th.childNodes));
      th.append(button);
      button.addEventListener("click", function () {
        var current = th.getAttribute("aria-sort");
        sortBy(key, current ? (current === "ascending" ? "descending" : "ascending") : first);
      });
      var words = directionWords[key] || ["low to high", "high to low"];
      [first, first === "ascending" ? "descending" : "ascending"].forEach(function (direction) {
        var option = document.createElement("option");
        option.value = key + ":" + direction;
        option.textContent = th.dataset.sortLabel + ", " + words[direction === "ascending" ? 0 : 1];
        select.append(option);
      });
    });
    select.addEventListener("change", function () {
      var choice = select.value.split(":");
      sortBy(choice[0], choice[1]);
    });
    select.closest(".sort-select").hidden = false;
    // A refresh keeps the reader's sort when its column is still shown.
    if (!(sorting && sortRows(sorting.key, sorting.direction))) {
      sorting = null;
      var ranked = table.querySelector("th[aria-sort]");
      select.value = ranked.dataset.sortKey + ":" + ranked.getAttribute("aria-sort");
    }
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
      if (!r.ok) throw responseError(r);
      view.innerHTML = r.body;
      delete view.dataset.stale;
      if (coverageOpen) view.querySelector("#coverage-details").open = true;
      viewStatus.className = "controls__status";
      viewStatus.textContent = "";
      q.set("month", month);
      var url = new URL(location.href);
      url.search = q.toString();
      history.replaceState(null, "", url);
      var coverageLink = document.querySelector("[data-coverage-link]");
      if (coverageLink) {
        var coverageUrl = new URL(coverageLink.href, location.href);
        coverageUrl.searchParams.set("month", month);
        coverageUrl.searchParams.set("active_only", q.get("active_only"));
        coverageLink.href = coverageUrl.toString();
      }
      // Only wire the fragment this request rendered; cancelled requests must
      // not wrap the current table's sort buttons or register listeners again.
      enhanceView();
      if (hadFocus) {
        var title = document.getElementById("consensus-title");
        if (title) title.focus();
      }
    } catch (e) {
      if (e.name === "AbortError") return;
      viewStatus.className = "controls__status";
      view.dataset.stale = "true";
      viewStatus.replaceChildren(stateNode("error", "Could not load " + monthLabel() + ". " + e.message +
        " Previous results remain below and have not been refreshed.", refreshView));
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
        if (item.kind) {
          var kind = document.createElement("span");
          kind.className = "combo__kind";
          kind.textContent = item.kind;
          li.append(kind);
        }
        // mousedown would blur the input and close the list before the click lands.
        li.addEventListener("mousedown", function (e) { e.preventDefault(); });
        li.addEventListener("click", function (event) { pick(i, event); });
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
        var maximum = opts.maxResults || 20;
        var note = results.length > maximum ? "Showing the first " + maximum + " of " + results.length +
          " matches. Type more to narrow the list." : results.length ? "" : opts.emptyText(query);
        show(results.slice(0, maximum), note);
      } catch (e) {
        if (e.name === "AbortError" || mine !== seq) return;
        show([], "Could not load suggestions (" + e.message + ").");
      }
    }

    function pick(i, event) {
      var item = items[i];
      close();
      input.value = item.label;
      opts.onPick(item, event);
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
        if (!list.hidden && active >= 0) { e.preventDefault(); pick(active, e); }
        else if (!list.hidden && items.length === 1) { e.preventDefault(); pick(0, e); }
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
      if (!r.ok) throw responseError(r);
      stockBox.innerHTML = r.body;
      updateFollowButtons();
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

  // A side drawer: a native modal dialog that slides in, except when opened from
  // the keyboard, and keeps focus containment through its short exit.
  // hooks.onClose runs when a close starts; hooks.onClosed once it has finished.
  function drawer(element, hooks) {
    hooks = hooks || {};
    var sequence = 0, closeTimer = null, opener = null;
    function open(instant) {
      var mine = ++sequence;
      clearTimeout(closeTimer);
      closeTimer = null;
      delete element.dataset.endingStyle;
      if (instant) element.dataset.instant = "";
      else delete element.dataset.instant;
      if (element.open) { delete element.dataset.startingStyle; return; }
      opener = document.activeElement;
      if (!instant) element.dataset.startingStyle = "";
      element.showModal();
      if (!instant) {
        // Start in the closed pose, then let the transition retarget normally.
        void element.offsetWidth;
        requestAnimationFrame(function () {
          if (mine === sequence && element.open) delete element.dataset.startingStyle;
        });
      }
    }
    function close(instant) {
      if (!element.open) return;
      var mine = ++sequence;
      clearTimeout(closeTimer);
      if (hooks.onClose) hooks.onClose();
      delete element.dataset.startingStyle;
      if (instant) {
        element.dataset.instant = "";
        element.close();
        return;
      }
      delete element.dataset.instant;
      element.dataset.endingStyle = "";
      // Keep native focus containment until the short exit has finished.
      closeTimer = setTimeout(function () {
        if (mine === sequence && element.open) element.close();
      }, 150);
    }
    element.addEventListener("transitionend", function (event) {
      if (event.target === element && event.propertyName === "opacity" &&
          element.dataset.endingStyle !== undefined) element.close();
    });
    element.addEventListener("cancel", function (event) {
      event.preventDefault();
      close(true);
    });
    element.addEventListener("close", function () {
      // A native close event can arrive after the drawer has already reopened.
      if (element.open) return;
      ++sequence;
      clearTimeout(closeTimer);
      closeTimer = null;
      delete element.dataset.startingStyle;
      delete element.dataset.endingStyle;
      delete element.dataset.instant;
      if (hooks.onClosed) hooks.onClosed();
      if (opener && opener.isConnected) opener.focus();
      opener = null;
    });
    // A click on the backdrop lands on the dialog element itself.
    element.addEventListener("click", function (e) { if (e.target === element) close(isKeyboardAction(e)); });
    return { open: open, close: close };
  }

  function abortStock() { if (inflight.stock) inflight.stock.abort(); }
  var stockDrawer = drawer(dialog, { onClose: abortStock, onClosed: function () {
    abortStock();
    setUrlParam("stock", "");
  } });
  document.getElementById("stock-dialog-close").addEventListener("click", function (event) {
    stockDrawer.close(isKeyboardAction(event));
  });
  function openStock(query, instant) {
    query = (query || "").trim();
    if (!query) return;
    if (searchDialog && searchDialog.open) searchDialog.close();
    stockDrawer.open(instant);
    loadStock(query);
  }

  // -- reading guide --------------------------------------------------------
  // The notes move into a drawer, so every view can open them in place.
  var guideDialog = document.getElementById("guide-dialog");
  var guideNotes = document.getElementById("guide-notes");
  if (guideDialog && guideNotes) {
    document.getElementById("guide-box").append(guideNotes);
    var guideDrawer = drawer(guideDialog);
    document.getElementById("guide-dialog-close").addEventListener("click", function (event) {
      guideDrawer.close(isKeyboardAction(event));
    });
    document.addEventListener("click", function (event) {
      var link = event.target.closest && event.target.closest("a[href='#notes-title']");
      if (!link) return;
      event.preventDefault();
      guideDrawer.open(isKeyboardAction(event));
    });
  }

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
      if (!r.ok) throw responseError(r);
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
  var schemeInput = document.getElementById("scheme-search");
  var schemes = [];
  function matchWords(query, text) {
    var words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return words.every(function (w) { return text.indexOf(w) !== -1; });
  }
  function showScheme(item) {
    schemeInput.value = item.label;
    pickedScheme = item.value;
    showSummary();
  }
  var schemesLoading = null;
  function mapSchemes(data) {
    return data.map(function (s) {
      return { value: String(s.id === undefined ? s.scheme_id : s.id), label: s.label,
               detail: (s.amc || s.amc_name) + (s.votes ? "" : " · index/debt, does not vote"),
               haystack: (s.label + " " + (s.amc || s.amc_name)).toLowerCase() };
    });
  }
  if (schemeData) schemes = mapSchemes(JSON.parse(schemeData.textContent));
  async function ensureSchemes() {
    if (schemeData || !schemeInput) return;
    if (!schemesLoading) schemesLoading = (async function () {
      var r = await load("schemes", "/api/schemes");
      if (!r.ok) throw responseError(r);
      schemes = mapSchemes(JSON.parse(r.body).schemes);
    }());
    try { await schemesLoading; }
    catch (e) { schemesLoading = null; throw e; }
  }
  if (schemeInput) {
    combobox({
      input: schemeInput,
      list: document.getElementById("scheme-options"),
      status: document.getElementById("scheme-search-status"),
      minChars: 0,
      maxResults: 12,
      source: async function (query) {
        await ensureSchemes();
        return schemes.filter(function (s) { return matchWords(query, s.haystack); });
      },
      emptyText: function (query) { return "No fund matches “" + query + "”."; },
      onType: clearSummary,
      onPick: showScheme
    });
    document.getElementById("summary-form").addEventListener("submit", function (e) { e.preventDefault(); });
  }

  // -- search -----------------------------------------------------------------
  // One search for stocks, individual funds and views, in a dialog opened from
  // the top bar, with Ctrl+K or Command+K, or with / outside a text field.
  var stockForm = document.getElementById("stock-form");
  var searchOpen = document.getElementById("search-open");
  function openSearch() {
    if (!searchDialog || searchDialog.open) return;
    searchDialog.showModal();
    stockInput.select();
  }
  if (stockForm) {
    var stockInput = document.getElementById("stock-search");
    var stockPicker = combobox({
      input: stockInput,
      list: document.getElementById("stock-options"),
      status: document.getElementById("stock-search-status"),
      minChars: 1,
      delay: 150,
      source: async function (query) {
        await ensureSchemes();
        var views = navLinks.filter(function (link) {
          return matchWords(query, (link.dataset.title || "").toLowerCase());
        }).map(function (link) { return { kind: "View", value: link.getAttribute("href"), label: link.dataset.title, link: link }; });
        var funds = schemes.filter(function (s) { return matchWords(query, s.haystack); }).slice(0, 6)
          .map(function (s) { return Object.assign({ kind: "Fund" }, s); });
        if (query.length < 2) return funds.concat(views);
        var q = new URLSearchParams({ q: query });
        if (currentMonth()) q.set("month", currentMonth());
        var r = await load("suggest", "/api/stocks/search?" + q);
        if (!r.ok) throw responseError(r);
        return JSON.parse(r.body).results.map(function (s) {
          var held = s.schemes_holding ? "held by " + s.schemes_holding + " fund" + (s.schemes_holding === 1 ? "" : "s")
                                       : "not held";
          return { value: s.isin, label: s.name,
                   detail: s.isin + (s.industry ? " · " + s.industry : "") + " · " + held + " in " + monthLabel() };
        }).concat(funds, views);
      },
      emptyText: function (query) { return "No stock, fund or view matches “" + query + "”."; },
      onPick: function (item, event) {
        if (!item.kind) { openStock(item.value, isKeyboardAction(event)); return; }
        searchDialog.close();
        stockDrawer.close(true);
        if (guideDrawer) guideDrawer.close(true);
        if (item.kind === "View") { goToView(item.link); return; }
        var funds = navLinkFor("#scheme-summary");
        if (funds) goToView(funds);
        showScheme(item);
      }
    });
    // Enter without choosing a suggestion looks the text up as a stock.
    stockForm.addEventListener("submit", function (e) { e.preventDefault(); openStock(stockInput.value, keyboardAction); });
  }
  if (searchDialog && searchOpen) {
    searchOpen.hidden = false;
    if (/Mac|iPhone|iPad/.test(typeof navigator === "undefined" ? "" : navigator.platform || "")) {
      searchOpen.querySelector("[data-shortcut]").textContent = "⌘K";
    }
    searchOpen.addEventListener("click", openSearch);
    document.addEventListener("keydown", function (e) {
      var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName || "") || e.target.isContentEditable;
      if (((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        openSearch();
      }
    });
    // A click on the backdrop lands on the dialog element itself.
    searchDialog.addEventListener("click", function (e) { if (e.target === searchDialog) searchDialog.close(); });
  }

  // -- view controls ----------------------------------------------------------
  if (form) {
    form.addEventListener("change", function (e) {
      if (form.elements.page) form.elements.page.value = "1";
      viewChanged(e.target.name === "side", e.target.id === "month-select");
    });
    form.addEventListener("submit", function (e) { e.preventDefault(); refreshView(); });
  }

  document.addEventListener("click", function (e) {
    var follow = e.target.closest("[data-watch-stock]");
    if (follow) {
      setFollowed(follow.dataset.watchStock, !watchlist.includes(follow.dataset.watchStock));
      return;
    }
    var link = e.target.closest(".stock-link");
    if (link) { openStock(link.dataset.isin, isKeyboardAction(e)); return; }
    // A click anywhere else on a ranking row opens its stock, unless it selects text.
    var row = e.target.closest("#consensus-table tbody tr");
    if (row && !e.target.closest("a, button, summary, input, select, label") &&
        !(window.getSelection && String(window.getSelection()))) {
      var rowLink = row.querySelector(".stock-link");
      rowLink.focus();
      openStock(rowLink.dataset.isin, false);
      return;
    }
    // "Show all" and the column toggle carry their view in the href; apply it
    // to the form so the URL and later filter changes keep it.
    var viewLink = e.target.closest("[data-view-link]");
    if (viewLink && form) {
      e.preventDefault();
      if (form.elements.page) form.elements.page.value = "1";
      new URL(viewLink.href).searchParams.forEach(function (value, key) {
        if ((key === "limit" || key === "cols" || key === "page") && form.elements[key]) form.elements[key].value = value;
      });
      refreshView();
    }
  });

  enhanceView();
  var initialStock = new URL(location.href).searchParams.get("stock");
  if (initialStock) openStock(initialStock, true);
})();
