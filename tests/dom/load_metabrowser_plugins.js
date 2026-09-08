// JSDOM-shim plugin loader — runs the SDK + every plugin's index.js
// in a Node sandbox with minimal `window`/`document` mocks, then dumps
// the resulting view registry as JSON to stdout. Exercised by Python
// tests under tests/test_plugin_e2e_render.py (Phase 3h).
//
// Usage:
//   node load_metabrowser_plugins.js <metabrowser_package_root> <metaproc_plugin_root>
//     '<manifest_contracts_json>' [extra_plugin_dir ...]
//
// Output: a single JSON line with shape:
//   {"plugins": ["markdown", "agent-log", ...],
//    "registrations": [{"kind": "markdown", "view": "rendered"}, ...],
//    "errors": [...]}
//
// We avoid jsdom on purpose — the SDK only needs `window`, plain
// console, and a tiny `document` stub; spinning up a real DOM would
// add ~150 MB of test deps for negligible gain.

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { loadPluginScripts, loadShell } = require("./plugin_test_utils.js");

/** @returns {never} */
function fail(msg) {
  process.stderr.write(`${msg}\n`);
  process.exit(1);
}

const args = process.argv.slice(2);
if (args.length < 3) {
  fail(
    "usage: load_metabrowser_plugins.js <metabrowser_package_root> " +
      "<metaproc_plugin_root> '<manifest_contracts_json>' [extra_plugin_dir ...]",
  );
}
const metabrowserRootArg = args[0];
const metaprocPluginRootArg = args[1];
const manifestContractsArg = args[2];
if (!metabrowserRootArg || !metaprocPluginRootArg || !manifestContractsArg) {
  fail("required plugin-loader argument is empty");
}
const metabrowserRoot = path.resolve(metabrowserRootArg);
const metaprocPluginRoot = path.resolve(metaprocPluginRootArg);
const manifestContracts = JSON.parse(manifestContractsArg);
const extraDirs = args.slice(3).map((d) => path.resolve(d));

function manifestContract(pluginRoot) {
  const resolved = path.resolve(pluginRoot);
  const contract = manifestContracts[resolved];
  if (!contract) {
    fail(`manifest contract missing for ${resolved}`);
  }
  return contract;
}

// ── Sandbox globals ───────────────────────────────────────────────

const errors = [];

const fakeDocument = {
  // Plugin index.js files only call this from inside their renderers,
  // not at registration time. Returning null is fine for the test.
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
  createElement: () => ({ appendChild: () => {} }),
  documentElement: {},
};

const sandbox = {
  console: {
    log: (...a) => process.stderr.write(`[plugin:log] ${a.join(" ")}\n`),
    warn: (...a) => process.stderr.write(`[plugin:warn] ${a.join(" ")}\n`),
    error: (...a) => process.stderr.write(`[plugin:error] ${a.join(" ")}\n`),
  },
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  fetch: () => Promise.reject(new Error("fetch unavailable in shim")),
  Promise,
  Set,
  Map,
  // Mustache and Chart globals are referenced by some plugins. We don't
  // render in the shim; just stub them so module loading does not throw.
  Mustache: { render: (tpl) => tpl },
  Chart: () => {},
  // hljs is referenced from app.js's toggleEvent; not relevant here.
  hljs: { highlightElement: () => {} },
  HTMLCanvasElement: () => {},
};

// `window` is the global; many plugins call `(typeof window !== "undefined")`
// which evaluates against the sandbox global, so set both.
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.document = fakeDocument;

vm.createContext(sandbox);

function load(filepath, label) {
  try {
    const src = fs.readFileSync(filepath, "utf-8");
    vm.runInContext(src, sandbox, { filename: label });
  } catch (err) {
    errors.push({ file: label, error: err instanceof Error ? err.message : String(err) });
  }
}

// ── 1. Plugin SDK ─────────────────────────────────────────────────

loadShell(metabrowserRoot, load);

if (!sandbox.metabrowser) {
  fail("the plugin SDK did not set window.metabrowser");
}

// ── 1b. Install a tracking Proxy on mb.builtins ───────────────────
//
// Enforces the "render-time-only namespace rule" from
// metabrowser-plugins.md: a plugin's top-level IIFE MUST NOT
// dereference another plugin's mb.builtins.<other> namespace unless
// <other> has already loaded. Anything that violates the rule
// resolves to `undefined` during shim load, and we record the bad
// access so the Python test can fail.
//
// Reads inside renderView callbacks happen at render time (never
// triggered by this shim), so they pass through silently — exactly
// matching the rule.
const _builtinReads = []; // {plugin, key, hadValue}
let _currentLoadingPlugin = null;
const _builtinsTarget = {};
const _builtinsProxy = new Proxy(_builtinsTarget, {
  get(target, key) {
    if (_currentLoadingPlugin !== null && typeof key === "string" && key !== "constructor") {
      _builtinReads.push({
        plugin: _currentLoadingPlugin,
        key: key,
        hadValue: target[key] !== undefined,
      });
    }
    return target[key];
  },
  set(target, key, value) {
    target[key] = value;
    return true;
  },
  has(target, key) {
    return key in target;
  },
});
sandbox.metabrowser.builtins = _builtinsProxy;

