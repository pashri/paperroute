"""Run the hand way editor's step script in node."""

import shutil
from pathlib import Path

import pytest

from paperroute_transit.adapters.board_web import BOARD_WEB
from tests.adapters.node_harness import FORMS_PREAMBLE, run_in_node

HARNESS = FORMS_PREAMBLE + r"""
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
    assert run_in_node(
        tmp_path=tmp_path,
        source=BOARD_WEB / 'ways.js',
        name='ways.mjs',
        harness=HARNESS,
    ) == {
        'selector': 'form[data-steps]',
        'url': '/pins/connect?stop=G1&off=H+2&part=steps',
        'found': 'fieldset.steps',
        'swapped': '<fieldset class="steps">new',
        'kept': '<fieldset class="steps">new',
        'listens': ['change'],
    }
