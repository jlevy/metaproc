const path = require("node:path");

function loadPluginScripts(pluginRoot, extraScripts, load, label) {
  for (const script of extraScripts) {
    load(path.join(pluginRoot, script), `${label}/${script}`);
  }
  load(path.join(pluginRoot, "index.js"), `${label}/index.js`);
}

module.exports = { loadPluginScripts };
