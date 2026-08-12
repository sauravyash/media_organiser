/* The write-side pages: bulk mechanical fixes, duplicate triage, and trash.
 *
 * All three post to /api/library/fix/apply, which journals every operation, so
 * anything these pages do can be undone from /library/trash. Nothing here
 * deletes: "trash" moves a file aside.
 *
 * Style matches dashboard.js (ES5, no build step) so the two can share helpers.
 */
(function (global) {
  "use strict";

  var esc = global.dashboardUtils.esc;
  var formatSize = global.dashboardUtils.formatSize;

  function kindLabel(kind) {
    return String(kind || "").replace(/-/g, " ");
  }

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data && data.error ? data.error : "HTTP " + r.status);
        return data;
      });
    });
  }

  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  var toastTimer = null;
  function toast(message, bad) {
    var existing = document.querySelector(".toast");
    if (existing) existing.parentNode.removeChild(existing);
    var el = document.createElement("div");
    el.className = "toast" + (bad ? " bad" : "");
    el.textContent = message;
    document.body.appendChild(el);
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 4000);
  }

  function renderResults(result) {
    var lines = (result.results || []).map(function (r) {
      var what = r.dst || r.src || r.path || "";
      return '<div class="result-line"><span class="badge ' + esc(r.status) + '">' +
        esc(r.status) + '</span><span class="what">' + esc(what) + "</span>" +
        (r.reason ? '<span class="why">' + esc(r.reason) + "</span>" : "") + "</div>";
    }).join("");
    var undo = result.batch
      ? ' <button type="button" class="btn" data-undo="' + esc(result.batch) + '">Undo this batch</button>'
      : "";
    return '<div class="results"><h3>' +
      esc(result.applied || result.restored || 0) + " applied · " +
      esc(result.skipped || 0) + " skipped · " +
      esc(result.errors || 0) + " errors" + undo + "</h3>" + lines + "</div>";
  }

  function bindUndo(scope, afterUndo) {
    Array.prototype.forEach.call(scope.querySelectorAll("[data-undo]"), function (btn) {
      btn.addEventListener("click", function () {
        var batch = btn.getAttribute("data-undo");
        btn.disabled = true;
        btn.textContent = "Undoing…";
        postJSON("/api/library/trash/undo", { batch: batch })
          .then(function (res) {
            toast(res.restored + " restored, " + res.skipped + " skipped");
            if (afterUndo) afterUndo(res);
          })
          .catch(function (err) {
            toast(err.message, true);
            btn.disabled = false;
            btn.textContent = "Undo this batch";
          });
      });
    });
  }

  // ------------------------------------------------------------------
  // /library/fix — bulk mechanical renames and NFO writes
  // ------------------------------------------------------------------

  function initFixPage(mount) {
    var el = document.querySelector(mount);
    var state = { plan: null, selected: {}, open: {}, busy: false, result: null };

    function selectable(action) {
      return !action.collision && !action.missing;
    }

    function selectedActions() {
      var out = [];
      (state.plan.groups || []).forEach(function (group) {
        group.actions.forEach(function (action) {
          if (state.selected[action.id]) out.push(action);
        });
      });
      return out;
    }

    function renderRow(action) {
      var blocked = !selectable(action);
      var reason = action.collision ? "target already exists"
        : (action.missing ? "source is gone" : "");
      return '<tr class="' + (blocked ? "blocked" : "") + '">' +
        '<td class="pick"><input type="checkbox" data-act="' + esc(action.id) + '"' +
        (state.selected[action.id] ? " checked" : "") + (blocked ? " disabled" : "") + "></td>" +
        '<td class="folder">' + esc(action.folder || "") + "</td>" +
        '<td class="from">' + esc(action.src_label) + "</td>" +
        '<td class="arrow">→</td>' +
        '<td class="to">' + esc(action.dst_label) + (reason ? '  <span class="badge skipped">' +
          esc(reason) + "</span>" : "") + "</td>" +
        "</tr>";
    }

    function renderGroup(group) {
      var open = state.open[group.kind];
      var body = "";
      if (open) {
        body = '<div class="group-body"><table class="rows"><thead><tr>' +
          '<th class="pick"></th><th>Folder</th><th>From</th><th></th><th>To</th>' +
          "</tr></thead><tbody>" +
          group.actions.map(renderRow).join("") + "</tbody></table></div>";
      }
      var chosen = group.actions.filter(function (a) { return state.selected[a.id]; }).length;
      return '<div class="group">' +
        '<div class="group-head" data-group="' + esc(group.kind) + '">' +
        '<span class="group-title">' + esc(kindLabel(group.kind)) + "</span>" +
        '<span class="group-count">' + group.count + " to apply" +
        (group.collisions ? " · " + group.collisions + " blocked" : "") + "</span>" +
        '<span class="spacer"></span>' +
        '<span class="group-count">' + chosen + " selected</span>" +
        '<button type="button" class="btn" data-toggle-group="' + esc(group.kind) + '">' +
        (chosen ? "Clear" : "Select all") + "</button>" +
        "<span>" + (open ? "▾" : "▸") + "</span>" +
        "</div>" + body + "</div>";
    }

    function render() {
      if (!state.plan) {
        el.innerHTML = '<div class="state">Scanning…</div>';
        return;
      }
      var groups = state.plan.groups || [];
      if (!groups.length) {
        el.innerHTML = '<div class="state">Nothing mechanical left to fix. ' +
          'Anything remaining needs a decision — try <a href="/library/triage">triage</a>.</div>';
        return;
      }

      var chosen = selectedActions();
      var html =
        '<div class="note">Applied in dependency order regardless of the order you tick them: ' +
        'folders are renamed before the files inside them, and NFOs are written last so they ' +
        'record the final path. Every operation is journalled and can be undone from ' +
        '<a href="/library/trash">trash</a>.</div>' +
        '<div class="stats">' +
        '<div class="stat"><div class="value">' + state.plan.total + '</div>' +
        '<div class="label">available fixes</div></div>' +
        '<div class="stat medium"><div class="value">' + state.plan.collisions + '</div>' +
        '<div class="label">blocked by a name clash</div></div>' +
        '<div class="stat ok"><div class="value">' + chosen.length + '</div>' +
        '<div class="label">selected</div></div>' +
        "</div>" +
        groups.map(renderGroup).join("") +
        '<div class="sticky-apply"><span class="count">' + chosen.length +
        ' selected</span>' +
        '<button type="button" class="btn" id="fix-select-all">Select every unblocked fix</button>' +
        '<button type="button" class="btn btn-primary" id="fix-apply"' +
        (chosen.length && !state.busy ? "" : " disabled") + ">" +
        (state.busy ? "Applying…" : "Apply " + chosen.length) + "</button></div>" +
        (state.result ? renderResults(state.result) : "");

      el.innerHTML = html;
      bind();
    }

    function bind() {
      Array.prototype.forEach.call(el.querySelectorAll("[data-group]"), function (head) {
        head.addEventListener("click", function (e) {
          if (e.target.hasAttribute("data-toggle-group")) return;
          var kind = head.getAttribute("data-group");
          state.open[kind] = !state.open[kind];
          render();
        });
      });

      Array.prototype.forEach.call(el.querySelectorAll("[data-toggle-group]"), function (btn) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          var kind = btn.getAttribute("data-toggle-group");
          var group = null;
          (state.plan.groups || []).forEach(function (g) { if (g.kind === kind) group = g; });
          if (!group) return;
          var anySelected = group.actions.some(function (a) { return state.selected[a.id]; });
          group.actions.forEach(function (a) {
            state.selected[a.id] = anySelected ? false : selectable(a);
          });
          render();
        });
      });

      Array.prototype.forEach.call(el.querySelectorAll("[data-act]"), function (box) {
        box.addEventListener("change", function () {
          state.selected[box.getAttribute("data-act")] = box.checked;
          render();
        });
      });

      var all = document.getElementById("fix-select-all");
      if (all) {
        all.addEventListener("click", function () {
          (state.plan.groups || []).forEach(function (g) {
            g.actions.forEach(function (a) { state.selected[a.id] = selectable(a); });
          });
          render();
        });
      }

      var apply = document.getElementById("fix-apply");
      if (apply) {
        apply.addEventListener("click", function () {
          var actions = selectedActions();
          if (!actions.length) return;
          state.busy = true;
          state.result = null;
          render();
          postJSON("/api/library/fix/apply", { actions: actions })
            .then(function (result) {
              state.result = result;
              state.selected = {};
              toast(result.applied + " applied, " + result.skipped + " skipped");
              return load(true);
            })
            .catch(function (err) { toast(err.message, true); })
            .finally(function () { state.busy = false; render(); });
        });
      }

      bindUndo(el, function () { load(true); });
    }

    function load(refresh) {
      return getJSON("/api/library/fix/plan" + (refresh ? "?refresh=1" : ""))
        .then(function (plan) {
          state.plan = plan;
          render();
        })
        .catch(function (err) {
          el.innerHTML = '<div class="state error">Could not load the plan: ' + esc(err.message) + "</div>";
        });
    }

    var rescan = document.getElementById("rescan");
    if (rescan) {
      rescan.addEventListener("click", function () {
        state.plan = null;
        render();
        load(true);
      });
    }

    render();
    load(false);
  }

  // ------------------------------------------------------------------
  // /library/triage — pick which copy of a movie survives
  // ------------------------------------------------------------------

  function initTriagePage(mount) {
    var el = document.querySelector(mount);
    var state = {
      folders: [], index: 0, detail: null,
      // One keeper per edition (the movie a file is a copy of) rather than one
      // per folder: a 1961 and a 1996 "101 Dalmatians" each survive their own
      // triage instead of one being trashed for the other.
      keepers: {}, merged: false, busy: false, done: 0
    };

    function current() {
      return state.folders[state.index] || null;
    }

    // A CD1/CD2 half is not a duplicate of its other half — every part stays.
    function isLocked(video) {
      return !!video.part;
    }

    // "Treat as one movie" collapses the split for the case a year cannot catch:
    // two copies of the same film where one filename carries the wrong year.
    function editionOf(video) {
      return state.merged ? 1 : (video.edition || 1);
    }

    function editionCount(detail) {
      return state.merged ? 1 : (detail.editions || 1);
    }

    function chooseDefaultKeepers(detail) {
      var keepers = {};
      // inspect_folder sorts biggest first within each edition.
      (detail.videos || []).forEach(function (v) {
        if (isLocked(v)) return;
        var edition = editionOf(v);
        if (!keepers.hasOwnProperty(edition)) keepers[edition] = v.path;
      });
      return keepers;
    }

    function isKeeper(video) {
      return !isLocked(video) && state.keepers[editionOf(video)] === video.path;
    }

    function removableVideos(detail) {
      return (detail.videos || []).filter(function (v) {
        return !isLocked(v) && !isKeeper(v);
      });
    }

    function renderSpecs(detail) {
      var biggest = 0;
      (detail.videos || []).forEach(function (v) { if (v.size > biggest) biggest = v.size; });
      var pickIndex = 0;
      var split = editionCount(detail) > 1;
      var seen = {};

      var rows = (detail.videos || []).map(function (video) {
        var locked = isLocked(video);
        var keeper = isKeeper(video);
        var edition = editionOf(video);
        var head = "";
        if (split && !seen[edition]) {
          seen[edition] = true;
          head = '<tr class="edition-head"><td colspan="7">' +
            esc(video.edition_label || "year unknown") +
            " — kept as a separate movie</td></tr>";
        }
        var key = "";
        if (!locked) {
          pickIndex += 1;
          key = pickIndex <= 9 ? String(pickIndex) : "";
        }
        var flags = [];
        if (video.identical_group) {
          flags.push('<span class="badge low">identical #' + video.identical_group + "</span>");
        }
        if (locked) flags.push('<span class="badge low">' + esc(video.part) + "</span>");
        if (!video.nfo) flags.push('<span class="badge skipped">no nfo</span>');

        return head + '<tr class="' + (locked ? "locked" : (keeper ? "keeper" : "")) + '">' +
          '<td class="pick">' + (locked
            ? '<span class="badge low">keep</span>'
            : '<input type="radio" name="keeper-' + esc(edition) + '" value="' + esc(video.path) + '"' +
              (keeper ? " checked" : "") + ">") + "</td>" +
          '<td class="pick">' + (key ? '<span class="key">' + key + "</span>" : "") + "</td>" +
          '<td class="name">' + esc(video.name) + " " + flags.join(" ") + "</td>" +
          '<td>' + esc(video.container) + "</td>" +
          '<td>' + esc(video.quality) + "</td>" +
          '<td>' + esc(video.year || "—") + "</td>" +
          '<td class="num ' + (video.size === biggest ? "biggest" : "") + '">' +
          esc(formatSize(video.size)) + "</td>" +
          "</tr>";
      }).join("");

      return '<table class="specs"><thead><tr>' +
        "<th></th><th></th><th>File</th><th>Container</th><th>Quality</th><th>Year</th>" +
        '<th class="num">Size</th></tr></thead><tbody>' + rows + "</tbody></table>";
    }

    function renderCard() {
      var folder = current();
      var detail = state.detail;
      if (!detail) return '<div class="state">Reading folder…</div>';

      var removable = removableVideos(detail);
      var allIdentical = removable.length > 0 && removable.every(function (v) {
        return v.identical_group;
      });

      var warning = "";
      if (!removable.length && editionCount(detail) > 1) {
        warning = '<div class="note">Nothing to remove — the files here are different ' +
          "movies that share a title, one copy of each. Skip to the next folder.</div>";
      } else if (!removable.length) {
        warning = '<div class="note">Nothing to remove here — every file is a separate part ' +
          "of a multi-disc movie. Skip to the next folder.</div>";
      } else if (allIdentical) {
        warning = '<div class="note">Byte-identical to the file you are keeping ' +
          "(same size, same fingerprint). Safe to remove.</div>";
      } else {
        warning = '<div class="note warn">These files are <strong>not</strong> identical — ' +
          "check the sizes and qualities before removing. They may be different cuts, or " +
          "different movies filed together.</div>";
      }

      // The years are only filenames. Offer the way out for a copy labelled with
      // the wrong year, which would otherwise be held back as its own movie.
      var mergeNote = "";
      if (detail.editions > 1 && !state.merged) {
        mergeNote = '<div class="note">Split into ' + detail.editions +
          " movies by the year in each filename, so one copy of each is kept. " +
          "If a year is wrong and these are really the same film, merge them.</div>";
      } else if (detail.editions > 1) {
        mergeNote = '<div class="note warn">Merged: every file is being treated as a copy ' +
          "of one movie, ignoring the years in the names.</div>";
      }
      var mergeButton = detail.editions > 1
        ? '<button type="button" class="btn" id="tri-merge">' +
          (state.merged ? "Split by year again" : "Treat as one movie") + "</button>"
        : "";

      return '<div class="triage-card">' +
        "<h2>" + esc(folder.folder) + "</h2>" +
        '<div class="path">' + esc(detail.path) + "</div>" +
        renderSpecs(detail) +
        mergeNote +
        warning +
        '<div class="triage-actions">' +
        '<button type="button" class="btn btn-danger" id="tri-apply"' +
        (removable.length && !state.busy ? "" : " disabled") + ">" +
        (state.busy ? "Working…" : "Trash " + removable.length + " other" +
          (removable.length === 1 ? "" : "s")) + "</button>" +
        mergeButton +
        '<button type="button" class="btn" id="tri-skip">Skip</button>' +
        '<button type="button" class="btn" id="tri-prev"' + (state.index ? "" : " disabled") +
        ">Previous</button>" +
        '<span class="hint">1–9 pick · enter trash the rest · s skip · ← → move</span>' +
        "</div></div>";
    }

    function render() {
      if (!state.folders.length) {
        el.innerHTML = '<div class="state">Nothing needs triage. ' +
          'Mechanical fixes live on the <a href="/library/fix">fix page</a>.</div>';
        return;
      }
      if (state.index >= state.folders.length) {
        el.innerHTML = '<div class="state">Done — ' + state.done +
          " folder(s) resolved this session. " +
          '<a href="/library/trash">Review the trash</a> if you want to undo anything.</div>';
        return;
      }
      var pct = Math.round((state.index / state.folders.length) * 100);
      el.innerHTML =
        '<div class="triage-shell">' +
        '<div class="triage-progress"><span>' + (state.index + 1) + " / " +
        state.folders.length + '</span><span class="triage-bar"><span style="width:' +
        pct + '%"></span></span><span>' + state.done + " resolved</span></div>" +
        renderCard() + "</div>";
      bind();
    }

    function bind() {
      Array.prototype.forEach.call(el.querySelectorAll('input[name^="keeper-"]'), function (radio) {
        radio.addEventListener("change", function () {
          state.keepers[radio.name.slice("keeper-".length)] = radio.value;
          render();
        });
      });

      var merge = document.getElementById("tri-merge");
      if (merge) merge.addEventListener("click", function () {
        state.merged = !state.merged;
        state.keepers = chooseDefaultKeepers(state.detail);
        render();
      });

      var apply = document.getElementById("tri-apply");
      if (apply) apply.addEventListener("click", applyCurrent);
      var skip = document.getElementById("tri-skip");
      if (skip) skip.addEventListener("click", function () { advance(1); });
      var prev = document.getElementById("tri-prev");
      if (prev) prev.addEventListener("click", function () { advance(-1); });
    }

    function applyCurrent() {
      if (state.busy || !state.detail) return;
      var actions = removableVideos(state.detail)
        .map(function (v) {
          return {
            verb: "trash-file",
            kind: "multiple-videos",
            src: v.path,
            size: v.size,
            mtime: v.mtime
          };
        });
      if (!actions.length) return;

      state.busy = true;
      render();
      postJSON("/api/library/fix/apply", { actions: actions })
        .then(function (result) {
          state.done += 1;
          var message = result.applied + " moved to trash";
          if (result.skipped) message += ", " + result.skipped + " skipped";
          toast(message, result.errors > 0);
          state.busy = false;
          advance(1);
        })
        .catch(function (err) {
          state.busy = false;
          toast(err.message, true);
          render();
        });
    }

    function advance(step) {
      var next = state.index + step;
      if (next < 0) next = 0;
      state.index = next;
      state.detail = null;
      state.keepers = {};
      state.merged = false;
      render();
      if (state.index < state.folders.length) loadDetail();
    }

    function loadDetail() {
      var folder = current();
      if (!folder) return;
      getJSON("/api/library/movies/triage/folder?path=" + encodeURIComponent(folder.path))
        .then(function (detail) {
          state.detail = detail;
          state.keepers = chooseDefaultKeepers(detail);
          render();
        })
        .catch(function (err) {
          el.innerHTML = '<div class="state error">Could not read ' + esc(folder.folder) +
            ": " + esc(err.message) + '</div>';
        });
    }

    document.addEventListener("keydown", function (e) {
      if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
      if (!state.detail) return;
      if (e.key >= "1" && e.key <= "9") {
        var pickable = (state.detail.videos || []).filter(function (v) { return !isLocked(v); });
        var chosen = pickable[parseInt(e.key, 10) - 1];
        if (chosen) {
          state.keepers[editionOf(chosen)] = chosen.path;
          render();
        }
      } else if (e.key === "Enter") {
        applyCurrent();
      } else if (e.key === "s" || e.key === "ArrowRight") {
        advance(1);
      } else if (e.key === "ArrowLeft") {
        advance(-1);
      }
    });

    function load(refresh) {
      getJSON("/api/library/movies/triage" + (refresh ? "?refresh=1" : ""))
        .then(function (data) {
          state.folders = data.folders || [];
          state.index = 0;
          render();
          if (state.folders.length) loadDetail();
        })
        .catch(function (err) {
          el.innerHTML = '<div class="state error">Could not load triage: ' + esc(err.message) + "</div>";
        });
    }

    var rescan = document.getElementById("rescan");
    if (rescan) rescan.addEventListener("click", function () { load(true); });

    el.innerHTML = '<div class="state">Scanning…</div>';
    load(false);
  }

  // ------------------------------------------------------------------
  // /library/trash — batches, undo, permanent delete
  // ------------------------------------------------------------------

  function initTrashPage(mount) {
    var el = document.querySelector(mount);

    function verbSummary(verbs) {
      return Object.keys(verbs || {}).map(function (v) {
        return kindLabel(v) + " ×" + verbs[v];
      }).join(", ");
    }

    function render(data) {
      var batches = data.batches || [];
      if (!batches.length) {
        el.innerHTML = '<div class="note">Nothing has been changed yet. Fixes applied from ' +
          '<a href="/library/fix">the fix page</a> or <a href="/library/triage">triage</a> ' +
          "show up here, and can be undone.</div>";
        return;
      }

      var rows = batches.map(function (batch) {
        return '<tr class="' + (batch.fully_undone ? "spent" : "") + '">' +
          '<td class="id">' + esc(batch.batch) + "</td>" +
          "<td>" + esc((batch.ts || "").replace("T", " ").replace("+00:00", " UTC")) + "</td>" +
          "<td>" + esc(verbSummary(batch.verbs)) + "</td>" +
          '<td class="num">' + batch.trashed + "</td>" +
          '<td class="num">' + esc(formatSize(batch.reclaimable)) + "</td>" +
          '<td class="act">' +
          (batch.fully_undone
            ? '<span class="badge low">undone</span>'
            : '<button type="button" class="btn" data-undo="' + esc(batch.batch) + '">Undo</button>' +
              (batch.trashed
                ? ' <button type="button" class="btn btn-danger" data-empty="' + esc(batch.batch) +
                  '">Delete for good</button>'
                : "")) +
          "</td></tr>";
      }).join("");

      el.innerHTML =
        '<div class="stats">' +
        '<div class="stat"><div class="value">' + batches.length + "</div>" +
        '<div class="label">batches</div></div>' +
        '<div class="stat"><div class="value">' + data.total_trashed + "</div>" +
        '<div class="label">files in trash</div></div>' +
        '<div class="stat medium"><div class="value">' + esc(formatSize(data.total_reclaimable)) +
        '</div><div class="label">reclaimable</div></div>' +
        "</div>" +
        '<div class="note">Trashed files sit in <code>' + esc(data.trash_dir) +
        "</code> on the same filesystem as the library, so nothing was copied. " +
        "A <code>.ignore</code> marker keeps media servers from indexing them. " +
        "Undo puts everything back; deleting for good does not.</div>" +
        '<table class="batches"><thead><tr>' +
        "<th>Batch</th><th>When</th><th>Operations</th>" +
        '<th class="num">Trashed</th><th class="num">Reclaimable</th><th></th>' +
        "</tr></thead><tbody>" + rows + "</tbody></table>";

      bindUndo(el, function () { load(); });

      Array.prototype.forEach.call(el.querySelectorAll("[data-empty]"), function (btn) {
        btn.addEventListener("click", function () {
          var batch = btn.getAttribute("data-empty");
          if (!global.confirm("Permanently delete the files trashed in " + batch +
              "?\n\nThis cannot be undone.")) return;
          btn.disabled = true;
          postJSON("/api/library/trash/empty", { batch: batch })
            .then(function (res) {
              toast(res.deleted + " deleted, " + formatSize(res.freed) + " freed");
              load();
            })
            .catch(function (err) { toast(err.message, true); btn.disabled = false; });
        });
      });
    }

    function load() {
      getJSON("/api/library/trash")
        .then(render)
        .catch(function (err) {
          el.innerHTML = '<div class="state error">Could not load the trash: ' + esc(err.message) + "</div>";
        });
    }

    var rescan = document.getElementById("rescan");
    if (rescan) rescan.addEventListener("click", load);

    el.innerHTML = '<div class="state">Loading…</div>';
    load();
  }

  global.initFixPage = initFixPage;
  global.initTriagePage = initTriagePage;
  global.initTrashPage = initTrashPage;
})(window);
