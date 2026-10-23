// Reusable accessible autocomplete. Sources and selection behavior belong to the caller.
(function (root) {
  "use strict";
  function combobox(opts) {
    var input = opts.input, list = opts.list, status = opts.status;
    var items = [], active = -1, seq = 0, timer = null;

    function close() {
      ++seq;
      clearTimeout(timer);
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      input.removeAttribute("aria-busy");
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
      input.setAttribute("aria-busy", "true");
      status.textContent = "Loading suggestions…";
      try {
        var results = await opts.source(query);
        if (mine !== seq) return;
        var maximum = opts.maxResults || 20;
        var note = results.length > maximum ? "Showing the first " + maximum + " of " + results.length +
          " matches. Type more to narrow the list." : results.length ? "" : opts.emptyText(query);
        show(results.slice(0, maximum), note);
      } catch (e) {
        if (e.name === "AbortError" || mine !== seq) return;
        show([], "Could not load suggestions (" + e.message + "). Type again to retry.");
      } finally {
        if (mine === seq) input.removeAttribute("aria-busy");
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

  root.FindIt = root.FindIt || {};
  root.FindIt.combobox = combobox;
})(window);
