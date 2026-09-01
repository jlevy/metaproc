// JSDOM-shim view renderer — runs the SDK + every plugin's index.js,
// then invokes a registered renderer with a synthetic `/api/file`
// payload and prints the resulting innerHTML to stdout. Used by
// Python tests under tests/test_metaproc_plugin_render.py to verify
// per-view render behaviour without spinning up a real browser.
//
// Usage:
//   node render_metabrowser_view.js <metabrowser_package_root> <metaproc_plugin_root>
//     '<manifest_contracts_json>' <kind> <view_id> '<json_payload>'
//
// Output: the container's innerHTML on stdout.
//
// Sandbox note: same minimal mocks as load_plugins.js; just enough
// for module-load + a synchronous render call.

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
if (args.length !== 6) {
  fail(
    "usage: render_metabrowser_view.js <metabrowser_package_root> " +
      "<metaproc_plugin_root> '<manifest_contracts_json>' " +
      "<kind> <view_id> '<json_payload>'",
  );
}
const metabrowserRoot = args[0];
const metaprocPluginRoot = args[1];
const contractsJson = args[2];
const kind = args[3];
const viewId = args[4];
const payloadStr = args[5];
if (!metabrowserRoot || !metaprocPluginRoot || !contractsJson || !kind || !viewId || !payloadStr) {
  fail("required view-renderer argument is empty");
}
const manifestContracts = JSON.parse(contractsJson);

function manifestContract(pluginRoot) {
  const resolved = path.resolve(pluginRoot);
  const contract = manifestContracts[resolved];
  if (!contract) {
    fail(`manifest contract missing for ${resolved}`);
  }
  return contract;
}

let payload;
try {
  payload = JSON.parse(payloadStr);
} catch (err) {
  fail(`payload JSON parse error: ${err instanceof Error ? err.message : String(err)}`);
}

// ── Sandbox ────────────────────────────────────────────────────────

const fakeContainer = {
  _innerHTML: "",
  set innerHTML(v) {
    this._innerHTML = v;
  },
  get innerHTML() {
    return this._innerHTML;
  },
  appendChild() {},
  querySelector: () => null,
  querySelectorAll: () => [],
};

const sandbox = {
  console: {
    log: (...a) => process.stderr.write(`[plugin:log] ${a.join(" ")}\n`),
    warn: (...a) => process.stderr.write(`[plugin:warn] ${a.join(" ")}\n`),
    error: (...a) => process.stderr.write(`[plugin:error] ${a.join(" ")}\n`),
  },
  setTimeout,
  clearTimeout,
  Promise,
  Set,
  Map,
  fetch: () => Promise.reject(new Error("fetch unavailable in shim")),
  Mustache: { render: (tpl) => tpl },
  Chart: () => {},
  hljs: { highlightElement: () => {} },
  HTMLCanvasElement: () => {},
  document: {
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    createElement: () => fakeContainer,
    documentElement: {},
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);

function load(filepath, label) {
  const src = fs.readFileSync(filepath, "utf-8");
  vm.runInContext(src, sandbox, { filename: label });
}

loadShell(metabrowserRoot, load);

// Joined here rather than inside main(): the argument checks above narrow these away from
// `string | undefined`, and that narrowing does not reach into a function body.
const builtinRoot = path.join(metabrowserRoot, "builtin_plugins");
const metaprocIndex = path.join(metaprocPluginRoot, "index.js");

async function main() {
  const builtinNames = fs
    .readdirSync(builtinRoot)
    .filter((n) => fs.statSync(path.join(builtinRoot, n)).isDirectory() && !n.startsWith("_"))
    .sort();
  for (const name of builtinNames) {
    const indexPath = path.join(builtinRoot, name, "index.js");
    if (fs.existsSync(indexPath)) {
      const pluginRoot = path.join(builtinRoot, name);
      await loadPluginScripts(
        pluginRoot,
        manifestContract(pluginRoot).extra_scripts,
        load,
        `builtin/${name}`,
        sandbox,
      );
    }
  }

  if (fs.existsSync(metaprocIndex)) {
    await loadPluginScripts(
      metaprocPluginRoot,
      manifestContract(metaprocPluginRoot).extra_scripts,
      load,
      "metaproc",
      sandbox,
    );
  }

  const view = sandbox.metabrowser.getRegisteredView(kind, viewId);
  if (!view) {
    fail(`no view registered for (${kind}, ${viewId})`);
  }

  const ctx = {
    path: payload.path || "",
    kind: payload.kind || kind,
    ext: payload.ext || "",
    size: payload.size || 0,
    frontmatter: payload.frontmatter || {},
    body: payload.content || "",
    raw: payload,
  };

  await view.render(fakeContainer, ctx);
  process.stdout.write(fakeContainer.innerHTML);
}

main().catch((err) => fail(`render error: ${err instanceof Error ? err.message : String(err)}`));
