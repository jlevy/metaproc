// Consumer-owned browser views for process, resource, chart, and stats artifacts.
//
// This classic script loads after MetaBrowser's generic shell and before the plugin
// entry point. It intentionally uses the shell's public browser helpers while keeping
// consumer-domain rendering out of the standalone MetaBrowser package.

var mb = window.metabrowser;
if (!mb) {
  throw new Error("consumer domain views require the MetaBrowser plugin SDK");
}
var _perf = mb.perf;
var esc = mb.escapeHtml;
var formatSize = mb.formatSize;

function errorMessage(value) {
  return value instanceof Error ? value.message : String(value);
}

// Share data-hook work across views for the current preview. MetaBrowser mounts
// non-default tabs lazily, but concurrent renders and the Visual/Steps pair can
// still ask for the same payload. Disposing any asynchronous view clears this
// cache so a file reload never reuses stale data.
var pluginDataRequests = new Map();

function requestPluginData(route, parameters) {
  var key = route + ":" + JSON.stringify(parameters);
  var cached = pluginDataRequests.get(key);
  if (cached) {
    return cached;
  }

  var request = mb.fetchPluginData("metaproc", route, parameters).catch((error) => {
    if (pluginDataRequests.get(key) === request) {
      pluginDataRequests.delete(key);
    }
    throw error;
  });
  pluginDataRequests.set(key, request);
  return request;
}

function loadVizModel(path) {
  return requestPluginData("viz-model", { process: path });
}

function clearPluginDataRequests() {
  pluginDataRequests.clear();
  visualLoadedForPath = null;
}

function handleResourceAction(event) {
  var target = event.target;
  if (!target || typeof target.closest !== "function") {
    return;
  }
  var actionTarget = target.closest("[data-metaproc-action]");
  if (!actionTarget) {
    return;
  }
  var action = actionTarget.getAttribute("data-metaproc-action");
  if (event.type === "change" && action === "resource-metric") {
    resourceMetricChanged(actionTarget);
  } else if (event.type === "change" && action === "resource-treemap-metric") {
    resourceTreemapMetricChanged(actionTarget);
  } else if (event.type === "click" && action === "copy-resource") {
    copyResourceTableAs(actionTarget.getAttribute("data-format") || "tsv", actionTarget);
  } else if (event.type === "click" && action === "resource-node") {
    resourceNodeClicked(actionTarget);
  } else if (event.type === "click" && action === "open-path") {
    var path = actionTarget.getAttribute("data-path");
    if (path) {
      mb.openPath(path);
    }
  } else if (event.type === "click" && action === "resource-treemap-tile") {
    resourceTreemapTileClicked(actionTarget.getAttribute("data-node-id") || "");
  }
}

if (window.document?.addEventListener) {
  window.document.addEventListener("click", handleResourceAction);
  window.document.addEventListener("change", handleResourceAction);
}

var currentResourceReportPayload = null;
var currentResourceSelectedNodeId = null;
var DEFAULT_RESOURCE_METRIC = { key: "actual_cost_usd", label: "Actual cost" };
var RESOURCE_METRICS = [
  DEFAULT_RESOURCE_METRIC,
  { key: "list_cost_usd", label: "List cost" },
  { key: "total_tokens", label: "Total tokens" },
  { key: "input_tokens", label: "Input tokens" },
  { key: "output_tokens", label: "Output tokens" },
  { key: "wall_time_s", label: "Wall time" },
  { key: "wait_throttling_s", label: "Throttling wait" },
  { key: "wait_network_s", label: "Network wait" },
  { key: "tool_exec_s", label: "Tool time" },
  { key: "local_compute_s", label: "Local compute" },
  { key: "tool_calls", label: "Tool calls" },
  { key: "tool_failures", label: "Tool failures" },
  { key: "rss_bytes_max", label: "Peak RSS" },
  { key: "billable_vcpu_hours", label: "Billable vCPU hr" },
  { key: "billable_memory_gib_hours", label: "Billable GiB hr" },
];

function setCurrentResourceReportPayload(payload, path) {
  var runDir = runDirFromResourceReportPath(path);
  if (runDir) {
    payload._runDir = runDir;
  }
  // NOTE: we intentionally do NOT seed ``currentVisualResources`` from the
  // resource report any more. Process specs for a run typically live outside
  // the run directory (e.g., ``example_plugin/process/mine/...``) while
  // ``resources.json`` lives under ``runs/<timestamp>/``, so there is no
  // reliable path-based way to tell whether a given Visual tab belongs to the
  // same run as the current resources payload. Silently reusing the payload
  // across runs caused run A's cost badges to appear on run B's visual
  // because node IDs (``predict``, ``postprocess``) are not run-specific.
  //
  // The overlay will come back with an explicit "Apply resources to Visual"
  // gesture in a later iteration. Until then, the decorator machinery
  // (``makeResourceDecorator`` / ``attachVisualResources``) exists so opt-in
  // callers can attach a known-matching payload deliberately.
  return payload;
}

function runDirFromResourceReportPath(path) {
  var suffix = "/resources.json";
  if (!path || path === "resources.json") {
    return "";
  }
  if (path.slice(-suffix.length) === suffix) {
    return path.slice(0, -suffix.length);
  }
  return "";
}

function resourceMetricChanged(sel) {
  var container = sel.closest(".resource-report-view");
  if (!container || !currentResourceReportPayload) {
    return;
  }
  container.outerHTML = renderResourceReportPayload(
    currentResourceReportPayload,
    sel.value,
    currentResourceSelectedNodeId,
  );
}

function resourceNodeClicked(row) {
  currentResourceSelectedNodeId = row.getAttribute("data-node-id");
  var container = row.closest(".resource-report-view");
  var select = container ? container.querySelector(".resource-metric-control select") : null;
  if (!container || !select || !currentResourceReportPayload) {
    return;
  }
  container.outerHTML = renderResourceReportPayload(
    currentResourceReportPayload,
    select.value,
    currentResourceSelectedNodeId,
  );
}

function resourceMetric(metrics, key) {
  metrics = metrics || {};
  if (key === "total_tokens") {
    return (
      (metrics.input_tokens || 0) +
      (metrics.output_tokens || 0) +
      (metrics.cache_read_tokens || 0) +
      (metrics.cache_write_tokens || 0)
    );
  }
  var value = metrics[key];
  return value == null ? 0 : value;
}

function formatResourceMetric(key, value) {
  if (key.indexOf("_cost_usd") >= 0) {
    return "$" + (value || 0).toFixed(2);
  }
  if (key.indexOf("_s") === key.length - 2) {
    return fmtDuration(value || 0);
  }
  if (key.indexOf("rss_bytes") >= 0) {
    return formatSize(value || 0);
  }
  if (key.indexOf("_hours") >= 0) {
    return (value || 0).toFixed(2);
  }
  return (value || 0).toLocaleString();
}

function flattenResourceRows(node, depth, rows) {
  rows.push({ node: node, depth: depth });
  var children = node.children || [];
  for (var i = 0; i < children.length; i++) {
    flattenResourceRows(children[i], depth + 1, rows);
  }
  return rows;
}

