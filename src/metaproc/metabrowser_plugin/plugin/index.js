// Metaproc MetaBrowser plugin entry point.
//
// Owns metaproc-domain views (process-spec, resource-report, runpool-log,
// process-log). The "Steps" view (process-spec) is implemented entirely in
// this file as a Mustache template + shared plugin-data request. The
// "Document" and "Source" views (process-spec) reuse the renderers exported by the
// markdown built-in plugin's index.js (mb.builtins.markdown.*) — built-ins
// discover before entry-point plugins, so the namespace is set before
// this script runs.
//
// New views can be added by adding a [[view]] block to manifest.toml and
// a registerView() call here. No metabrowser core changes needed.

(() => {
  const mb = window.metabrowser;
  if (!mb) {
    console.error("metaproc plugin: window.metabrowser missing — SDK not loaded");
    return;
  }

  // ── Generic markdown views (rendered + source) ──────────────────
  //
  // process-spec files ARE markdown; the kind classifier just tags
  // them differently so we can offer the visual + steps views on top.
  // Reuse the markdown plugin's renderers verbatim — Document and
  // Source tabs render identically to a plain .md file.

  if (mb.builtins?.markdown) {
    mb.registerView("process-spec", "rendered", {
      render: mb.builtins.markdown.renderRendered,
    });
    mb.registerView("process-spec", "source", {
      render: mb.builtins.markdown.renderSource,
    });
    mb.registerView("structure-report", "source", {
      render: mb.builtins.markdown.renderSource,
    });
  } else {
    console.warn(
      "metaproc plugin: markdown built-in not loaded; rendered/source views unavailable",
    );
  }

  // Render a flat list of step IDs from the viz-model hook. Cheap; doubles
  // as a smoke test for the plugin pipeline (manifest → index.js → fetch
  // sidekick → render).
  const STEPS_TMPL = `
    <div class="mb-plugin-metaproc steps-view">
      <h3>{{header.name}} — {{stepCount}} step{{#plural}}s{{/plural}}</h3>
      {{#hasSteps}}
        <ul class="step-list">
          {{#steps}}
            <li class="step-item">
              <code>{{id}}</code>
              <span class="step-mode">{{mode}}</span>
              {{#label}}<span class="step-label"> — {{label}}</span>{{/label}}
            </li>
          {{/steps}}
        </ul>
      {{/hasSteps}}
      {{^hasSteps}}
        <p class="empty">No steps in this process spec.</p>
      {{/hasSteps}}
    </div>
  `;

  function asyncView(render, dispose) {
    let generation = 0;
    return {
      async render(container, ctx) {
        const current = ++generation;
        await render(container, ctx, () => current === generation);
      },
      dispose() {
        generation += 1;
        dispose();
      },
    };
  }

  async function renderSteps(container, ctx, isCurrent) {
    try {
      const data = await domainViews().loadVizModel(ctx.path);
      if (!isCurrent()) {
        return;
      }
      const viz = data.viz || {};
      const nodes = (viz.nodes || []).filter((n) => n.kind === "step");
      const header = viz.header || {};
      const steps = nodes.map((n) => ({
        id: n.id,
        mode: n.step?.mode || "",
        label: n.label || "",
      }));
      container.innerHTML = mb.render(STEPS_TMPL, {
        header: { name: header.name || ctx.path },
        steps: steps,
        stepCount: steps.length,
        hasSteps: steps.length > 0,
        plural: steps.length !== 1,
      });
    } catch (err) {
      if (!isCurrent()) {
        return;
      }
      container.innerHTML =
        "<div class='preview-empty'>Steps view error: " +
        mb.escapeHtml(err instanceof Error ? err.message : String(err)) +
        "</div>";
    }
  }

  function domainViews() {
    const views = window.MetaprocDomainViews;
    if (!views) {
      throw new Error("metaproc domain view runtime is unavailable");
    }
    return views;
  }

  function disposeAsyncData() {
    domainViews().clearPluginDataRequests();
  }

  function disposeChartView() {
    const views = domainViews();
    views.clearPluginDataRequests();
    views.disposeCharts();
  }

  mb.registerView("process-spec", "steps", asyncView(renderSteps, disposeAsyncData));

  async function renderCharts(container, ctx, isCurrent) {
    container.innerHTML = '<div class="charts-placeholder preview-empty">Charts loading...</div>';
    await domainViews().loadCharts(container, ctx.path, isCurrent);
  }

  async function renderStats(container, ctx, isCurrent) {
    container.innerHTML = '<div class="stats-placeholder preview-empty">Stats loading...</div>';
    await domainViews().loadStats(container, ctx.path, isCurrent);
  }

  async function renderVisual(container, ctx, isCurrent) {
    container.innerHTML =
      '<div class="viz-placeholder preview-empty">Visualization loading...</div>';
    await domainViews().loadVisual(container, ctx.path, isCurrent);
  }

  // Resource-report data is the parsed JSON of the resources.json file
  // (ctx.raw.content). The consumer domain-view runtime owns the synchronous
  // renderers and state.
  function _parsePayload(ctx) {
    try {
      return JSON.parse(ctx.raw.content || "{}");
    } catch {
      return null;
    }
  }

  function renderResources(container, ctx) {
    const payload = _parsePayload(ctx);
    if (!payload) {
      container.innerHTML = '<div class="preview-empty">Could not parse resource report</div>';
      return;
    }
    const views = domainViews();
    const stamped = views.setCurrentResourceReportPayload(payload, ctx.path);
    container.innerHTML = views.renderResourceReportPayload(stamped || payload, "actual_cost_usd");
  }

  function renderTreemap(container, ctx) {
    const payload = _parsePayload(ctx);
    if (!payload) {
      container.innerHTML = '<div class="preview-empty">Could not parse resource report</div>';
      return;
    }
    const views = domainViews();
    const stamped = views.setCurrentResourceReportPayload(payload, ctx.path);
    container.innerHTML = views.renderResourceTreemapPayload(stamped || payload, "actual_cost_usd");
  }

  function structureReport(ctx) {
    const fm = ctx.frontmatter || {};
    return fm.structure_report || null;
  }

  function mapList(map) {
    if (!map || typeof map !== "object") {
      return "";
    }
    return Object.entries(map)
      .map(
        ([k, v]) =>
          "<li><code>" + mb.escapeHtml(k) + "</code>: " + mb.escapeHtml(String(v)) + "</li>",
      )
      .join("");
  }

  function renderStructureSummary(container, ctx) {
    const report = structureReport(ctx);
    if (!report) {
      container.innerHTML =
        '<div class="preview-empty">No structure_report frontmatter found</div>';
      return;
    }
    const summary = report.summary || {};
    const warnings = report.warnings || [];
    container.innerHTML = `
      <div class="mb-plugin-metaproc structure-report-view">
        <h3>Structure Report</h3>
        <div class="metric-grid">
          <div><strong>${mb.escapeHtml(String(summary.artifacts || 0))}</strong><span>Artifacts</span></div>
          <div><strong>${mb.escapeHtml(String(summary.warnings || 0))}</strong><span>Warnings</span></div>
        </div>
        <h4>Status</h4>
        <ul>${mapList(summary.by_status)}</ul>
        <h4>Stage</h4>
        <ul>${mapList(summary.by_stage)}</ul>
        <h4>Profile</h4>
        <ul>${mapList(summary.by_profile)}</ul>
        ${
          warnings.length
            ? "<h4>Warnings</h4><ul>" +
              warnings
                .map(
                  (w) =>
                    "<li><code>" +
                    mb.escapeHtml(w.code || "") +
                    "</code> " +
                    mb.escapeHtml(w.message || "") +
                    "</li>",
                )
                .join("") +
              "</ul>"
            : ""
        }
      </div>
    `;
  }

  function renderStructureArtifacts(container, ctx) {
    const report = structureReport(ctx);
    if (!report) {
      container.innerHTML =
        '<div class="preview-empty">No structure_report frontmatter found</div>';
      return;
    }
    const rows = (report.artifacts || [])
      .map(
        (a) => `
      <tr>
        <td><code>${mb.escapeHtml(a.id || "")}</code></td>
        <td>${mb.escapeHtml(a.schema || "")}</td>
        <td>${mb.escapeHtml(a.status || "")}</td>
        <td>${mb.escapeHtml(a.stage || "")}</td>
        <td>${mb.escapeHtml(a.producer_step || "")}</td>
        <td>${mb.escapeHtml((a.consumers || []).join(", "))}</td>
        <td>${mb.escapeHtml(String((a.warnings || []).length))}</td>
      </tr>
    `,
      )
      .join("");
    container.innerHTML = `
      <div class="mb-plugin-metaproc structure-report-view">
        <table class="structure-table">
          <thead><tr><th>Artifact</th><th>Schema</th><th>Status</th><th>Stage</th><th>Producer</th><th>Consumers</th><th>Warnings</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  function renderStructureGraph(container, ctx) {
    const report = structureReport(ctx);
    if (!report) {
      container.innerHTML =
        '<div class="preview-empty">No structure_report frontmatter found</div>';
      return;
    }
    const rows = (report.edges || [])
      .map(
        (e) => `
      <tr>
        <td><code>${mb.escapeHtml(e.from || "")}</code></td>
        <td><code>${mb.escapeHtml(e.to || "")}</code></td>
        <td>${mb.escapeHtml(e.carrier || "")}</td>
        <td>${mb.escapeHtml(e.status || "")}</td>
      </tr>
    `,
      )
      .join("");
    container.innerHTML = `
      <div class="mb-plugin-metaproc structure-report-view">
        <table class="structure-table">
          <thead><tr><th>From</th><th>To</th><th>Carrier</th><th>Status</th></tr></thead>
          <tbody>${rows || "<tr><td colspan='4'>No dependency edges reported</td></tr>"}</tbody>
        </table>
      </div>
    `;
  }

  // Reusable raw-JSON renderer for JSONL kinds (runpool-log, process-log).
  // Same body as mb.builtins.agentLog.renderRaw — delegate to keep the
  // metaproc plugin lean.
  function jsonlRaw(container, ctx) {
    if (mb.builtins?.agentLog?.renderRaw) {
      mb.builtins.agentLog.renderRaw(container, ctx);
    } else {
      container.innerHTML = '<div class="preview-empty">Raw view unavailable</div>';
    }
  }
  function jsonlLog(container, ctx) {
    if (mb.builtins?.agentLog?.renderLog) {
      mb.builtins.agentLog.renderLog(container, ctx);
    } else {
      container.innerHTML = '<div class="preview-empty">Log view unavailable</div>';
    }
  }

  // Raw view for the resource-report kind (a single JSON file, not JSONL).
  // /api/file serves it as text with `content`, not `raw_text` — using
  // jsonlRaw here would render an empty <code> block. Pretty-print when
  // possible; fall back to verbatim text for malformed JSON so the
  // operator can still inspect the bytes.
  function jsonRaw(container, ctx) {
    const content = ctx.raw?.content || "";
    let formatted;
    try {
      formatted = JSON.stringify(JSON.parse(content), null, 2);
    } catch {
      formatted = content;
    }
    container.innerHTML = mb.wrapWithCopy(
      '<pre class="code-block"><code class="language-json">' +
        mb.escapeHtml(formatted) +
        "</code></pre>",
    );
  }

  // process-spec
  mb.registerView("process-spec", "visual", asyncView(renderVisual, disposeAsyncData));

  // runpool-log
  mb.registerView("runpool-log", "charts", asyncView(renderCharts, disposeChartView));
  mb.registerView("runpool-log", "stats", asyncView(renderStats, disposeAsyncData));
  mb.registerView("runpool-log", "log", { render: jsonlLog });
  mb.registerView("runpool-log", "raw", { render: jsonlRaw });

  // process-log
  mb.registerView("process-log", "log", { render: jsonlLog });
  mb.registerView("process-log", "raw", { render: jsonlRaw });

  // resource-report — raw uses jsonRaw, not jsonlRaw, because the file
  // is a single JSON document served as text/content (not a JSONL stream).
  mb.registerView("resource-report", "resources", { render: renderResources });
  mb.registerView("resource-report", "treemap", { render: renderTreemap });
  mb.registerView("resource-report", "raw", { render: jsonRaw });

  // structure-report
  mb.registerView("structure-report", "summary", { render: renderStructureSummary });
  mb.registerView("structure-report", "artifacts", { render: renderStructureArtifacts });
  mb.registerView("structure-report", "graph", { render: renderStructureGraph });
})();