// Wrap the existing load() so we know which plugin is executing.
const _rawLoad = load;
function loadPlugin(filepath, label, pluginName) {
  _currentLoadingPlugin = pluginName;
  try {
    _rawLoad(filepath, label);
  } finally {
    _currentLoadingPlugin = null;
  }
}

async function main() {
  // ── 2. Built-in plugins (alphabetical, matching discovery order) ──

  const builtinRoot = path.join(metabrowserRoot, "builtin_plugins");
  const builtinNames = fs
    .readdirSync(builtinRoot)
    .filter((name) => {
      const stat = fs.statSync(path.join(builtinRoot, name));
      return stat.isDirectory() && !name.startsWith("_") && !name.startsWith(".");
    })
    .sort();

  const loadedPlugins = [];
  const loadedPluginRoots = [];

  for (const name of builtinNames) {
    const indexPath = path.join(builtinRoot, name, "index.js");
    const manifestPath = path.join(builtinRoot, name, "manifest.toml");
    if (!fs.existsSync(indexPath) || !fs.existsSync(manifestPath)) {
      continue;
    }
    await loadPluginScripts(
      path.join(builtinRoot, name),
      manifestContract(path.join(builtinRoot, name)).extra_scripts,
      (file, label) => loadPlugin(file, label, name),
      `builtin/${name}`,
      sandbox,
    );
    loadedPlugins.push(name);
    loadedPluginRoots.push(path.join(builtinRoot, name));
  }

  // ── 3. Entry-point plugins (just metaproc) ────────────────────────

  const metaprocPluginIndex = path.join(metaprocPluginRoot, "index.js");
  if (fs.existsSync(metaprocPluginIndex)) {
    await loadPluginScripts(
      metaprocPluginRoot,
      manifestContract(metaprocPluginRoot).extra_scripts,
      (file, label) => loadPlugin(file, label, "metaproc"),
      "metaproc",
      sandbox,
    );
    loadedPlugins.push("metaproc");
    loadedPluginRoots.push(metaprocPluginRoot);
  }

  // ── 4. Extra dirs (--plugins-dir / METABROWSER_PLUGINS_DIRS) ──────

  for (const dir of extraDirs) {
    if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) {
      continue;
    }
    const subdirs = fs.readdirSync(dir).sort();
    for (const sub of subdirs) {
      const subPath = path.join(dir, sub);
      if (!fs.statSync(subPath).isDirectory()) {
        continue;
      }
      const indexPath = path.join(subPath, "index.js");
      const manifestPath = path.join(subPath, "manifest.toml");
      if (!fs.existsSync(indexPath) || !fs.existsSync(manifestPath)) {
        continue;
      }
      await loadPluginScripts(
        subPath,
        manifestContract(subPath).extra_scripts,
        (file, label) => loadPlugin(file, label, sub),
        `extra/${sub}`,
        sandbox,
      );
      loadedPlugins.push(sub);
      loadedPluginRoots.push(subPath);
    }
  }

  // ── 5. Inspect the view registry ──────────────────────────────────

  const mb = sandbox.metabrowser;
  const registrations = [];
  // listViewsForKind iterates per-kind; we don't have the kind list,
  // The registry is private to the SDK closure. Python parses TOML with
  // `tomllib` and supplies the declared view contract to this shim.
  const declaredViews = loadedPluginRoots.flatMap(
    (pluginRoot) => manifestContract(pluginRoot).views,
  );
  for (const v of declaredViews) {
    const reg = mb.getRegisteredView(v.kind, v.id);
    registrations.push({
      kind: v.kind,
      view: v.id,
      registered: !!reg,
    });
  }

  // Violations of the render-time-only namespace rule: a plugin's top-level
  // IIFE read mb.builtins.<key> and got undefined. Anything that resolved
  // to a real value (e.g. unknown_jsonl reading agentLog after agent_log
  // loaded) is fine — load-order happens to satisfy the read. The foot-gun
  // is the undefined case, so flag only that.
  const namespaceViolations = _builtinReads
    .filter((r) => !r.hadValue)
    .map((r) => ({ plugin: r.plugin, key: r.key }));
  const metaprocVisualView = mb.getRegisteredView("process-spec", "visual");
  const metaprocChartsView = mb.getRegisteredView("runpool-log", "charts");
  const metaprocDomainViewHandlers = [
    "attachVisualResources",
    "copyResourceTableAs",
    "detachVisualResources",
    "loadResourcesForRunDir",
    "resourceMetricChanged",
    "resourceNodeClicked",
    "resourceTreemapMetricChanged",
    "resourceTreemapTileClicked",
  ];

  process.stdout.write(
    `${JSON.stringify({
      plugins: loadedPlugins,
      registrations: registrations,
      errors: errors,
      namespace_violations: namespaceViolations,
      metaproc_viz_loaded: Boolean(sandbox.MetaprocViz?.renderViz),
      metaproc_domain_views_loaded: Boolean(sandbox.MetaprocDomainViews),
      metaproc_domain_view_handlers_loaded: metaprocDomainViewHandlers.every(
        (name) => typeof sandbox.MetaprocDomainViews?.[name] === "function",
      ),
      metaproc_visual_has_dispose: Boolean(metaprocVisualView?.dispose),
      metaproc_charts_has_dispose: Boolean(metaprocChartsView?.dispose),
    })}\n`,
  );
}

main().catch((err) => fail(err instanceof Error ? err.message : String(err)));