function renderResourceReportPayload(report, metricKey, selectedNodeId) {
  var root = report.hierarchy_root || {};
  var totals = root.total_metrics || {};
  var rows = flattenResourceRows(root, 0, []);
  var rowsById = {};
  for (var ri = 0; ri < rows.length; ri++) {
    rowsById[rows[ri].node.node_id] = rows[ri].node;
  }
  rows.sort(
    (a, b) =>
      resourceMetric(b.node.total_metrics, metricKey) -
      resourceMetric(a.node.total_metrics, metricKey),
  );
  if (!selectedNodeId || !rowsById[selectedNodeId]) {
    selectedNodeId = rows.length ? rows[0].node.node_id : null;
  }
  currentResourceSelectedNodeId = selectedNodeId;
  resourceSelectionBus.set(selectedNodeId, "resource-report");
  var selectedNode = selectedNodeId ? rowsById[selectedNodeId] : null;
  var selected = RESOURCE_METRICS.find((m) => m.key === metricKey) || DEFAULT_RESOURCE_METRIC;
  var options = RESOURCE_METRICS.map(
    (m) =>
      '<option value="' +
      esc(m.key) +
      '"' +
      (m.key === metricKey ? " selected" : "") +
      ">" +
      esc(m.label) +
      "</option>",
  ).join("");

  var html = '<div class="resource-report-view" data-metric-key="' + esc(metricKey) + '">';
  html += '<div class="resource-header">';
  html +=
    '<div><div class="resource-title">Resource report: ' + esc(report.run_id || "") + "</div>";
  html += '<div class="resource-subtitle">' + esc(report.generated_at || "") + "</div></div>";
  html +=
    '<label class="resource-metric-control">Metric <select data-metaproc-action="resource-metric">' +
    options +
    "</select></label>";
  html += "</div>";

  html += '<div class="resource-summary-grid">';
  html += resourceSummaryCell(
    "Total tokens",
    formatResourceMetric("total_tokens", resourceMetric(totals, "total_tokens")),
  );
  html += resourceSummaryCell(
    "Wall time",
    formatResourceMetric("wall_time_s", resourceMetric(totals, "wall_time_s")),
  );
  html += resourceSummaryCell(
    "Actual cost",
    formatResourceMetric("actual_cost_usd", resourceMetric(totals, "actual_cost_usd")),
  );
  html += resourceSummaryCell(
    "Tool calls",
    formatResourceMetric("tool_calls", resourceMetric(totals, "tool_calls")),
  );
  html += resourceSummaryCell(
    "Tool failures",
    formatResourceMetric("tool_failures", resourceMetric(totals, "tool_failures")),
  );
  html += resourceSummaryCell("Source logs", (report.source_logs || []).length.toLocaleString());
  html += "</div>";

  html +=
    '<div class="resource-section-title">' +
    "<span>Hierarchy by " +
    esc(selected.label) +
    "</span>" +
    '<button class="resource-copy-btn" data-metaproc-action="copy-resource" data-format="tsv" title="Copy the current hierarchy rows as TSV">Copy TSV</button>' +
    '<button class="resource-copy-btn" data-metaproc-action="copy-resource" data-format="csv" title="Copy the current hierarchy rows as CSV">Copy CSV</button>' +
    "</div>";
  html +=
    '<table class="resource-table resource-hierarchy-table"><thead><tr>' +
    "<th>Node</th><th>Type</th><th>" +
    esc(selected.label) +
    "</th><th>Tokens</th><th>Wall</th><th>Actual cost</th><th>Tools</th><th>Logs</th>" +
    "</tr></thead><tbody>";
  for (var r = 0; r < rows.length; r++) {
    var node = rows[r].node;
    var metrics = node.total_metrics || {};
    var indent = rows[r].depth * 14;
    var rowClass = node.node_id === selectedNodeId ? ' class="resource-row-selected"' : "";
    html +=
      '<tr data-node-id="' +
      esc(node.node_id || "") +
      '"' +
      rowClass +
      ' data-metaproc-action="resource-node">';
    html +=
      '<td><span class="resource-node-label" style="padding-left:' +
      indent +
      'px">' +
      esc(node.label || node.node_id || "") +
      "</span></td>";
    html += "<td>" + esc(node.node_type || "") + "</td>";
    html += "<td>" + formatResourceMetric(metricKey, resourceMetric(metrics, metricKey)) + "</td>";
    html +=
      "<td>" +
      formatResourceMetric("total_tokens", resourceMetric(metrics, "total_tokens")) +
      "</td>";
    html +=
      "<td>" +
      formatResourceMetric("wall_time_s", resourceMetric(metrics, "wall_time_s")) +
      "</td>";
    html +=
      "<td>" +
      formatResourceMetric("actual_cost_usd", resourceMetric(metrics, "actual_cost_usd")) +
      "</td>";
    html +=
      "<td>" + formatResourceMetric("tool_calls", resourceMetric(metrics, "tool_calls")) + "</td>";
    html += "<td>" + (node.log_summary?.source_log_count || 0).toLocaleString() + "</td>";
    html += "</tr>";
  }
  html += "</tbody></table>";
  html += renderResourceNodeDetail(selectedNode);
  html += renderTaxonomyRollups(report.taxonomy_rollups || {}, metricKey);
  html += renderResourceSourceLogs(report.source_logs || []);
  html += "</div>";
  return html;
}

function resourceSummaryCell(label, value) {
  return (
    '<div class="resource-summary-cell"><span>' +
    esc(label) +
    "</span><strong>" +
    esc(String(value)) +
    "</strong></div>"
  );
}

function renderResourceSourceLogs(logs) {
  // Group source logs by owner node so operators see evidence
  // clustered against the hotspots they already found in the hierarchy.
  var html = '<div class="resource-section-title">Log evidence</div>';
  if (!logs.length) {
    return html + '<div class="preview-empty">No source logs</div>';
  }

  // Group by owner_node_id (null owners → the unattributed bucket).
  var groups = {};
  var groupOrder = [];
  for (var i = 0; i < logs.length; i++) {
    var owner = logs[i].owner_node_id || "<unattributed>";
    if (!groups[owner]) {
      groups[owner] = [];
      groupOrder.push(owner);
    }
    groups[owner].push(logs[i]);
  }
  groupOrder.sort((a, b) => {
    if (a === "<unattributed>") {
      return 1;
    }
    if (b === "<unattributed>") {
      return -1;
    }
    return a.localeCompare(b);
  });

  for (var g = 0; g < groupOrder.length; g++) {
    var ownerId = groupOrder[g];
    var groupLogs = groups[ownerId];
    var totalEvents = 0,
      totalErrors = 0,
      totalCalls = 0;
    for (var k = 0; k < groupLogs.length; k++) {
      var s = groupLogs[k].summary || {};
      totalEvents += s.event_count || 0;
      totalErrors += s.error_count || 0;
      totalCalls += s.tool_call_count || 0;
    }
    html += '<div class="resource-log-group">';
    html +=
      '<div class="resource-log-group-header">' +
      "<strong>" +
      esc(ownerId) +
      "</strong>" +
      " · " +
      groupLogs.length +
      " log" +
      (groupLogs.length === 1 ? "" : "s") +
      " · " +
      totalEvents.toLocaleString() +
      " events" +
      " · " +
      totalCalls.toLocaleString() +
      " tool calls" +
      (totalErrors
        ? ' · <span class="resource-log-errors">' + totalErrors.toLocaleString() + " errors</span>"
        : "") +
      "</div>";
    html +=
      '<table class="resource-table"><thead><tr>' +
      "<th>Path</th><th>Adapter</th><th>Kind</th><th>Events</th><th>Errors</th><th>Tool calls</th><th>First</th><th>Last</th>" +
      "</tr></thead><tbody>";
    for (var j = 0; j < groupLogs.length; j++) {
      var log = groupLogs[j];
      var summary = log.summary || {};
      var path = log.path || "";
      html += "<tr>";
      html +=
        '<td><button class="resource-link" data-metaproc-action="open-path" data-path="' +
        esc(path) +
        '">' +
        esc(path) +
        "</button></td>";
      html += "<td>" + esc(log.adapter || "") + "</td>";
      html += "<td>" + esc(log.kind || "") + "</td>";
      html += "<td>" + (summary.event_count || 0).toLocaleString() + "</td>";
      html += "<td>" + (summary.error_count || 0).toLocaleString() + "</td>";
      html += "<td>" + (summary.tool_call_count || 0).toLocaleString() + "</td>";
      html += "<td>" + esc(formatShortTs(summary.first_ts)) + "</td>";
      html += "<td>" + esc(formatShortTs(summary.last_ts)) + "</td>";
      html += "</tr>";
    }
    html += "</tbody></table>";
    html += "</div>";
  }
  return html;
}

