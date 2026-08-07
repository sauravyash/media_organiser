/* Shared renderer for the read-only movie and music dashboards.
 *
 * Each page calls initDashboard() with an endpoint and one or more tabs. A tab
 * says which array in the payload it lists and how to turn one record into a
 * row. Everything else — stats, filtering, search, paging, copy buttons — is
 * handled here so the two dashboards stay consistent.
 */
(function (global) {
  "use strict";

  var PAGE_SIZE = 150;

  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatSize(bytes) {
    if (!bytes || bytes < 0) return "";
    // Units run through PB and the index is clamped, so a multi-TB library
    // never renders as "undefined" (same fix as upload.html's formatSize).
    var units = ["B", "KB", "MB", "GB", "TB", "PB"];
    var i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return (Math.round((bytes / Math.pow(1024, i)) * 10) / 10) + " " + units[i];
  }

  function kindLabel(kind) {
    return kind.replace(/-/g, " ");
  }

  function initDashboard(config) {
    var mount = document.querySelector(config.mount || "#app");
    var state = {
      data: null,
      tab: config.tabs[0].id,
      search: "",
      severity: "",
      kind: "",
      onlyIssues: true,
      limit: PAGE_SIZE
    };

    function activeTab() {
      for (var i = 0; i < config.tabs.length; i++) {
        if (config.tabs[i].id === state.tab) return config.tabs[i];
      }
      return config.tabs[0];
    }

    function records() {
      if (!state.data) return [];
      return state.data[activeTab().listKey] || [];
    }

    function summary() {
      if (!state.data) return null;
      return state.data[activeTab().summaryKey] || null;
    }

    function matches(entry, row) {
      if (state.onlyIssues && (!entry.issues || !entry.issues.length)) return false;
      if (state.severity) {
        var found = (entry.issues || []).some(function (i) { return i.severity === state.severity; });
        if (!found) return false;
      }
      if (state.kind) {
        var hasKind = (entry.issues || []).some(function (i) { return i.kind === state.kind; });
        if (!hasKind) return false;
      }
      if (state.search) {
        var needle = state.search.toLowerCase();
        var haystack = [row.title, row.detail]
          .concat((row.meta || []).map(function (m) { return m.value; }))
          .concat((entry.issues || []).map(function (i) { return i.kind + " " + i.message + " " + (i.suggestion || ""); }))
          .join(" ")
          .toLowerCase();
        if (haystack.indexOf(needle) === -1) return false;
      }
      return true;
    }

    function renderStats() {
      var s = summary();
      if (!s) return "";
      var sev = s.by_severity || {};
      var tiles = [
        { value: s.total, label: activeTab().unit || "items", cls: "" },
        { value: s.clean, label: "look fine", cls: "ok" },
        { value: sev.high || 0, label: "high severity", cls: "high" },
        { value: sev.medium || 0, label: "medium severity", cls: "medium" },
        { value: sev.low || 0, label: "low severity", cls: "low" },
        { value: s.issues, label: "recommended changes", cls: "" }
      ];
      return '<div class="stats">' + tiles.map(function (t) {
        return '<div class="stat ' + t.cls + '"><div class="value">' + esc(t.value) +
          '</div><div class="label">' + esc(t.label) + "</div></div>";
      }).join("") + "</div>";
    }

    function renderTabs() {
      if (config.tabs.length < 2) return "";
      return '<div class="tabs">' + config.tabs.map(function (t) {
        return '<button type="button" data-tab="' + esc(t.id) + '"' +
          (t.id === state.tab ? ' class="active"' : "") + ">" + esc(t.label) + "</button>";
      }).join("") + "</div>";
    }

    function renderControls() {
      var s = summary() || { by_kind: {} };
      var kinds = Object.keys(s.by_kind || {});
      return '<div class="controls">' +
        '<input type="search" id="dash-search" placeholder="Search title, path or recommendation…" value="' + esc(state.search) + '">' +
        '<select id="dash-severity"><option value="">All severities</option>' +
        ["high", "medium", "low"].map(function (sv) {
          return '<option value="' + sv + '"' + (state.severity === sv ? " selected" : "") + ">" + sv + "</option>";
        }).join("") + "</select>" +
        '<select id="dash-kind"><option value="">All issue types</option>' +
        kinds.map(function (k) {
          return '<option value="' + esc(k) + '"' + (state.kind === k ? " selected" : "") + ">" +
            esc(kindLabel(k)) + " (" + s.by_kind[k] + ")</option>";
        }).join("") + "</select>" +
        '<label><input type="checkbox" id="dash-only"' + (state.onlyIssues ? " checked" : "") +
        "> only show items needing a change</label>" +
        "</div>";
    }

    function renderIssues(issues) {
      if (!issues || !issues.length) return "";
      return '<ul class="issues">' + issues.map(function (issue) {
        var suggestion = "";
        if (issue.suggestion) {
          suggestion = '<div class="suggestion"><span class="arrow">→</span><span class="suggestion-text">' +
            esc(issue.suggestion) + '</span>' +
            '<button type="button" class="copy-btn" data-copy="' + esc(issue.suggestion) + '">copy</button></div>';
        }
        return '<li class="issue"><div class="issue-head">' +
          '<span class="badge ' + esc(issue.severity) + '">' + esc(issue.severity) + "</span>" +
          '<span class="badge kind">' + esc(kindLabel(issue.kind)) + "</span>" +
          "</div><div>" + esc(issue.message) + "</div>" + suggestion + "</li>";
      }).join("") + "</ul>";
    }

    function renderEntry(entry, row) {
      var sev = entry.severity || "none";
      var meta = (row.meta || []).filter(function (m) { return m.value !== "" && m.value !== null && m.value !== undefined; });
      return '<div class="entry sev-' + esc(sev) + '">' +
        '<div class="entry-head"><span class="entry-title">' + esc(row.title) + "</span>" +
        (entry.issues && entry.issues.length
          ? ""
          : '<span class="badge ok">ok</span>') +
        '<span class="entry-meta">' + meta.map(function (m) {
          return "<span>" + esc(m.label ? m.label + " " + m.value : m.value) + "</span>";
        }).join("") + "</span></div>" +
        (row.detail ? '<div class="entry-files">' + esc(row.detail) + "</div>" : "") +
        renderIssues(entry.issues) +
        "</div>";
    }

    function render() {
      if (!state.data) {
        mount.innerHTML = '<div class="state">Scanning…</div>';
        return;
      }

      if (config.unavailable) {
        var blocker = config.unavailable(state.data);
        if (blocker) {
          mount.innerHTML = renderTabs() + blocker;
          bind();
          return;
        }
      }

      var tab = activeTab();
      var all = records();
      var rows = [];
      for (var i = 0; i < all.length; i++) {
        var row = tab.row(all[i]);
        if (matches(all[i], row)) rows.push({ entry: all[i], row: row });
      }

      var shown = rows.slice(0, state.limit);
      var body;
      // A tab can fail on its own while the rest of the payload is fine.
      var tabError = tab.errorKey ? state.data[tab.errorKey] : null;
      if (tabError) {
        body = '<div class="state error">Could not load ' + esc(tab.label.toLowerCase()) +
          ': ' + esc(tabError) + "</div>";
        mount.innerHTML = renderTabs() + body;
        bind();
        return;
      }
      if (!all.length) {
        body = '<div class="state">' + esc(config.emptyMessage || "Nothing here yet.") + "</div>";
      } else if (!rows.length) {
        body = '<div class="state">Nothing matches the current filters.</div>';
      } else {
        body = '<div class="entry-list">' + shown.map(function (r) {
          return renderEntry(r.entry, r.row);
        }).join("") + "</div>";
        if (rows.length > shown.length) {
          body += '<div class="more"><button type="button" class="btn" id="dash-more">' +
            "Show more (" + (rows.length - shown.length) + " remaining)</button></div>";
        }
      }

      mount.innerHTML = renderTabs() + renderStats() + renderControls() + body;
      bind();
    }

    function bind() {
      var search = document.getElementById("dash-search");
      if (search) {
        search.addEventListener("input", function (e) {
          state.search = e.target.value;
          state.limit = PAGE_SIZE;
          render();
          var again = document.getElementById("dash-search");
          if (again) {
            again.focus();
            again.setSelectionRange(again.value.length, again.value.length);
          }
        });
      }

      var severity = document.getElementById("dash-severity");
      if (severity) {
        severity.addEventListener("change", function (e) {
          state.severity = e.target.value;
          state.limit = PAGE_SIZE;
          render();
        });
      }

      var kind = document.getElementById("dash-kind");
      if (kind) {
        kind.addEventListener("change", function (e) {
          state.kind = e.target.value;
          state.limit = PAGE_SIZE;
          render();
        });
      }

      var only = document.getElementById("dash-only");
      if (only) {
        only.addEventListener("change", function (e) {
          state.onlyIssues = e.target.checked;
          state.limit = PAGE_SIZE;
          render();
        });
      }

      var more = document.getElementById("dash-more");
      if (more) {
        more.addEventListener("click", function () {
          state.limit += PAGE_SIZE;
          render();
        });
      }

      Array.prototype.forEach.call(mount.querySelectorAll("[data-tab]"), function (btn) {
        btn.addEventListener("click", function () {
          state.tab = btn.getAttribute("data-tab");
          state.kind = "";
          state.limit = PAGE_SIZE;
          render();
        });
      });

      Array.prototype.forEach.call(mount.querySelectorAll(".copy-btn"), function (btn) {
        btn.addEventListener("click", function () {
          var text = btn.getAttribute("data-copy");
          var done = function () {
            btn.textContent = "copied";
            setTimeout(function () { btn.textContent = "copy"; }, 1200);
          };
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done, function () { btn.textContent = "failed"; });
          } else {
            btn.textContent = "failed";
          }
        });
      });
    }

    function load(refresh) {
      var button = document.getElementById("rescan");
      if (button) { button.disabled = true; button.textContent = "Scanning…"; }
      if (refresh) {
        state.data = null;
        render();
      }
      fetch(config.endpoint + (refresh ? "?refresh=1" : ""), { headers: { Accept: "application/json" } })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          state.data = data;
          if (config.onLoad) config.onLoad(data);
          render();
        })
        .catch(function (err) {
          mount.innerHTML = '<div class="state error">Could not load ' + esc(config.endpoint) +
            ": " + esc(err.message) + "</div>";
        })
        .finally(function () {
          if (button) { button.disabled = false; button.textContent = "Rescan"; }
        });
    }

    var rescan = document.getElementById("rescan");
    if (rescan) {
      rescan.addEventListener("click", function () { load(true); });
    }

    render();
    load(false);
  }

  global.initDashboard = initDashboard;
  global.dashboardUtils = { esc: esc, formatSize: formatSize };
})(window);
