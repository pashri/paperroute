"""Run the hand way editor's step script in node."""

import json
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

HARNESS = r"""
import { pathToFileURL } from 'node:url';
globalThis.FormData = class {
  constructor(form) { this.entries = form.fields; }
  [Symbol.iterator]() { return this.entries[Symbol.iterator](); }
};
const forms = [];
globalThis.document = {
  querySelectorAll: (selector) => { results.selector = selector; return forms; },
};
const results = {};
const mod = await import(pathToFileURL(process.argv[2]).href);
const steps = { outerHTML: '<fieldset class="steps">old</fieldset>' };
const form = {
  fields: [['stop', 'G1'], ['off', 'H 2']],
  getAttribute: (name) => (name === 'action' ? '/pins/connect' : null),
  querySelector: (selector) => { results.found = selector; return steps; },
};
globalThis.fetch = async (url) => {
  results.url = url;
  return { ok: true, text: async () => '<fieldset class="steps">new' };
};
await mod.nextStep(form);
results.swapped = steps.outerHTML;
globalThis.fetch = async () => ({ ok: false });
await mod.nextStep(form);
results.kept = steps.outerHTML;
const listeners = [];
mod.followSteps({ addEventListener: (type, fn) => listeners.push([type, fn]) });
results.listens = listeners.map(([type]) => type);
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_next_step_swaps_fieldset(tmp_path: Path) -> None:
    """The steps fieldset is fetched with `part=steps` and replaced."""
    web_dir = files('paperroute') / 'assets' / 'web'
    script = tmp_path / 'ways.mjs'
    script.write_text(
        (web_dir / 'ways.js').read_text(encoding='utf-8'), encoding='utf-8',
    )
    harness = tmp_path / 'harness.mjs'
    harness.write_text(HARNESS, encoding='utf-8')
    done = subprocess.run(
        ['node', str(harness), str(script)],
        capture_output=True,
        check=True,
        text=True,
    )
    assert json.loads(done.stdout) == {
        'selector': 'form[data-steps]',
        'url': '/pins/connect?stop=G1&off=H+2&part=steps',
        'found': 'fieldset.steps',
        'swapped': '<fieldset class="steps">new',
        'kept': '<fieldset class="steps">new',
        'listens': ['change'],
    }
