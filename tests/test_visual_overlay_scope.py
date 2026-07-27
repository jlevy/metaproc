"""Regression: Visual-tab resource overlay must never contaminate across runs.

Bug: opening run A's ``resources.json`` used to seed a module-level
``currentVisualResources`` variable that was then applied to every later
``MetaprocViz.renderViz`` call regardless of which visual (i.e. which run)
was being drawn. Because resource node IDs are generic step ids like
``predict`` or ``postprocess``, the decorator would happily match run B's
visual and paint run A's cost badges onto it.

This test drives the consumer domain-view runtime through Node with a minimal browser shim and
proves three properties of the new overlay contract:

1. Opening a resources.json tab does NOT attach the payload as a
   visual overlay. The auto-seed is gone.
2. ``attachVisualResources(payload, processPath)`` pins the overlay to
   exactly one process-spec path; visuals rendered for a different path
   get no decorator.
3. ``detachVisualResources()`` clears the attachment.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from metaproc.metabrowser_plugin import plugin_dir

_NODE = shutil.which("node")


DOMAIN_VIEWS_JS = plugin_dir() / "domain_views.js"


def _harness_script(domain_views_js_path: Path) -> str:
    """Build the Node harness that loads consumer views under a shim."""
    return textwrap.dedent(
        f"""
        const fs = require('fs');
        const src = fs.readFileSync({json.dumps(str(domain_views_js_path))}, 'utf8');

        // Minimal shims for the consumer domain-view runtime.
        const noopElement = {{
          style: {{}},
          classList: {{add: () => {{}}, remove: () => {{}}, toggle: () => {{}}, contains: () => false}},
          appendChild: () => {{}},
          addEventListener: () => {{}},
          removeEventListener: () => {{}},
          setAttribute: () => {{}},
          getAttribute: () => null,
          innerHTML: '',
          textContent: '',
          children: [],
          parentElement: null,
          closest: () => null,
        }};
        const makeEl = () => ({{...noopElement}});
        const document = {{
          querySelector: () => makeEl(),
          querySelectorAll: () => [],
          getElementById: () => makeEl(),
          createElement: () => makeEl(),
          createElementNS: () => makeEl(),
          addEventListener: () => {{}},
          body: makeEl(),
          head: makeEl(),
          documentElement: makeEl(),
        }};
        const localStorage = {{
          getItem: () => null,
          setItem: () => {{}},
          removeItem: () => {{}},
          clear: () => {{}},
        }};
        const sessionStorage = {{
          getItem: () => null,
          setItem: () => {{}},
          removeItem: () => {{}},
          clear: () => {{}},
        }};
        const setTimeout = (fn) => fn();
        const clearTimeout = () => {{}};
        const setInterval = () => 0;
        const clearInterval = () => {{}};
        const location = {{hash: '', search: '', pathname: '/'}};
        const history = {{replaceState: () => {{}}, pushState: () => {{}}}};
        const navigator = {{userAgent: 'node-test'}};
        const captured = {{decorators: null, visualPath: null}};
        const window = {{
          document,
          location,
          history,
          navigator,
          addEventListener: () => {{}},
          matchMedia: () => ({{matches: false, addListener: () => {{}}}}),
          MetaprocViz: {{
            renderViz: async function (container, viz, options) {{
              captured.decorators = options ? options.decorators || [] : [];
              return Promise.resolve();
            }},
          }},
          MetabrowserIcons: {{}},
          MetabrowserFileTypes: {{}},
          MetabrowserTooltip: null,
        }};
        window.metabrowser = {{
          perf: {{measureAsync: async function (_name, fn) {{ return await fn(); }}}},
          escapeHtml: function (value) {{ return String(value); }},
          formatSize: function (value) {{ return String(value); }},
          openPath: function () {{}},
        }};
        const fetch = async () => ({{
          ok: true,
          json: async () => ({{}}),
          text: async () => '',
        }});

        // Evaluate domain_views.js inside a sandbox-ish scope so ``var`` decls leak to us.
        // Python f-string needs the literal eval() call; we pass src verbatim.
        const scope = new Function(
          'window', 'document', 'location', 'history', 'navigator', 'fetch',
          'localStorage', 'sessionStorage', 'setTimeout', 'clearTimeout',
          'setInterval', 'clearInterval',
          src + '\\nreturn {{' +
            'attachVisualResources, detachVisualResources, ' +
            'setCurrentResourceReportPayload, renderVizPayload, ' +
            'getState: function() {{ return {{' +
              'currentVisualResources: currentVisualResources, ' +
              'currentVisualResourcesPath: currentVisualResourcesPath, ' +
              'visualLoadedForPath: visualLoadedForPath, ' +
            '}}; }}, ' +
            'setVisualLoadedForPath: function (p) {{ visualLoadedForPath = p; }}' +
          '}};'
        );
        const api = scope(
          window, document, location, history, navigator, fetch,
          localStorage, sessionStorage, setTimeout, clearTimeout,
          setInterval, clearInterval
        );

        async function run() {{
          const results = [];

          const runAPayload = {{
            schema: 'metaproc.resources/v1',
            run_id: 'run-A',
            hierarchy_root: {{
              node_id: 'run-A',
              node_type: 'run',
              children: [
                {{node_id: 'predict', node_type: 'step', total_metrics: {{actual_cost_usd: 1.23}}, self_metrics: {{}}, children: [], log_summary: {{}}}},
              ],
              total_metrics: {{}}, self_metrics: {{}}, log_summary: {{}},
            }},
          }};

          // 1) Opening run A resource report should NOT auto-attach the overlay.
          api.setCurrentResourceReportPayload(runAPayload, 'runs/A/resources.json');
          const afterReport = api.getState();
          results.push({{
            name: 'report-tab-does-not-auto-attach',
            pass: afterReport.currentVisualResources === null
               && afterReport.currentVisualResourcesPath === null,
            state: afterReport,
          }});

          // 2) Rendering any visual with nothing attached passes zero decorators.
          api.setVisualLoadedForPath('earnings/process/mine/mine.process.md');
          captured.decorators = null;
          await api.renderVizPayload({{}}, {{viz: {{nodes: [], edges: []}}}});
          results.push({{
            name: 'no-decorator-without-explicit-attach',
            pass: Array.isArray(captured.decorators) && captured.decorators.length === 0,
            got: captured.decorators,
          }});

          // 3) Attaching the payload to process path P applies the decorator ONLY for P.
          const procA = 'earnings/process/mine/mine.process.md';
          api.attachVisualResources(runAPayload, procA);

          api.setVisualLoadedForPath(procA);
          captured.decorators = null;
          await api.renderVizPayload({{}}, {{viz: {{nodes: [], edges: []}}}});
          results.push({{
            name: 'decorator-applied-when-path-matches',
            pass: Array.isArray(captured.decorators) && captured.decorators.length === 1,
            got: captured.decorators && captured.decorators.length,
          }});

          // 4) Opening a DIFFERENT visual (run B's process) must NOT inherit the overlay.
          const procB = 'earnings/process/predict/predict.process.md';
          api.setVisualLoadedForPath(procB);
          captured.decorators = null;
          await api.renderVizPayload({{}}, {{viz: {{nodes: [], edges: []}}}});
          results.push({{
            name: 'no-decorator-on-different-visual-path',
            pass: Array.isArray(captured.decorators) && captured.decorators.length === 0,
            got: captured.decorators && captured.decorators.length,
          }});

          // 5) detachVisualResources() clears attachment and the decorator stops
          //    applying even on the originally-matched path.
          api.detachVisualResources();
          api.setVisualLoadedForPath(procA);
          captured.decorators = null;
          await api.renderVizPayload({{}}, {{viz: {{nodes: [], edges: []}}}});
          results.push({{
            name: 'detach-clears-attachment',
            pass: Array.isArray(captured.decorators) && captured.decorators.length === 0,
            got: captured.decorators && captured.decorators.length,
          }});

          process.stdout.write(JSON.stringify(results));
        }}
        run().catch(err => {{
          process.stderr.write(err && err.stack || String(err));
          process.exit(1);
        }});
        """
    )


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_visual_overlay_does_not_cross_contaminate_runs(tmp_path: Path) -> None:
    """Stale overlay state must not leak across runs or process-spec paths."""
    assert _NODE is not None  # narrows for basedpyright; the skipif guards at runtime
    script = tmp_path / "harness.js"
    script.write_text(_harness_script(DOMAIN_VIEWS_JS))

    result = subprocess.run(
        [_NODE, str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"node harness failed:\n{result.stderr}"
    cases = json.loads(result.stdout.strip())
    failures = [c for c in cases if not c["pass"]]
    assert not failures, "overlay scope regressions:\n" + "\n".join(
        f"  - {c['name']}: {c}" for c in failures
    )
    # Sanity: every expected case actually ran.
    names = {c["name"] for c in cases}
    assert names == {
        "report-tab-does-not-auto-attach",
        "no-decorator-without-explicit-attach",
        "decorator-applied-when-path-matches",
        "no-decorator-on-different-visual-path",
        "detach-clears-attachment",
    }