function formatShortTs(iso) {
  if (!iso) {
    return "—";
  }
  // Drop sub-second + timezone for compact display.
  return String(iso)
    .replace(/\.[0-9]+/, "")
    .replace(/(Z|[+-][0-9:]+)$/, "");
}

// ── Taxonomy rollup panel ──────────────────────────────────────

var TAXONOMY_FAMILIES = [
  { key: "time_kind_path", label: "Time breakdown" },
  { key: "provider_path", label: "Top providers" },
  { key: "model_path", label: "Top models" },
  { key: "tool_path", label: "Top tools" },
];

function renderTaxonomyRollups(rollups, metricKey) {
  var hasAny = false;
  for (var i = 0; i < TAXONOMY_FAMILIES.length; i++) {
    var candidateFamily = TAXONOMY_FAMILIES[i];
    if (candidateFamily && (rollups[candidateFamily.key] || []).length) {
      hasAny = true;
      break;
    }
  }
  if (!hasAny) {
    return "";
  }

  var html = '<div class="resource-section-title">Taxonomy rollups</div>';
  html += '<div class="resource-taxonomy-grid">';
  for (var f = 0; f < TAXONOMY_FAMILIES.length; f++) {
    var family = TAXONOMY_FAMILIES[f];
    if (!family) {
      continue;
    }
    var entries = rollups[family.key] || [];
    if (!entries.length) {
      continue;
    }
    // Rank by the user's selected metric when it's meaningful for this family;
    // otherwise fall back to the built-in family-default metric.
    var rankKey = taxonomyRankKeyFor(family.key, metricKey);
    var ranked = entries
      .slice()
      .sort((a, b) => resourceMetric(b.metrics, rankKey) - resourceMetric(a.metrics, rankKey));
    html += '<div class="resource-taxonomy-panel">';
    html += '<div class="resource-taxonomy-title">' + esc(family.label) + "</div>";
    html +=
      '<table class="resource-table"><thead><tr><th>Prefix</th><th>' +
      esc(rankKey) +
      "</th></tr></thead><tbody>";
    var limit = Math.min(ranked.length, 6);
    for (var n = 0; n < limit; n++) {
      var entry = ranked[n];
      html +=
        '<tr><td title="' +
        esc(entry.canonical || "") +
        '">' +
        esc(entry.canonical || "") +
        "</td>" +
        "<td>" +
        formatResourceMetric(rankKey, resourceMetric(entry.metrics, rankKey)) +
        "</td></tr>";
    }
    html += "</tbody></table></div>";
  }
  html += "</div>";
  return html;
}

function taxonomyRankKeyFor(familyKey, metricKey) {
  if (familyKey === "time_kind_path") {
    // For time taxonomy, prefer a time-like metric; respect metricKey when it is one.
    if (metricKey && (metricKey.endsWith("_s") || metricKey === "wall_time_s")) {
      return metricKey;
    }
    return "wait_throttling_s";
  }
  if (familyKey === "tool_path") {
    if (
      metricKey === "tool_exec_s" ||
      metricKey === "tool_calls" ||
      metricKey === "tool_failures"
    ) {
      return metricKey;
    }
    return "tool_exec_s";
  }
  // provider_path and model_path default to cost.
  if (metricKey === "actual_cost_usd" || metricKey === "list_cost_usd") {
    return metricKey;
  }
  return "actual_cost_usd";
}

// ── Shared selection bus ───────────────────────────────────────

var resourceSelectionBus = (() => {
  var currentNodeId = null;
  var subscribers = [];
  return {
    get: () => currentNodeId,
    set: (nodeId, sourceTag) => {
      if (currentNodeId === nodeId) {
        return;
      }
      currentNodeId = nodeId;
      for (var i = 0; i < subscribers.length; i++) {
        try {
          subscribers[i](nodeId, sourceTag || "unknown");
        } catch (_err) {
          /* subscribers must not throw each other off */
        }
      }
    },
    subscribe: (fn) => {
      subscribers.push(fn);
      return function unsubscribe() {
        var idx = subscribers.indexOf(fn);
        if (idx >= 0) {
          subscribers.splice(idx, 1);
        }
      };
    },
  };
})();

if (typeof window !== "undefined") {
  window.MetaprocResourceSelection = resourceSelectionBus;
}

// ── CSV / TSV export ───────────────────────────────────────────

function copyResourceTableAs(format, btn) {
  var container = btn.closest(".resource-report-view");
  if (!container) {
    return;
  }
  var table = container.querySelector(".resource-hierarchy-table");
  if (!table) {
    return;
  }
  var sep = format === "csv" ? "," : "\t";
  var lines = [];
  var headers = table.querySelectorAll("thead th");
  var headerCells = [];
  for (var h = 0; h < headers.length; h++) {
    headerCells.push(csvEscape(headers[h].textContent || "", sep));
  }
  lines.push(headerCells.join(sep));
  var rows = table.querySelectorAll("tbody tr");
  for (var r = 0; r < rows.length; r++) {
    var cells = rows[r].querySelectorAll("td");
    var out = [];
    for (var c = 0; c < cells.length; c++) {
      // Strip indent from the first column so exported data doesn't carry CSS.
      var text = (cells[c].textContent || "").replace(/^\s+/, "").replace(/\s+$/, "");
      out.push(csvEscape(text, sep));
    }
    lines.push(out.join(sep));
  }
  var payload = lines.join("\n");
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(payload).then(() => {
      btn.textContent = "Copied!";
      setTimeout(() => {
        btn.textContent = format === "csv" ? "Copy CSV" : "Copy TSV";
      }, 1200);
    });
  } else {
    // Fallback for non-secure contexts: open a tiny window so the user can copy manually.
    var w = window.open("", "_blank", "width=600,height=400");
    if (w) {
      w.document.body.innerText = payload;
    }
  }
}

