(() => {
  "use strict";

  const importForm = document.querySelector("#import-form");
  const pathsInput = document.querySelector("#import-paths");
  const importCourse = document.querySelector("#import-course");
  const importSubmit = document.querySelector("#import-submit");
  const importFeedback = document.querySelector("#import-feedback");
  const importFiles = document.querySelector("#import-files");

  const searchForm = document.querySelector("#search-form");
  const queryInput = document.querySelector("#search-q");
  const searchCourse = document.querySelector("#search-course");
  const searchSubmit = document.querySelector("#search-submit");
  const results = document.querySelector("#results");
  const resultsMeta = document.querySelector("#results-meta");
  const examples = document.querySelector("#examples");
  const reviewBody = document.querySelector("#review-body");
  const reviewRefresh = document.querySelector("#review-refresh");

  const TERM_SEPARATORS = /[\s，,。；;：:、？！?!（）()【】\[\]"'“”‘’/\\|]+/;

  let searching = false;
  let reviewLoading = false;
  let lastSearch = null;

  function splitPaths(value) {
    return value
      .split(/[\n,，;；]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function setButtonBusy(button, busy, busyText) {
    if (busy) {
      button.dataset.idleLabel = button.textContent;
      button.textContent = busyText;
      button.disabled = true;
      return;
    }
    button.textContent = button.dataset.idleLabel || button.textContent;
    button.disabled = false;
  }

  function setFeedback(kind, message) {
    importFeedback.className = `feedback ${kind}`;
    importFeedback.textContent = message;
    importFeedback.hidden = false;
  }

  async function readError(response, fallback) {
    try {
      const payload = await response.json();
      const detail = payload && payload.detail;
      if (typeof detail === "string" && detail.trim()) {
        return detail;
      }
      if (Array.isArray(detail)) {
        const messages = detail.map((item) => item && item.msg).filter(Boolean);
        if (messages.length) {
          return messages.join("；");
        }
      }
    } catch (_error) {
      // 响应不是 JSON 时使用兜底文案
    }
    return fallback;
  }

  function renderImportFiles(files) {
    importFiles.replaceChildren();
    files.forEach((file) => {
      const row = document.createElement("li");
      row.className = `file-row ${file.status === "ok" ? "is-ok" : "is-error"}`;

      const name = document.createElement("span");
      name.className = "file-name";
      name.textContent = file.source_file || "(未知文件)";

      const count = document.createElement("span");
      count.className = "file-count";
      count.textContent = file.status === "ok" ? `${file.chunks} 块` : "失败";

      if (file.error) {
        name.title = file.error;
        count.title = file.error;
      }

      row.append(name, count);
      importFiles.append(row);
    });
    importFiles.hidden = files.length === 0;
  }

  importForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const paths = splitPaths(pathsInput.value);
    const course = importCourse.value.trim();
    if (!paths.length) {
      setFeedback("error", "请填写至少一个目录或文件路径。");
      pathsInput.focus();
      return;
    }
    if (!course) {
      setFeedback("error", "请填写课程标签，检索时可按它过滤。");
      importCourse.focus();
      return;
    }

    setButtonBusy(importSubmit, true, "导入中…");
    setFeedback("pending", "正在切块、向量化并写入 seekdb，首次导入会稍慢…");
    importFiles.hidden = true;

    try {
      const response = await fetch("/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ paths, course }),
      });
      if (!response.ok) {
        throw new Error(await readError(response, `导入失败（HTTP ${response.status}）`));
      }

      const data = await response.json();
      const files = Array.isArray(data.files) ? data.files : [];
      const failed = files.filter((file) => file.status !== "ok").length;
      renderImportFiles(files);

      if (data.ok && data.total_chunks > 0) {
        setFeedback(
          "success",
          `已写入 ${data.total_chunks} 个片段，来自 ${files.length} 个文件。重复导入会覆盖同课程、同文件的旧片段。`
        );
      } else if (data.total_chunks > 0) {
        setFeedback("warning", `写入 ${data.total_chunks} 个片段，${failed} 项失败，详情见下方列表。`);
      } else {
        setFeedback("error", "没有写入任何片段，请检查路径与文件内容。");
      }

      if (data.course) {
        searchCourse.value = data.course;
      }
    } catch (error) {
      setFeedback("error", error.message || "导入失败，请确认服务已启动。");
    } finally {
      setButtonBusy(importSubmit, false, "");
    }
  });

  function showState(kind, title, detail) {
    results.replaceChildren();
    const card = document.createElement("div");
    card.className = `state-card${kind === "error" ? " is-error" : ""}`;

    if (kind === "loading") {
      const spinner = document.createElement("span");
      spinner.className = "spinner";
      spinner.setAttribute("aria-hidden", "true");
      card.append(spinner);
    } else {
      const mark = document.createElement("span");
      mark.className = "state-mark";
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = kind === "error" ? "!" : "寻";
      card.append(mark);
    }

    const heading = document.createElement("p");
    heading.className = "state-title";
    heading.textContent = title;

    const text = document.createElement("p");
    text.className = "state-detail";
    text.textContent = detail;

    card.append(heading, text);
    results.append(card);
  }

  function highlightTokens(term, query) {
    const unique = new Map();
    const add = (value) => {
      const cleaned = (value || "").trim();
      if (cleaned.length >= 2) {
        const key = cleaned.toLocaleLowerCase();
        if (!unique.has(key)) {
          unique.set(key, cleaned);
        }
      }
    };

    (term || "").split(TERM_SEPARATORS).forEach(add);
    (query || "").split(/\s+/).forEach(add);
    ((term || "").match(/[A-Za-z][A-Za-z0-9+#._-]*/g) || []).forEach(add);

    if (!unique.size && term && term.trim()) {
      unique.set(term.trim().toLocaleLowerCase(), term.trim());
    }

    return Array.from(unique.values()).sort((left, right) => right.length - left.length);
  }

  function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function appendHighlighted(container, text, tokens) {
    if (!tokens.length) {
      container.textContent = text;
      return;
    }

    const pattern = new RegExp(tokens.map(escapeRegExp).join("|"), "gi");
    let cursor = 0;
    let match = pattern.exec(text);

    while (match !== null) {
      if (match.index > cursor) {
        container.append(document.createTextNode(text.slice(cursor, match.index)));
      }
      const mark = document.createElement("mark");
      mark.textContent = match[0];
      container.append(mark);
      cursor = match.index + match[0].length;

      if (match[0].length === 0) {
        pattern.lastIndex += 1;
      }
      match = pattern.exec(text);
    }

    if (cursor < text.length) {
      container.append(document.createTextNode(text.slice(cursor)));
    }
  }

  function formatDistance(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(4) : "—";
  }

  function renderResults(items, term, elapsedMs, query) {
    results.replaceChildren();

    const tokens = highlightTokens(term, query);
    const elapsed = Math.max(1, Math.round(elapsedMs));
    resultsMeta.textContent = term
      ? `找到 ${items.length} 条 · ${elapsed} ms · 关键词「${term}」`
      : `找到 ${items.length} 条 · ${elapsed} ms`;

    if (!items.length) {
      showState(
        "empty",
        "没有找到相关段落",
        "换个问法，或确认课程标签是否过滤过窄。full-text 分支匹配不到时，语义向量仍会兜底。"
      );
      return;
    }

    const fragment = document.createDocumentFragment();
    items.forEach((item, index) => {
      const article = document.createElement("article");
      article.className = "result";

      const rank = document.createElement("span");
      rank.className = "rank";
      rank.setAttribute("aria-hidden", "true");
      rank.textContent = String(index + 1).padStart(2, "0");

      const body = document.createElement("div");
      body.className = "result-body";

      const top = document.createElement("div");
      top.className = "result-top";

      const source = document.createElement("span");
      source.className = "source";
      source.textContent = item.source_file || "(未标注来源)";

      const location = document.createElement("span");
      location.className = "location";
      location.textContent = item.location || "未标注位置";

      const tag = document.createElement("span");
      const both = item.matched_by === "fulltext+vector";
      tag.className = `tag ${both ? "tag-both" : "tag-vector"}`;
      tag.textContent = both ? "全文 + 向量" : "语义向量";

      top.append(source, location, tag);

      if (item.doc_id) {
        const actions = document.createElement("div");
        actions.className = "result-actions";

        const weakButton = document.createElement("button");
        weakButton.type = "button";
        weakButton.className = "mini-btn";
        weakButton.dataset.feedbackVerdict = "weak";
        weakButton.dataset.docId = item.doc_id;
        weakButton.textContent = "标记不懂";
        weakButton.title = "把这条资料写入 PowerContext 薄弱点记忆";

        const masteredButton = document.createElement("button");
        masteredButton.type = "button";
        masteredButton.className = "mini-btn";
        masteredButton.dataset.feedbackVerdict = "mastered";
        masteredButton.dataset.docId = item.doc_id;
        masteredButton.textContent = "已掌握";
        masteredButton.title = "把这条资料记为已掌握";

        actions.append(weakButton, masteredButton);
        top.append(actions);
      }

      const snippet = document.createElement("p");
      snippet.className = "snippet";
      appendHighlighted(snippet, item.text || "", tokens);

      const foot = document.createElement("div");
      foot.className = "result-foot";

      const distanceLabel = document.createElement("span");
      distanceLabel.textContent = "相关度距离";
      distanceLabel.title = "混合检索距离，越小越相关";

      const distance = document.createElement("span");
      distance.className = "distance";
      distance.textContent = formatDistance(item.distance);

      foot.append(distanceLabel, distance);

      if (item.doc_id) {
        const docId = document.createElement("span");
        docId.className = "docid";
        docId.textContent = `#${String(item.doc_id).slice(0, 8)}`;
        docId.title = item.doc_id;
        foot.append(docId);
      }

      body.append(top, snippet, foot);
      article.append(rank, body);
      fragment.append(article);
    });

    results.append(fragment);
  }

  function decodeTerm(header, fallback) {
    if (!header) {
      return fallback;
    }
    try {
      return decodeURIComponent(header);
    } catch (_error) {
      return fallback;
    }
  }

  function renderReviewLoading() {
    reviewBody.replaceChildren();
    const wrapper = document.createElement("div");
    wrapper.className = "review-loading";
    const spinner = document.createElement("span");
    spinner.className = "spinner spinner-small";
    spinner.setAttribute("aria-hidden", "true");
    wrapper.append(spinner, document.createTextNode("正在读取跨会话记忆…"));
    reviewBody.append(wrapper);
  }

  function renderReviewEmpty(title, detail) {
    reviewBody.replaceChildren();
    const box = document.createElement("div");
    box.className = "review-empty";

    const heading = document.createElement("p");
    heading.textContent = title;

    const text = document.createElement("span");
    text.textContent = detail;

    box.append(heading, text);
    reviewBody.append(box);
  }

  function renderReviewError(message) {
    reviewBody.replaceChildren();
    const box = document.createElement("div");
    box.className = "review-empty is-error";

    const heading = document.createElement("p");
    heading.textContent = "PowerContext 未连接";

    const text = document.createElement("span");
    text.textContent = "请先在 127.0.0.1:8000 启动 PowerContext（powercontext server run），再重试。";
    text.title = message || "";

    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "review-retry";
    retry.textContent = "重试";
    retry.addEventListener("click", () => loadReview());

    box.append(heading, text, retry);
    reviewBody.append(box);
  }

  function renderReview(data) {
    reviewBody.replaceChildren();
    const suggestions = Array.isArray(data.suggestions) ? data.suggestions : [];

    if (!suggestions.length) {
      renderReviewEmpty(
        data.status === "empty" ? "暂无记忆，先去学习吧" : "暂无复习建议",
        data.status === "empty"
          ? "在检索结果上点「标记不懂」，这条薄弱点会写进 PowerContext。"
          : "PowerContext 没有返回可解析的复习条目。"
      );
      return;
    }

    const meta = document.createElement("div");
    meta.className = "review-meta";
    const count = document.createElement("span");
    count.textContent = `${suggestions.length} 条薄弱点记忆`;
    const source = document.createElement("span");
    source.textContent = "PowerContext · 重启不丢";
    meta.append(count, source);

    const list = document.createElement("ul");
    list.className = "review-list";
    suggestions.forEach((suggestion) => {
      const item = document.createElement("li");
      item.className = "review-item";

      const text = document.createElement("p");
      text.className = "review-text";
      text.textContent = suggestion.text || "";
      text.title = suggestion.text || "";
      item.append(text);

      const artifact = suggestion.artifact;
      if (artifact && artifact.artifact_id) {
        const family = artifact.family || "atomic-memory";
        const revision = artifact.revision == null ? "" : `@${artifact.revision}`;
        item.title = `${family}/${artifact.artifact_id}${revision}`;
      }
      list.append(item);
    });

    reviewBody.append(meta, list);
  }

  async function loadReview({ quiet = false } = {}) {
    if (reviewLoading) {
      return;
    }
    reviewLoading = true;
    reviewRefresh.disabled = true;
    if (!quiet) {
      renderReviewLoading();
    }

    try {
      const response = await fetch("/review", { headers: { Accept: "application/json" } });
      if (!response.ok) {
        throw new Error(await readError(response, `读取复习建议失败（HTTP ${response.status}）`));
      }
      const data = await response.json();
      renderReview(data);
    } catch (error) {
      renderReviewError(error.message || "无法连接 PowerContext。");
    } finally {
      reviewLoading = false;
      reviewRefresh.disabled = false;
    }
  }

  async function submitFeedback(button) {
    const actions = button.closest(".result-actions");
    const docId = button.dataset.docId;
    const verdict = button.dataset.feedbackVerdict;
    if (!actions || !docId || !verdict) {
      return;
    }

    const buttons = Array.from(actions.querySelectorAll("button"));
    buttons.forEach((item) => {
      item.disabled = true;
    });

    let status = actions.querySelector(".feedback-state");
    if (!status) {
      status = document.createElement("span");
      status.className = "feedback-state";
      status.setAttribute("role", "status");
      actions.append(status);
    }
    status.className = "feedback-state";
    status.textContent = "记录中…";
    status.title = "";

    try {
      const response = await fetch("/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          doc_id: docId,
          verdict,
          note: "",
          query: lastSearch ? lastSearch.query : "",
        }),
      });
      if (!response.ok) {
        throw new Error(await readError(response, `记录失败（HTTP ${response.status}）`));
      }

      const data = await response.json();
      buttons.forEach((item) => item.remove());
      status.className = "feedback-state is-success";
      status.textContent = verdict === "weak" ? "已记为薄弱点" : "已记为掌握";
      status.title = data.artifact_ref || (data.source && data.source.source_id) || "";
      loadReview({ quiet: true });
    } catch (error) {
      status.className = "feedback-state is-error";
      status.textContent = "记录失败，检查 PowerContext";
      status.title = error.message || "";
      buttons.forEach((item) => {
        item.disabled = false;
      });
    }
  }

  async function runSearch() {
    if (searching) {
      return;
    }

    const query = queryInput.value.trim();
    const course = searchCourse.value.trim();
    if (!query) {
      showState("error", "先写下你的问题", "例如「写放大是什么」，或点一个示例问题。");
      resultsMeta.textContent = "等待输入问题";
      queryInput.focus();
      return;
    }

    searching = true;
    setButtonBusy(searchSubmit, true, "检索中…");
    resultsMeta.textContent = "正在查询 seekdb…";
    showState("loading", "正在混合检索…", "向量语义与全文关键词两路召回，再由 RRF 融合排序。");

    const started = performance.now();
    try {
      const params = new URLSearchParams({ q: query });
      if (course) {
        params.set("course", course);
      }

      const response = await fetch(`/search?${params.toString()}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(await readError(response, `检索失败（HTTP ${response.status}）`));
      }

      const payload = await response.json();
      if (!Array.isArray(payload)) {
        throw new Error("检索响应格式不正确。");
      }

      lastSearch = { query, course };
      const term = decodeTerm(response.headers.get("X-Search-Term"), query);
      renderResults(payload, term, performance.now() - started, query);
    } catch (error) {
      resultsMeta.textContent = "请求失败";
      showState(
        "error",
        "检索没有完成",
        error.message || "无法连接服务，请确认 uvicorn 已在 127.0.0.1:8001 启动。"
      );
    } finally {
      searching = false;
      setButtonBusy(searchSubmit, false, "");
    }
  }

  searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    runSearch();
  });

  examples.addEventListener("click", (event) => {
    const chip = event.target.closest("button[data-query]");
    if (!chip) {
      return;
    }
    queryInput.value = chip.dataset.query || "";
    searchForm.requestSubmit();
  });

  results.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-feedback-verdict]");
    if (!button || button.disabled) {
      return;
    }
    submitFeedback(button);
  });

  reviewRefresh.addEventListener("click", () => loadReview());
  loadReview();
})();
