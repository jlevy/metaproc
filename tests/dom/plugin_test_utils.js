const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// Metabrowser ships some built-in plugins as ES modules and others as classic scripts.
// The served page loads both with <script type="module"> / <script>; a shim that can only
// run classic scripts silently drops the module ones, and the plugins that borrow from
// them — metaproc reuses `mb.builtins.markdown` — then look broken in tests while working
// in a browser. So detect the shape and use the matching loader.
const ESM_SYNTAX = /^\s*(?:import|export)[\s{*]/m;

function isEsm(source) {
  return ESM_SYNTAX.test(source);
}

/**
 * Evaluate an ES module, and its relative imports, inside an existing vm context.
 *
 * Needs `node --experimental-vm-modules`. Callers get an explicit message rather than
 * `vm.SourceTextModule is not a constructor` if the flag is missing, because that error
 * names nothing a reader can act on.
 */
async function evaluateModule(filepath, label, context) {
  if (typeof vm.SourceTextModule !== "function") {
    throw new Error(`cannot load ES module ${label}: run node with --experimental-vm-modules`);
  }
  const cache = new Map();

  function moduleFor(absolutePath) {
    const cached = cache.get(absolutePath);
    if (cached) {
      return cached;
    }
    const source = fs.readFileSync(absolutePath, "utf-8");
    const module = new vm.SourceTextModule(source, {
      context,
      identifier: absolutePath,
      initializeImportMeta(meta) {
        meta.url = `file://${absolutePath}`;
      },
    });
    cache.set(absolutePath, module);
    return module;
  }

  const entry = moduleFor(filepath);
  await entry.link((specifier, referencing) => {
    if (!specifier.startsWith(".")) {
      throw new Error(`${label}: unexpected bare import ${specifier}`);
    }
    return moduleFor(path.resolve(path.dirname(referencing.identifier), specifier));
  });
  await entry.evaluate();
}

/**
 * Load one plugin's assets the way the shell does: extra scripts first, then index.js.
 *
 * `load` runs a classic script. `context` is the vm context those scripts share, and is
 * what an ES module entry point is evaluated in, so both shapes see the same
 * `window.metabrowser`.
 */
async function loadPluginScripts(pluginRoot, extraScripts, load, label, context) {
  for (const script of extraScripts) {
    load(path.join(pluginRoot, script), `${label}/${script}`);
  }
  const indexPath = path.join(pluginRoot, "index.js");
  const indexLabel = `${label}/index.js`;
  if (context && isEsm(fs.readFileSync(indexPath, "utf-8"))) {
    await evaluateModule(indexPath, indexLabel, context);
    return;
  }
  load(indexPath, indexLabel);
}

// The shell's own script order, copied from the served page rather than reduced to the
// subset that happened to work. The SDK has prerequisites -- navigation.js publishes
// MetabrowserNavigationRoute, resource-context.js publishes the resource-context store --
// and it throws without them; the built-ins have their own, which is why filter-controls
// is here even though nothing in this repository calls it. Loading only plugin-sdk.js was
// correct when it had no prerequisites and broke silently when it gained some.
// Keep in sync with the <script> block in metabrowser's server.py.
//
// file-type-taxonomy.js is deliberately absent: it reads a registry the server injects
// into METABROWSER_SETTINGS and throws without it, and no plugin under test consumes the
// taxonomy, so forging one here would only be a second definition to drift against.
const SHELL_SCRIPTS = [
  "asset-loader.js",
  "theme-state.js",
  "request-error.js",
  "formatters.js",
  "inventory-scope.js",
  "directory-totals-store.js",
  "contribution-registry.js",
  "resource-context.js",
  "view-state.js",
  "navigation.js",
  "source-append.js",
  "plugin-sdk.js",
  "perf.js",
  "filter-state.js",
  "filter-controls.js",
  "icons.js",
];

/** Resolve one shell asset under a metabrowser package root. */
function shellScriptPath(metabrowserRoot, name) {
  const direct = path.join(metabrowserRoot, "static", name);
  if (fs.existsSync(direct)) {
    return direct;
  }
  // plugin-sdk.js was plugin_sdk.js before Metabrowser 0.9. Resolve by looking, so a
  // rename reports a genuinely missing asset instead of a name this repository guessed.
  const legacy = path.join(metabrowserRoot, "static", name.replace(/-/g, "_"));
  return fs.existsSync(legacy) ? legacy : direct;
}

// Bundles the page fetches on demand through asset-loader.js rather than as startup
// scripts. Keep in sync with `asset_bundles` in metabrowser's server.py. A bundle whose
// files this Metabrowser does not ship is left unpublished, so an older host keeps its
// own "Unknown asset bundle" behavior instead of a forged one.
const ON_DEMAND_BUNDLES = {
  mustache: [{ file: "vendor/mustache.min.js", provides: "Mustache" }],
  "sdk-views": [{ file: "plugin-sdk-views.js", provides: "MetabrowserSourceLineAnchors" }],
};

/**
 * Publish ON_DEMAND_BUNDLES the way the server does and evaluate a requested bundle in
 * the sandbox when asset-loader.js appends its <script> tag.
 */
function installAssetBundles(sandbox, metabrowserRoot, load) {
  const published = {};
  for (const [name, entries] of Object.entries(ON_DEMAND_BUNDLES)) {
    const resolved = entries.map((entry) => ({
      src: path.join(metabrowserRoot, "static", entry.file),
      provides: entry.provides,
    }));
    if (resolved.every((entry) => fs.existsSync(entry.src))) {
      published[name] = resolved;
    }
  }
  sandbox.METABROWSER_ASSET_BUNDLES = published;
  // asset-loader.js announces each script it loads with a window event.
  sandbox.Event ??= Event;
  sandbox.CustomEvent ??= CustomEvent;
  sandbox.dispatchEvent ??= () => true;
  const document = sandbox.document;
  const createElement = document.createElement;
  document.createElement = (tag, ...rest) =>
    tag === "script" ? { tagName: "SCRIPT", remove() {} } : createElement(tag, ...rest);
  document.head = {
    ...document.head,
    appendChild(script) {
      setTimeout(() => {
        try {
          load(script.src, path.basename(script.src));
        } catch {
          script.onerror?.();
          return;
        }
        script.onload?.();
      }, 0);
    },
  };
}

/**
 * Load every shell script the plugin SDK and built-ins depend on, in the page's order.
 * With a sandbox, also publish the on-demand bundles the SDK fetches before it renders.
 */
function loadShell(metabrowserRoot, load, sandbox) {
  if (sandbox) {
    installAssetBundles(sandbox, metabrowserRoot, load);
  }
  for (const name of SHELL_SCRIPTS) {
    load(shellScriptPath(metabrowserRoot, name), name);
  }
}

module.exports = {
  ON_DEMAND_BUNDLES,
  SHELL_SCRIPTS,
  evaluateModule,
  isEsm,
  loadPluginScripts,
  loadShell,
  shellScriptPath,
};