function csvEscape(text, sep) {
  if (text == null) {
    return "";
  }
  var s = String(text);
  if (s.indexOf(sep) < 0 && s.indexOf("\n") < 0 && s.indexOf('"') < 0) {
    return s;
  }
  return '"' + s.replace(/"/g, '""') + '"';
}

// ── Treemap view ───────────────────────────────────────────────

var currentResourceTreemapRootId = null;

function renderResourceTreemapPayload(report, metricKey, rootId) {
  var root = report.hierarchy_root || {};
  var byId = {};
  collectNodesById(root, byId);
  var focusNode = rootId && byId[rootId] ? byId[rootId] : root;
  var crumbs = buildTreemapBreadcrumbs(focusNode, byId);
  var selected = RESOURCE_METRICS.find((m) => m.key === metricKey) || DEFAULT_RESOURCE_METRIC;
  var options = RESOURCE_METRICS.map(
    (m) =>
      '<option value="' +
      esc(m.key) +
      '"' +
      (m.key === metricKey ? " selected" : "") +
      ">" +
      esc(m.label) +
      "</option>",
  ).join("");

  var html =
    '<div class="resource-treemap-view" data-metric-key="' +
    esc(metricKey) +
    '" data-root-id="' +
    esc(focusNode.node_id || "") +
    '">';
  html += '<div class="resource-header">';
  html += '<div><div class="resource-title">Treemap: ' + esc(report.run_id || "") + "</div>";
  html += '<div class="resource-treemap-crumbs">' + crumbs + "</div></div>";
  html +=
    '<label class="resource-metric-control">Metric <select data-metaproc-action="resource-treemap-metric">' +
    options +
    "</select></label>";
  html += "</div>";

  var W = 900,
    H = 480;
  var rects = layoutSquarified(focusNode, metricKey, 0, 0, W, H);
  html += '<div class="resource-treemap-canvas-wrap">';
  html +=
    '<svg class="resource-treemap-canvas" viewBox="0 0 ' +
    W +
    " " +
    H +
    '" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">';
  for (var i = 0; i < rects.length; i++) {
    var rct = rects[i];
    var accent = metricHeatClass(rct.value, rects[0] ? rects[0].value : 1);
    html +=
      '<g class="resource-treemap-tile ' +
      accent +
      '" data-metaproc-action="resource-treemap-tile" data-node-id="' +
      esc(rct.node.node_id || "") +
      '">';
    html +=
      '<rect x="' + rct.x + '" y="' + rct.y + '" width="' + rct.w + '" height="' + rct.h + '" />';
    if (rct.w > 60 && rct.h > 20) {
      var label = esc(rct.node.label || rct.node.node_id || "");
      var value = formatResourceMetric(metricKey, rct.value);
      html +=
        '<text x="' +
        (rct.x + 6) +
        '" y="' +
        (rct.y + 14) +
        '" class="resource-treemap-label">' +
        label +
        "</text>";
      if (rct.h > 34) {
        html +=
          '<text x="' +
          (rct.x + 6) +
          '" y="' +
          (rct.y + 28) +
          '" class="resource-treemap-value">' +
          esc(value) +
          "</text>";
      }
    }
    html += "</g>";
  }
  html += "</svg>";
  html += "</div>";
  if (!rects.length) {
    html +=
      '<div class="preview-empty">No children with a positive ' +
      esc(selected.label) +
      " value at this level.</div>";
  }
  html += "</div>";
  return html;
}

function collectNodesById(node, out) {
  if (!node) {
    return;
  }
  if (node.node_id) {
    out[node.node_id] = node;
  }
  var children = node.children || [];
  for (var i = 0; i < children.length; i++) {
    collectNodesById(children[i], out);
  }
}

function buildTreemapBreadcrumbs(focusNode, byId) {
  var chain = [];
  var cur = focusNode;
  while (cur) {
    chain.unshift(cur);
    cur = cur.parent_id ? byId[cur.parent_id] : null;
  }
  var parts = [];
  for (var i = 0; i < chain.length; i++) {
    var n = chain[i];
    if (i === chain.length - 1) {
      parts.push(
        '<span class="resource-crumb-current">' + esc(n.label || n.node_id || "") + "</span>",
      );
    } else {
      parts.push(
        '<button class="resource-link" data-metaproc-action="resource-treemap-tile" data-node-id="' +
          esc(n.node_id || "") +
          '">' +
          esc(n.label || n.node_id || "") +
          "</button>",
      );
    }
  }
  return parts.join(' <span class="resource-crumb-sep">›</span> ');
}

function layoutSquarified(parent, metricKey, x, y, w, h) {
  var children = (parent.children || [])
    .map((c) => ({ node: c, value: Math.max(0, resourceMetric(c.total_metrics || {}, metricKey)) }))
    .filter((c) => c.value > 0);
  children.sort((a, b) => b.value - a.value);
  if (!children.length) {
    return [];
  }
  var total = 0;
  for (var i = 0; i < children.length; i++) {
    total += children[i].value;
  }
  return squarify(children, [], { x: x, y: y, w: w, h: h, total: total });
}

function squarify(remaining, row, box) {
  var out = [];
  var cursor = 0;
  while (cursor < remaining.length) {
    row = [remaining[cursor]];
    var rowSum = remaining[cursor].value;
    var next = cursor + 1;
    while (next < remaining.length) {
      var candidateSum = rowSum + remaining[next].value;
      var currentWorst = worstRatio(row, rowSum, box);
      var candidateRow = row.concat([remaining[next]]);
      var candidateWorst = worstRatio(candidateRow, candidateSum, box);
      if (candidateWorst <= currentWorst) {
        row = candidateRow;
        rowSum = candidateSum;
        next++;
      } else {
        break;
      }
    }
    out = out.concat(placeRow(row, rowSum, box));
    // Shrink box by the laid row.
    var shortSide = Math.min(box.w, box.h);
    var rowThickness = ((rowSum / box.total) * (box.w * box.h)) / (shortSide || 1);
    if (box.w < box.h) {
      box = {
        x: box.x,
        y: box.y + rowThickness,
        w: box.w,
        h: Math.max(0, box.h - rowThickness),
        total: box.total - rowSum,
      };
    } else {
      box = {
        x: box.x + rowThickness,
        y: box.y,
        w: Math.max(0, box.w - rowThickness),
        h: box.h,
        total: box.total - rowSum,
      };
    }
    cursor = next;
    if (box.total <= 0 || box.w <= 0 || box.h <= 0) {
      break;
    }
  }
  return out;
}

function worstRatio(row, rowSum, box) {
  if (!row.length || rowSum <= 0) {
    return Infinity;
  }
  var shortSide = Math.min(box.w, box.h);
  var area = box.w * box.h;
  var scale = (rowSum / box.total) * area;
  var rowLength = scale / (shortSide || 1);
  var worst = 0;
  for (var i = 0; i < row.length; i++) {
    var cellArea = (row[i].value / box.total) * area;
    var cellShort = cellArea / (rowLength || 1);
    var ratio = Math.max(rowLength / (cellShort || 1), (cellShort || 1) / rowLength);
    if (ratio > worst) {
      worst = ratio;
    }
  }
  return worst;
}

function placeRow(row, rowSum, box) {
  var out = [];
  var area = box.w * box.h;
  var rowArea = (rowSum / box.total) * area;
  var shortSide = Math.min(box.w, box.h);
  var rowThickness = rowArea / (shortSide || 1);
  var horizontal = box.w >= box.h;
  var offset = 0;
  for (var i = 0; i < row.length; i++) {
    var cellArea = (row[i].value / box.total) * area;
    var cellLength = cellArea / (rowThickness || 1);
    if (horizontal) {
      out.push({
        node: row[i].node,
        value: row[i].value,
        x: box.x + offset,
        y: box.y,
        w: cellLength,
        h: rowThickness,
      });
    } else {
      out.push({
        node: row[i].node,
        value: row[i].value,
        x: box.x,
        y: box.y + offset,
        w: rowThickness,
        h: cellLength,
      });
    }
    offset += cellLength;
  }
  return out;
}

function metricHeatClass(value, peakValue) {
  if (!peakValue || !value) {
    return "resource-heat-cold";
  }
  var ratio = value / peakValue;
  if (ratio > 0.66) {
    return "resource-heat-hot";
  }
  if (ratio > 0.33) {
    return "resource-heat-warm";
  }
  return "resource-heat-cool";
}

function resourceTreemapMetricChanged(sel) {
  var view = sel.closest(".resource-treemap-view");
  if (!view || !currentResourceReportPayload) {
    return;
  }
  view.outerHTML = renderResourceTreemapPayload(
    currentResourceReportPayload,
    sel.value,
    currentResourceTreemapRootId,
  );
}

function resourceTreemapTileClicked(nodeId) {
  currentResourceTreemapRootId = nodeId || null;
  resourceSelectionBus.set(nodeId, "treemap");
  var view = document.querySelector(".resource-treemap-view");
  if (!view || !currentResourceReportPayload) {
    return;
  }
  var metricKey = view.getAttribute("data-metric-key") || "actual_cost_usd";
  view.outerHTML = renderResourceTreemapPayload(
    currentResourceReportPayload,
    metricKey,
    currentResourceTreemapRootId,
  );
}

// ── Visual-tab resource overlay ────────────────────────────────

// currentVisualResources is set ONLY via attachVisualResources() — never
// implicitly from opening a resources.json tab. Process specs do not live
// under the run directory, so there is no path-based way to tell whether a
// given Visual belongs to the same run as some resources payload; silently
// reusing a prior payload cross-contaminated run B's visual with run A's
// cost badges. The decorator now ships off-by-default until an operator
// gesture (future "Apply resources" UI) explicitly attaches a payload AND
// a matching visual path.
var currentVisualMetricKey = "actual_cost_usd";
var currentVisualResources = null; // payload from /api/resources (attached)
var currentVisualResourcesPath = null; // process-spec path this payload is pinned to

function makeResourceDecorator(resourcesDoc, metricKey) {
  var nodeMap = {};
  collectNodesById(resourcesDoc.hierarchy_root || {}, nodeMap);
  // Find peak value so heat classes have a reference.
  var peak = 0;
  for (var id in nodeMap) {
    if (!Object.hasOwn(nodeMap, id)) {
      continue;
    }
    var v = resourceMetric(nodeMap[id].total_metrics || {}, metricKey);
    if (v > peak) {
      peak = v;
    }
  }
  return {
    predicate: (node) => node && nodeMap[node.id] != null,
    decorate: (node) => {
      var rec = nodeMap[node.id];
      if (!rec) {
        return null;
      }
      var value = resourceMetric(rec.total_metrics || {}, metricKey);
      var selfValue = resourceMetric(rec.self_metrics || {}, metricKey);
      var cls = metricHeatClass(value, peak);
      var fmt = formatResourceMetric(metricKey, value);
      var lines = [metricKey + " (total): " + fmt];
      if (selfValue && selfValue !== value) {
        lines.push(metricKey + " (self): " + formatResourceMetric(metricKey, selfValue));
      }
      if (rec.log_summary?.source_log_count) {
        lines.push(rec.log_summary.source_log_count + " source log(s)");
      }
      return {
        badge: fmt,
        accent_token: cls,
        tooltip_addendum: lines.join("\n"),
      };
    },
  };
}

function attachVisualResources(payload, processPath) {
  // Explicit opt-in: pin a resources payload to the Visual tab for exactly
  // one process-spec path. ``renderVizPayload`` applies the decorator only
  // when the visual being rendered matches ``processPath``. Passing a
  // ``null`` payload (or calling ``detachVisualResources``) clears the
  // overlay so the next navigation gets a clean slate.
  if (!payload?.hierarchy_root || !processPath) {
    currentVisualResources = null;
    currentVisualResourcesPath = null;
    return;
  }
  currentVisualResources = payload;
  currentVisualResourcesPath = processPath;
}

function detachVisualResources() {
  currentVisualResources = null;
  currentVisualResourcesPath = null;
}

async function loadResourcesForRunDir(runDirRel) {
  // Fetcher for the future "Apply resources" gesture: looks up a run's
  // resources payload without committing it as the active visual overlay.
  // Callers must still invoke ``attachVisualResources(payload, processPath)``
  // to opt into decoration.
  try {
    var payload = await requestPluginData("resources", { run_dir: runDirRel });
    payload._runDir = runDirRel;
    return payload;
  } catch (_err) {
    return null;
  }
}

function renderResourceNodeDetail(node) {
  if (!node) {
    return "";
  }
  var selfMetrics = node.self_metrics || {};
  var totalMetrics = node.total_metrics || {};
  var refs = node.source_refs || [];
  var html = '<div class="resource-section-title">Selected node</div>';
  html += '<div class="resource-node-detail">';
  html += '<div class="resource-detail-main">';
  html += '<div class="resource-detail-title">' + esc(node.label || node.node_id || "") + "</div>";
  html +=
    '<div class="resource-subtitle">' +
    esc(node.node_type || "") +
    " · " +
    esc(node.node_id || "") +
    "</div>";
  html += "</div>";
  html += '<div class="resource-detail-grid">';
  html += resourceSummaryCell(
    "Self tokens",
    formatResourceMetric("total_tokens", resourceMetric(selfMetrics, "total_tokens")),
  );
  html += resourceSummaryCell(
    "Total tokens",
    formatResourceMetric("total_tokens", resourceMetric(totalMetrics, "total_tokens")),
  );
  html += resourceSummaryCell(
    "Self cost",
    formatResourceMetric("actual_cost_usd", resourceMetric(selfMetrics, "actual_cost_usd")),
  );
  html += resourceSummaryCell(
    "Total cost",
    formatResourceMetric("actual_cost_usd", resourceMetric(totalMetrics, "actual_cost_usd")),
  );
  html += resourceSummaryCell(
    "Tool calls",
    formatResourceMetric("tool_calls", resourceMetric(totalMetrics, "tool_calls")),
  );
  html += resourceSummaryCell("Source refs", refs.length.toLocaleString());
  html += "</div>";
  if (refs.length) {
    html +=
      '<table class="resource-table"><thead><tr><th>Source</th><th>Kind</th><th>Spans</th><th>Offsets</th></tr></thead><tbody>';
    for (var i = 0; i < refs.length; i++) {
      var ref = refs[i];
      html += "<tr>";
      html += "<td>" + esc(ref.path || "") + "</td>";
      html += "<td>" + esc(ref.kind || "") + "</td>";
      html += "<td>" + esc((ref.span_ids || []).join(", ") || "-") + "</td>";
      html += "<td>" + esc((ref.line_offsets || []).join(", ") || "-") + "</td>";
      html += "</tr>";
    }
    html += "</tbody></table>";
  }
  html += "</div>";
  return html;
}

var visualLoadedForPath = null;

async function loadVisual(container, path, isCurrent) {
  container.innerHTML =
    '<div class="loading"><div class="spinner"></div>Loading visualization…</div>';
  try {
    var payload = await loadVizModel(path);
    if (!isCurrent()) {
      return;
    }
    visualLoadedForPath = path;
    await renderVizPayload(container, payload);
  } catch (err) {
    if (!isCurrent()) {
      return;
    }
    container.innerHTML = renderVizError(null, errorMessage(err));
  }
}

async function renderVizPayload(container, payload) {
  var viz = payload.viz || payload;
  return _perf.measureAsync(
    "renderVizPayload",
    async () => {
      if (!global_MetaprocViz()) {
        container.innerHTML = '<div class="viz-error">viz renderer not loaded</div>';
        return;
      }
      var decorators = [];
      // Apply the resource decorator only when an operator has explicitly pinned
      // a resources payload to *this* process-spec path. The guard prevents the
      // classic cross-run contamination bug where run A's cost badges leak onto
      // run B's visual because node IDs (``predict``, ``postprocess``) are not
      // run-specific.
      if (
        currentVisualResources?.hierarchy_root &&
        currentVisualResourcesPath &&
        currentVisualResourcesPath === visualLoadedForPath
      ) {
        decorators.push(makeResourceDecorator(currentVisualResources, currentVisualMetricKey));
      }
      await window.MetaprocViz.renderViz(container, viz, {
        onOpenFile: (p) => {
          mb.openPath(p);
        },
        decorators: decorators,
        warnings: payload.validation_warnings || [],
      });
    },
    {
      nodes: viz && Array.isArray(viz.nodes) ? viz.nodes.length : 0,
      warnings: payload?.validation_warnings ? payload.validation_warnings.length : 0,
    },
  );
}

function renderVizError(parsed, fallback) {
  var title = "Could not build visualization";
  var detail = fallback;
  if (parsed?.detail) {
    detail = parsed.detail;
    if (parsed.error) {
      title = parsed.error;
    }
  }
  return (
    '<div class="viz-error-panel">' +
    '<div class="viz-error-title">' +
    esc(title) +
    "</div>" +
    '<pre class="viz-error-detail">' +
    esc(detail) +
    "</pre>" +
    '<div class="viz-error-hint">Open the <strong>Source</strong> or <strong>Document</strong> tab to view the process spec directly.</div>' +
    "</div>"
  );
}

function global_MetaprocViz() {
  return typeof window.MetaprocViz !== "undefined" && window.MetaprocViz.renderViz;
}

async function loadCharts(container, path, isCurrent) {
  try {
    var chartData = await requestPluginData("charts", { path: path });
    if (!isCurrent()) {
      return;
    }
    renderChartsPayload(container, chartData);
  } catch (e) {
    if (!isCurrent()) {
      return;
    }
    console.warn("loadCharts: " + errorMessage(e) + " for " + path);
    container.innerHTML = '<div class="preview-empty">Error loading charts</div>';
  }
}

function disposeCharts() {
  if (window.MetabrowserCharts?.dispose) {
    window.MetabrowserCharts.dispose();
  }
}

function renderChartsPayload(container, chartData) {
  if (!window.MetabrowserCharts) {
    container.innerHTML = '<div class="preview-empty">Chart renderer unavailable</div>';
    return;
  }
  window.MetabrowserCharts.renderPayload(container, chartData);
}

// ── Stats tab ──────────────────────────────────────────────────

async function loadStats(container, filePath, isCurrent) {
  var runDir = filePath.replace(/\/?\.logs\/[^/]+$/, "") || ".";

  try {
    var stats = await requestPluginData("stats", { path: runDir });
    if (!isCurrent()) {
      return;
    }
    container.innerHTML = "";
    renderStatsCards(container, stats);
  } catch (e) {
    if (!isCurrent()) {
      return;
    }
    console.warn("loadStats: " + errorMessage(e) + " for " + runDir);
    container.innerHTML = '<div class="preview-empty">Error loading stats</div>';
  }
}

function renderStatsCards(container, stats) {
  return _perf.measure(
    "renderStatsCards",
    () => {
      var html = '<div class="stats-grid">';

      // Progress section
      if (stats.progress) {
        var p = stats.progress;
        var statusLabel = p.running > 0 ? "RUNNING" : "COMPLETE";
        var statusClass = p.running > 0 ? "stat-running" : "stat-complete";
        html += '<div class="stats-card stats-card-wide">';
        html +=
          '<div class="stats-card-header">Progress <span class="stats-badge ' +
          statusClass +
          '">' +
          statusLabel +
          "</span></div>";
        if (p.wall_time_s > 0) {
          html += '<div class="stats-meta">Wall time: ' + fmtDuration(p.wall_time_s) + "</div>";
        }
        // Progress bar
        var pct = p.total_items > 0 ? (p.completed * 100) / p.total_items : 0;
        var failPct = p.total_items > 0 ? (p.failed * 100) / p.total_items : 0;
        html += '<div class="stats-progress-bar">';
        html += '<div class="stats-progress-fill" style="width:' + pct.toFixed(1) + '%"></div>';
        if (failPct > 0) {
          html +=
            '<div class="stats-progress-fail" style="width:' +
            failPct.toFixed(1) +
            "%;left:" +
            pct.toFixed(1) +
            '%"></div>';
        }
        html += "</div>";
        html += '<div class="stats-meta">' + p.completed + "/" + p.total_items + " completed";
        if (p.failed > 0) {
          html += ", " + p.failed + " failed";
        }
        if (p.running > 0) {
          html += ", " + p.running + " running";
        }
        if (p.pending > 0) {
          html += ", " + p.pending + " pending";
        }
        html += "</div>";
        // Variant table
        var variants = Object.keys(p.variants);
        if (variants.length > 0) {
          html +=
            '<table class="stats-table"><thead><tr><th>Variant</th><th>Done</th><th>Run</th><th>Fail</th><th>Pend</th><th>Total</th><th>%</th></tr></thead><tbody>';
          for (var i = 0; i < variants.length; i++) {
            var vname = variants[i];
            if (vname === undefined) {
              continue;
            }
            var vp = p.variants[vname];
            if (!vp) {
              continue;
            }
            html +=
              "<tr><td>" +
              esc(vname) +
              "</td><td>" +
              vp.completed +
              "</td><td>" +
              vp.running +
              "</td><td>" +
              vp.failed +
              "</td><td>" +
              vp.pending +
              "</td><td>" +
              vp.total +
              "</td><td>" +
              vp.pct.toFixed(0) +
              "%</td></tr>";
          }
          html += "</tbody></table>";
        }
        html += "</div>";
      }

      // Throughput section
      if (stats.throughput) {
        var t = stats.throughput;
        html += '<div class="stats-card">';
        html += '<div class="stats-card-header">Throughput</div>';
        html +=
          '<div class="stats-kv"><span class="stats-label">Items/hour</span><span class="stats-value">' +
          t.items_per_hour +
          "</span></div>";
        html +=
          '<div class="stats-kv"><span class="stats-label">Recent rate</span><span class="stats-value">' +
          t.recent_items_per_hour +
          "/hr</span></div>";
        html +=
          '<div class="stats-kv"><span class="stats-label">Completed</span><span class="stats-value">' +
          t.unique_completed +
          "</span></div>";
        html +=
          '<div class="stats-kv"><span class="stats-label">Process runs</span><span class="stats-value">' +
          t.total_process_runs +
          "</span></div>";
        html +=
          '<div class="stats-kv"><span class="stats-label">Retry factor</span><span class="stats-value">' +
          t.retry_factor +
          "x (" +
          t.retry_pct +
          "%)</span></div>";
        html += "</div>";
      }

      // Timing section
      if (stats.timing) {
        var tm = stats.timing;
        html += '<div class="stats-card">';
        html += '<div class="stats-card-header">Timing</div>';
        if (tm.p50_seconds != null) {
          html +=
            '<div class="stats-kv"><span class="stats-label">p50</span><span class="stats-value">' +
            fmtDuration(tm.p50_seconds) +
            "</span></div>";
        }
        if (tm.p95_seconds != null) {
          html +=
            '<div class="stats-kv"><span class="stats-label">p95</span><span class="stats-value">' +
            fmtDuration(tm.p95_seconds) +
            "</span></div>";
        }
        if (tm.p99_seconds != null) {
          html +=
            '<div class="stats-kv"><span class="stats-label">p99</span><span class="stats-value">' +
            fmtDuration(tm.p99_seconds) +
            "</span></div>";
        }
        html +=
          '<div class="stats-kv"><span class="stats-label">avg</span><span class="stats-value">' +
          fmtDuration(tm.avg_seconds) +
          "</span></div>";
        html +=
          '<div class="stats-kv"><span class="stats-label">min</span><span class="stats-value">' +
          fmtDuration(tm.min_seconds) +
          "</span></div>";
        html +=
          '<div class="stats-kv"><span class="stats-label">max</span><span class="stats-value">' +
          fmtDuration(tm.max_seconds) +
          "</span></div>";
        html += "</div>";
      }

      // Pool section
      if (stats.pool) {
        var pl = stats.pool;
        var workerNames =
          pl.worker_ids && pl.worker_ids.length > 0 ? pl.worker_ids.join(", ") : "local";
        var liveCapParts = [];
        if (pl.live_worker_caps) {
          var liveCapWorkers = Object.keys(pl.live_worker_caps).sort();
          for (var j = 0; j < liveCapWorkers.length; j++) {
            var workerId = liveCapWorkers[j];
            if (workerId === undefined) {
              continue;
            }
            liveCapParts.push(workerId + "=" + pl.live_worker_caps[workerId]);
          }
        }
        var processKillTotal = 0;
        if (pl.process_kills_by_reason) {
          for (var killReason in pl.process_kills_by_reason) {
            processKillTotal += pl.process_kills_by_reason[killReason];
          }
        }
        var processFailureParts = [];
        if (pl.process_failure_counts) {
          var pfc = pl.process_failure_counts;
          if (pfc.rate_limited) {
            processFailureParts.push("rate_limited: " + pfc.rate_limited);
          }
          if (pfc.server_error) {
            processFailureParts.push("server_error: " + pfc.server_error);
          }
          if (pfc.invalid_output) {
            processFailureParts.push("invalid_output: " + pfc.invalid_output);
          }
          if (pfc.timeout) {
            processFailureParts.push("timeout: " + pfc.timeout);
          }
          if (pfc.crash) {
            processFailureParts.push("crash: " + pfc.crash);
          }
          if (pfc.unknown) {
            processFailureParts.push("unknown: " + pfc.unknown);
          }
        }
        var runnerFailureParts = [];
        if (pl.runner_failure_counts) {
          var rfc = pl.runner_failure_counts;
          if (rfc.rate_limited) {
            runnerFailureParts.push("rate_limited: " + rfc.rate_limited);
          }
          if (rfc.server_error) {
            runnerFailureParts.push("server_error: " + rfc.server_error);
          }
          if (rfc.invalid_output) {
            runnerFailureParts.push("invalid_output: " + rfc.invalid_output);
          }
          if (rfc.timeout) {
            runnerFailureParts.push("timeout: " + rfc.timeout);
          }
          if (rfc.crash) {
            runnerFailureParts.push("crash: " + rfc.crash);
          }
          if (rfc.unknown) {
            runnerFailureParts.push("unknown: " + rfc.unknown);
          }
        }
        html += '<div class="stats-card">';
        html += '<div class="stats-card-header">Pool</div>';
        html +=
          '<div class="stats-kv"><span class="stats-label">Workers</span><span class="stats-value">' +
          pl.num_workers +
          " (" +
          esc(workerNames) +
          ")</span></div>";
        if (pl.desired_workers != null) {
          html +=
            '<div class="stats-kv"><span class="stats-label">Topology</span><span class="stats-value">active=' +
            pl.num_workers +
            " desired=" +
            pl.desired_workers +
            "</span></div>";
        }
        html +=
          '<div class="stats-kv"><span class="stats-label">Concurrency</span><span class="stats-value">' +
          pl.current_concurrency +
          "/" +
          pl.max_concurrency +
          "</span></div>";
        if (pl.desired_max_concurrency != null || liveCapParts.length > 0) {
          var workerCapsValue = "";
          if (pl.desired_max_concurrency != null) {
            workerCapsValue += "desired=" + pl.desired_max_concurrency;
          }
          if (liveCapParts.length > 0) {
            if (workerCapsValue) {
              workerCapsValue += " ";
            }
            workerCapsValue += "live=" + liveCapParts.join(", ");
          }
          html +=
            '<div class="stats-kv"><span class="stats-label">Worker caps</span><span class="stats-value">' +
            esc(workerCapsValue) +
            "</span></div>";
        }
        if (pl.scale_generation != null) {
          html +=
            '<div class="stats-kv"><span class="stats-label">Scale gen</span><span class="stats-value">' +
            pl.scale_generation +
            "</span></div>";
        }
        if (pl.effective_target != null) {
          html +=
            '<div class="stats-kv"><span class="stats-label">Target</span><span class="stats-value">' +
            pl.effective_target +
            "</span></div>";
        }
        if (pl.memory_ceiling != null || pl.provider_ceiling != null || pl.operator_cap != null) {
          var ceilingsValue =
            "memory=" +
            (pl.memory_ceiling != null ? pl.memory_ceiling : "-") +
            " provider=" +
            (pl.provider_ceiling != null ? pl.provider_ceiling : "-") +
            " operator=" +
            (pl.operator_cap != null ? pl.operator_cap : "-");
          html +=
            '<div class="stats-kv"><span class="stats-label">Ceilings</span><span class="stats-value">' +
            ceilingsValue +
            "</span></div>";
        }
        if (pl.bottleneck) {
          html +=
            '<div class="stats-kv"><span class="stats-label">Bottleneck</span><span class="stats-value">' +
            esc(pl.bottleneck) +
            "</span></div>";
        }
        if (pl.recent_rate_limits) {
          html +=
            '<div class="stats-kv"><span class="stats-label">Recent 429s</span><span class="stats-value">' +
            pl.recent_rate_limits +
            "</span></div>";
        }
        if (pl.pressure) {
          html +=
            '<div class="stats-kv"><span class="stats-label">Pressure</span><span class="stats-value">' +
            esc(pl.pressure.level) +
            " (" +
            pl.pressure.available_pct.toFixed(0) +
            "% avail)</span></div>";
        }
        html +=
          '<div class="stats-kv"><span class="stats-label">Adjustments</span><span class="stats-value">' +
          pl.concurrency_adjustments +
          "</span></div>";
        if (
          pl.process_starts != null &&
          pl.process_successful_exits != null &&
          pl.process_failed_exits != null
        ) {
          if (
            pl.process_starts ||
            pl.process_successful_exits ||
            pl.process_failed_exits ||
            processKillTotal
          ) {
            html +=
              '<div class="stats-kv"><span class="stats-label">Process exits</span><span class="stats-value">starts=' +
              pl.process_starts +
              " success=" +
              pl.process_successful_exits +
              " failed=" +
              pl.process_failed_exits +
              " killed=" +
              processKillTotal +
              "</span></div>";
          }
        } else if (processKillTotal) {
          console.warn(
            "stats.pool has process_kills_by_reason but missing process_starts/process_successful_exits/process_failed_exits — older stats format",
          );
          html +=
            '<div class="stats-kv"><span class="stats-label">Process exits</span><span class="stats-value">killed=' +
            processKillTotal +
            "</span></div>";
        }
        if (processFailureParts.length > 0) {
          html +=
            '<div class="stats-failures"><span class="stats-label">Process failures</span><span class="stats-value">' +
            processFailureParts.join(", ") +
            "</span></div>";
        }
        if (runnerFailureParts.length > 0) {
          html +=
            '<div class="stats-failures"><span class="stats-label">Retry failures</span><span class="stats-value">' +
            runnerFailureParts.join(", ") +
            "</span></div>";
        }
        html += "</div>";
      }

      // API section
      if (stats.api) {
        var a = stats.api;
        html += '<div class="stats-card">';
        html += '<div class="stats-card-header">API Usage</div>';
        html +=
          '<div class="stats-kv"><span class="stats-label">Total calls</span><span class="stats-value">' +
          a.total_calls.toLocaleString() +
          "</span></div>";
        var modelParts = [];
        for (var m in a.models) {
          modelParts.push(esc(m) + " (" + a.models[m].toLocaleString() + ")");
        }
        if (modelParts.length > 0) {
          html +=
            '<div class="stats-kv"><span class="stats-label">Models</span><span class="stats-value">' +
            modelParts.join(", ") +
            "</span></div>";
        }
        html +=
          '<div class="stats-kv"><span class="stats-label">Cost</span><span class="stats-value">$' +
          a.total_cost_usd.toFixed(2) +
          "</span></div>";
        html +=
          '<div class="stats-kv"><span class="stats-label">Avg calls/item</span><span class="stats-value">' +
          a.avg_calls_per_item +
          "</span></div>";
        if (a.error_count > 0) {
          html +=
            '<div class="stats-kv"><span class="stats-label">Errors</span><span class="stats-value stats-error">' +
            a.error_count.toLocaleString() +
            "</span></div>";
        }
        html += "</div>";
      }

      // Resources section
      if (stats.resources) {
        var r = stats.resources;
        html += '<div class="stats-card">';
        html += '<div class="stats-card-header">Resources</div>';
        html +=
          '<div class="stats-kv"><span class="stats-label">Avg peak RSS</span><span class="stats-value">' +
          formatSize(r.avg_peak_rss_bytes || 0) +
          "</span></div>";
        html +=
          '<div class="stats-kv"><span class="stats-label">Max peak RSS</span><span class="stats-value">' +
          formatSize(r.max_peak_rss_bytes || 0) +
          "</span></div>";
        html +=
          '<div class="stats-kv"><span class="stats-label">Swap</span><span class="stats-value">' +
          r.swap_used_gb.toFixed(1) +
          " GB</span></div>";
        if (r.kills_by_reason && Object.keys(r.kills_by_reason).length > 0) {
          var killTotal = 0;
          var killParts = [];
          for (var reason in r.kills_by_reason) {
            killTotal += r.kills_by_reason[reason];
            killParts.push(reason + ": " + r.kills_by_reason[reason]);
          }
          html +=
            '<div class="stats-kv"><span class="stats-label">Kills</span><span class="stats-value">' +
            killTotal +
            " (" +
            killParts.join(", ") +
            ")</span></div>";
        }
        html += "</div>";
      }

      // No data at all
      if (
        !stats.progress &&
        !stats.throughput &&
        !stats.timing &&
        !stats.pool &&
        !stats.api &&
        !stats.resources
      ) {
        html += '<div class="preview-empty">No stats data available</div>';
      }

      html += "</div>";
      container.innerHTML = html;
    },
    {
      has_progress: !!stats?.progress,
      has_throughput: !!stats?.throughput,
      has_timing: !!stats?.timing,
      has_pool: !!stats?.pool,
      has_api: !!stats?.api,
      has_resources: !!stats?.resources,
    },
  );
}

function fmtDuration(totalSeconds) {
  if (totalSeconds == null) {
    return "?";
  }
  var s = Math.round(totalSeconds);
  if (s < 60) {
    return s + "s";
  }
  var m = Math.floor(s / 60);
  var sec = s % 60;
  if (m < 60) {
    return m + "m " + sec + "s";
  }
  var h = Math.floor(m / 60);
  m = m % 60;
  return h + "h " + m + "m";
}

// ── Copy path ───────────────────────────────────────────────────

window.MetaprocDomainViews = {
  attachVisualResources: attachVisualResources,
  clearPluginDataRequests: clearPluginDataRequests,
  copyResourceTableAs: copyResourceTableAs,
  detachVisualResources: detachVisualResources,
  disposeCharts: disposeCharts,
  loadCharts: loadCharts,
  loadResourcesForRunDir: loadResourcesForRunDir,
  loadStats: loadStats,
  loadVisual: loadVisual,
  loadVizModel: loadVizModel,
  resourceMetricChanged: resourceMetricChanged,
  resourceNodeClicked: resourceNodeClicked,
  resourceTreemapMetricChanged: resourceTreemapMetricChanged,
  resourceTreemapTileClicked: resourceTreemapTileClicked,
  renderResourceReportPayload: renderResourceReportPayload,
  renderResourceTreemapPayload: renderResourceTreemapPayload,
  setCurrentResourceReportPayload: setCurrentResourceReportPayload,
  openPath: mb.openPath,
};
