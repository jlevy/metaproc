const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { loadPluginScripts, loadShell } = require("./plugin_test_utils.js");

const args = process.argv.slice(2);
if (args.length !== 3) {
  throw new Error(
    "usage: test_metaproc_plugin_lifecycle.js <metabrowser_package_root> " +
      "<metaproc_plugin_root> '<manifest_contracts_json>'",
  );
}
const metabrowserRootArg = args[0];
const metaprocPluginRootArg = args[1];
const manifestContractsArg = args[2];
if (!metabrowserRootArg || !metaprocPluginRootArg || !manifestContractsArg) {
  throw new Error("required lifecycle-test argument is empty");
}
const metabrowserRoot = path.resolve(metabrowserRootArg);
const metaprocPluginRoot = path.resolve(metaprocPluginRootArg);
const manifestContracts = JSON.parse(manifestContractsArg);
const metaprocContract = manifestContracts[metaprocPluginRoot];
if (!metaprocContract) {
  throw new Error(`manifest contract missing for ${metaprocPluginRoot}`);
}
const pending = [];
const requestCounts = new Map();
const chartLifecycle = { disposeCalls: 0, renderCalls: 0 };

const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  Promise,
  Set,
  Map,
  URL,
  location: { origin: "http://localhost" },
  fetch(url) {
    const parsed = new URL(url, "http://localhost");
    const key = parsed.pathname + parsed.search;
    requestCounts.set(key, (requestCounts.get(key) || 0) + 1);
    return new Promise((resolve) => pending.push({ url: key, resolve }));
  },
  Mustache: { render: (template) => template },
  Chart: () => {},
  hljs: { highlightElement: () => {} },
  HTMLCanvasElement: () => {},
  document: {
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    createElement: () => ({
      appendChild: () => {},
      addEventListener: () => {},
      classList: { add: () => {} },
      querySelector: () => null,
      querySelectorAll: () => [],
      style: {},
    }),
    documentElement: {},
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

function load(filepath, label) {
  vm.runInContext(fs.readFileSync(filepath, "utf-8"), sandbox, { filename: label });
}

function matchingRequests(fragment) {
  return pending.filter(({ url }) => url.includes(fragment));
}

function ok(payload) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

async function main() {
  loadShell(metabrowserRoot, load);
  // Stubbed rather than loaded: this test is about the metaproc views' own lifecycle, so
  // the built-ins it borrows from are stand-ins. `mountRendered` is the Metabrowser 0.9
  // name for what was `renderRendered`; keeping the old name here would let the real
  // rename pass unnoticed in the one place that pins this contract.
  sandbox.metabrowser.builtins = {
    markdown: { mountRendered: () => {}, renderSource: () => {} },
    agentLog: { renderLog: () => {}, renderRaw: () => {} },
  };
  await loadPluginScripts(
    metaprocPluginRoot,
    metaprocContract.extra_scripts,
    load,
    "metaproc",
    sandbox,
  );

  const renderCalls = [];
  sandbox.MetaprocViz.renderViz = async (container, viz) => {
    renderCalls.push({ container: container.name, viz });
    container.innerHTML = `rendered:${viz.name}`;
  };
  sandbox.MetabrowserCharts = {
    dispose() {
      chartLifecycle.disposeCalls += 1;
    },
    renderPayload(container, data) {
      chartLifecycle.renderCalls += 1;
      container.innerHTML = `charts:${data.name}`;
    },
  };

  const visual = sandbox.metabrowser.getRegisteredView("process-spec", "visual");
  const first = { name: "first", innerHTML: "" };
  const second = { name: "second", innerHTML: "" };
  const firstRender = visual.render(first, { path: "first.md" });
  const secondRender = visual.render(second, { path: "second.md" });

  pending[0].resolve(ok({ viz: { name: "first" } }));
  await firstRender;
  pending[1].resolve(ok({ viz: { name: "second" } }));
  await secondRender;

  const steps = sandbox.metabrowser.getRegisteredView("process-spec", "steps");
  const sharedVisual = { name: "shared-visual", innerHTML: "" };
  const sharedSteps = { name: "shared-steps", innerHTML: "" };
  const sharedVisualRender = visual.render(sharedVisual, { path: "shared.md" });
  const sharedStepsRender = steps.render(sharedSteps, { path: "shared.md" });
  const sharedRequests = matchingRequests("viz-model?process=shared.md");
  if (sharedRequests.length !== 1) {
    throw new Error(`expected one shared viz-model request, got ${sharedRequests.length}`);
  }
  sharedRequests[0].resolve(
    ok({
      viz: {
        name: "shared",
        header: { name: "Shared process" },
        nodes: [{ id: "step-1", kind: "step", step: { mode: "agent" } }],
      },
    }),
  );
  await Promise.all([sharedVisualRender, sharedStepsRender]);

  const failedVisual = { name: "failed-visual", innerHTML: "" };
  const failedVisualRender = visual.render(failedVisual, { path: "failure.md" });
  const failureRequests = matchingRequests("viz-model?process=failure.md");
  failureRequests[0].resolve(
    ok({
      type: "plugin_error",
      error: "Plugin data hook failed",
      detail: "sidekick unavailable",
    }),
  );
  await failedVisualRender;

  const retryVisual = { name: "retry-visual", innerHTML: "" };
  const retryVisualRender = visual.render(retryVisual, { path: "failure.md" });
  const retriedFailureRequests = matchingRequests("viz-model?process=failure.md");
  if (retriedFailureRequests.length !== 2) {
    throw new Error(
      `expected failed data-hook request to retry, got ${retriedFailureRequests.length} request(s)`,
    );
  }
  retriedFailureRequests[1].resolve(ok({ viz: { name: "recovered" } }));
  await retryVisualRender;

  const charts = sandbox.metabrowser.getRegisteredView("runpool-log", "charts");
  const firstCharts = { name: "first-charts", innerHTML: "" };
  const secondCharts = { name: "second-charts", innerHTML: "" };
  const firstChartsRender = charts.render(firstCharts, { path: "run/.logs/pool.jsonl" });
  const secondChartsRender = charts.render(secondCharts, { path: "run/.logs/pool.jsonl" });
  const chartRequests = matchingRequests("charts?path=run%2F.logs%2Fpool.jsonl");
  if (chartRequests.length !== 1) {
    throw new Error(`expected one shared charts request, got ${chartRequests.length}`);
  }
  chartRequests[0].resolve(ok({ name: "pool" }));
  await Promise.all([firstChartsRender, secondChartsRender]);

  const stats = sandbox.metabrowser.getRegisteredView("runpool-log", "stats");
  const firstStatsRender = stats.render(
    { name: "first-stats", innerHTML: "" },
    { path: "run/.logs/pool.jsonl" },
  );
  const secondStats = { name: "second-stats", innerHTML: "" };
  const secondStatsRender = stats.render(secondStats, { path: "run/.logs/pool.jsonl" });
  const statsRequests = matchingRequests("stats?path=run");
  if (statsRequests.length !== 1) {
    throw new Error(`expected one shared stats request, got ${statsRequests.length}`);
  }
  statsRequests[0].resolve(ok({}));
  await Promise.all([firstStatsRender, secondStatsRender]);

  charts.dispose();
  const freshCharts = { name: "fresh-charts", innerHTML: "" };
  const freshChartsRender = charts.render(freshCharts, { path: "run/.logs/pool.jsonl" });
  const freshChartRequests = matchingRequests("charts?path=run%2F.logs%2Fpool.jsonl");
  if (freshChartRequests.length !== 2) {
    throw new Error(
      `expected a fresh charts request after dispose, got ${freshChartRequests.length}`,
    );
  }
  freshChartRequests[1].resolve(ok({ name: "fresh-pool" }));
  await freshChartsRender;

  const resources = sandbox.metabrowser.getRegisteredView("resource-report", "resources");
  const resourceContainer = { name: "resources", innerHTML: "" };
  const resourceRender = resources.render(resourceContainer, {
    path: "runs/current/resources.json",
    raw: {
      content: JSON.stringify({
        schema: "metaproc:ResourcesDocument/0.1",
        run_id: "current",
        hierarchy_root: {
          node_id: "current",
          node_type: "run",
          label: "current",
          self_metrics: {},
          total_metrics: {},
          source_refs: [],
          log_summary: {},
          children: [],
        },
        source_logs: [],
        taxonomy_rollups: {},
      }),
    },
  });
  const runtimeRequests = matchingRequests("viz-model?run_dir=runs%2Fcurrent");
  if (runtimeRequests.length !== 1) {
    throw new Error(`expected one run-context viz-model request, got ${runtimeRequests.length}`);
  }
  runtimeRequests[0].resolve(
    ok({
      viz: {
        task_projection: {
          run_dir: "runs/current",
          tasks: [
            {
              key: { step_id: "collect", item_key: "alpha", scope_path: ["batch"] },
              state: "completed",
              attempt_id: "attempt-1",
              accepted_outputs: [{ name: "report", path: "runs/current/report.md" }],
              unaccepted_outputs: [
                {
                  name: "draft",
                  path: "runs/current/draft.md",
                  reason: "result-not-validated",
                },
              ],
            },
          ],
          coverage_gaps: [
            {
              key: { step_id: "publish", item_key: null, scope_path: ["batch"] },
              reason: "task-state-missing",
            },
            {
              key: { step_id: "review", item_key: null, scope_path: ["batch"] },
              reason: "scope-state-missing",
            },
          ],
        },
      },
    }),
  );
  await resourceRender;

  process.stdout.write(
    JSON.stringify({
      firstHtml: first.innerHTML,
      secondHtml: second.innerHTML,
      renderCalls,
      sharedVisualHtml: sharedVisual.innerHTML,
      failedVisualHtml: failedVisual.innerHTML,
      retryVisualHtml: retryVisual.innerHTML,
      secondChartsHtml: secondCharts.innerHTML,
      secondStatsHtml: secondStats.innerHTML,
      freshChartsHtml: freshCharts.innerHTML,
      vizModelRequestCount: requestCounts.get("/api/plugin/metaproc/viz-model?process=shared.md"),
      failedVizModelRequestCount: requestCounts.get(
        "/api/plugin/metaproc/viz-model?process=failure.md",
      ),
      chartRequestCount: requestCounts.get(
        "/api/plugin/metaproc/charts?path=run%2F.logs%2Fpool.jsonl",
      ),
      statsRequestCount: requestCounts.get("/api/plugin/metaproc/stats?path=run"),
      chartDisposeCalls: chartLifecycle.disposeCalls,
      chartRenderCalls: chartLifecycle.renderCalls,
      resourceHtml: resourceContainer.innerHTML,
      runtimeRequestCount: requestCounts.get(
        "/api/plugin/metaproc/viz-model?run_dir=runs%2Fcurrent",
      ),
    }),
  );
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
