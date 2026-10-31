/* 课引 CourseCite 前端。只渲染后端给好的顺序，不在这里重排。 */
(function () {
  "use strict";

  var SNAPSHOT_URL = window.COURSECITE_SNAPSHOT || null;
  var snapshot = null;
  var localLog = [];
  var lastHits = {};

  function $(id) { return document.getElementById(id); }

  function normalize(text) {
    return String(text || "").trim().replace(/\s+/g, " ");
  }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function queryKey(text, course, lectureMax) {
    return normalize(text) + "|" + (course || "") + "|" + (lectureMax == null ? "" : lectureMax);
  }

  function citationLabel(c) {
    if (!c) return "（无出处）";
    var page = c.page == null ? "p.—" : "p." + c.page;
    var section = ((c.section || "") + " " + (c.section_title || "")).trim();
    return c.course + " · 第 " + c.lecture + " 讲 · " + section + " · " + page;
  }

  function showStatus(message, kind) {
    var box = $("status");
    box.hidden = !message;
    box.textContent = message || "";
    box.className = "status " + (kind || "");
  }

  async function loadSnapshot() {
    if (!SNAPSHOT_URL) return null;
    if (snapshot) return snapshot;
    var resp = await fetch(SNAPSHOT_URL, { cache: "no-store" });
    if (!resp.ok) throw new Error("快照加载失败：" + resp.status);
    snapshot = await resp.json();
    return snapshot;
  }

  async function search(text, course, lectureMax) {
    if (SNAPSHOT_URL) {
      var snap = await loadSnapshot();
      var wanted = queryKey(text, course, lectureMax);
      var entry = (snap.queries || []).find(function (item) {
        return queryKey(item.query, item.filters && item.filters.course, item.filters && item.filters.max_lecture) === wanted;
      });
      if (!entry) {
        var available = (snap.queries || []).map(function (item) { return item.query; });
        var err = new Error("离线快照中没有这个问题，试试下面的示例查询。");
        err.available = available;
        throw err;
      }
      return {
        query: entry.query,
        filters: entry.filters,
        elapsed_ms: entry.elapsed_ms,
        store_mode: "snapshot",
        hits: entry.results || []
      };
    }
    var params = new URLSearchParams({ q: text });
    if (course) params.set("course", course);
    if (lectureMax != null && lectureMax !== "") params.set("lecture_max", String(lectureMax));
    var response = await fetch("/api/search?" + params.toString(), { cache: "no-store" });
    var payload = await response.json();
    if (!response.ok) {
      var error = new Error(payload.error || "检索失败");
      error.available = payload.available || [];
      throw error;
    }
    return payload;
  }

  function renderHit(hit) {
    lastHits[hit.chunk_id] = hit;
    var channels = (hit.channels || []).join(", ") || "-";
    var full = hit.text ? '<pre class="fulltext">' + esc(hit.text) + "</pre>" : "";
    return (
      '<article class="hit">' +
        '<div class="rank">' + esc(hit.rank) + "</div>" +
        '<div class="body">' +
          '<div class="cite">' + esc(citationLabel(hit.citation)) + "</div>" +
          '<p class="snippet">' + esc(hit.snippet) + "</p>" +
          '<div class="meta">' +
            '<span class="score">score ' + Number(hit.score).toFixed(4) + "</span>" +
            '<span class="channel">' + esc(channels) + "</span>" +
            '<span class="source">' + esc(hit.citation.path) + ":" + esc(hit.citation.line_start) + "</span>" +
            '<button class="link" data-id="' + esc(hit.chunk_id) + '">展开原文</button>' +
          "</div>" +
          full +
        "</div>" +
      "</article>"
    );
  }

  function renderResults(response) {
    var hits = response.hits || [];
    if (!hits.length) {
      $("results").innerHTML = '<p class="empty">没有命中。换个说法，或去掉过滤条件。</p>';
      return;
    }
    $("results").innerHTML = hits.map(renderHit).join("");
    showStatus("命中 " + hits.length + " 条 · " + response.store_mode + " · " + response.elapsed_ms + " ms", "ok");
  }

  async function toggleFulltext(button) {
    var id = button.getAttribute("data-id");
    var hit = lastHits[id];
    var article = button.closest(".hit");
    var pre = article.querySelector(".fulltext");
    if (pre) { pre.hidden = !pre.hidden; return; }
    if (!hit) return;
    if (hit.text) {
      pre = document.createElement("pre");
      pre.className = "fulltext";
      pre.textContent = hit.text;
      article.querySelector(".body").appendChild(pre);
      return;
    }
    try {
      var resp = await fetch("/api/obtain/" + encodeURIComponent(id));
      var chunk = await resp.json();
      if (!resp.ok) throw new Error(chunk.error || "取原文失败");
      pre = document.createElement("pre");
      pre.className = "fulltext";
      pre.textContent = chunk.text;
      article.querySelector(".body").appendChild(pre);
    } catch (err) {
      showStatus(err.message, "error");
    }
  }

  function renderLog(records) {
    if (!records || !records.length) {
      $("log").textContent = "暂无记录。";
      return;
    }
    $("log").innerHTML = records.map(function (record) {
      return '<div class="log-row"><span>' + esc(record.created_at || "本次") + "</span>" +
        "<strong>" + esc(record.query) + "</strong>" +
        "<span>" + esc(record.elapsed_ms) + " ms · " + esc(record.store_mode || "") + "</span></div>";
    }).join("");
  }

  async function refreshLog() {
    if (SNAPSHOT_URL) { renderLog(localLog); return; }
    try {
      var resp = await fetch("/api/log?limit=8", { cache: "no-store" });
      if (resp.ok) { renderLog(await resp.json()); return; }
    } catch (err) { /* 页面下方日志失败不影响检索结果 */ }
    renderLog(localLog);
  }

  function renderSuggestions(items) {
    $("suggestions").innerHTML = (items || []).map(function (text) {
      return '<button class="chip" type="button">' + esc(text) + "</button>";
    }).join("");
    Array.prototype.forEach.call($("suggestions").querySelectorAll(".chip"), function (chip) {
      chip.addEventListener("click", function () {
        $("q").value = chip.textContent;
        $("search-form").dispatchEvent(new Event("submit", { cancelable: true }));
      });
    });
  }

  async function loadMeta() {
    try {
      var meta;
      if (SNAPSHOT_URL) {
        var snap = await loadSnapshot();
        meta = {
          store_mode: "snapshot",
          stats: snap.index || {},
          suggestions: (snap.queries || []).map(function (item) { return item.query; })
        };
      } else {
        var resp = await fetch("/api/meta", { cache: "no-store" });
        meta = await resp.json();
      }
      $("mode-badge").textContent = meta.store_mode === "live" ? "LiveStore · 真查 seekdb" : "SnapshotStore · 快照回放";
      $("mode-badge").className = "badge " + (meta.store_mode === "live" ? "live" : "snapshot");
      renderSuggestions(meta.suggestions);
      if (meta.store_mode === "snapshot") {
        showStatus("当前是预生成快照回放，只支持示例查询；自由检索请本地运行 LiveStore。", "info");
      }
    } catch (err) {
      showStatus("初始化失败：" + err.message, "error");
    }
  }

  async function onSubmit(event) {
    event.preventDefault();
    var text = $("q").value.trim();
    if (!text) { $("q").focus(); return; }
    var course = $("course").value || null;
    var lectureValue = $("lecture-max").value;
    var lectureMax = lectureValue === "" ? null : Number(lectureValue);
    showStatus("检索中…", "info");
    $("results").innerHTML = "";
    try {
      var response = await search(text, course, lectureMax);
      renderResults(response);
      if (!SNAPSHOT_URL) {
        await refreshLog();
      } else {
        localLog.unshift({
          created_at: "本次",
          query: response.query,
          elapsed_ms: response.elapsed_ms,
          store_mode: response.store_mode
        });
        renderLog(localLog);
      }
    } catch (err) {
      showStatus(err.message, "error");
      if (err.available && err.available.length) renderSuggestions(err.available);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    $("search-form").addEventListener("submit", onSubmit);
    $("results").addEventListener("click", function (event) {
      var button = event.target.closest("button.link");
      if (button) toggleFulltext(button);
    });
    loadMeta();
    refreshLog();
  });
})();
