"""Run the suggestion script in node against stub browser globals."""

import shutil
from pathlib import Path

import pytest

from paperroute_transit.adapters.board_web import BOARD_WEB
from tests.adapters.node_harness import FORMS_PREAMBLE, run_in_node

HARNESS = FORMS_PREAMBLE + r"""
const results = {};
const forms = [];
globalThis.document = {
  createElement: (tag) => ({ tagName: tag.toUpperCase(), className: '', textContent: '' }),
  querySelectorAll: (selector) => {
    results.selector = selector;
    return [{ closest: () => { const f = { addEventListener: (type) => { results.listens = type; } }; forms.push(f); return f; } }];
  },
};
const mod = await import(pathToFileURL(process.argv[2]).href);
const list = {
  outerHTML: '<div data-suggestions></div>',
  dataset: { suggestUrl: '/x/suggest', failure: 'No <b>answer</b>' },
  children: [],
  replaceChildren(...children) { this.children = children; },
};
const shown = () => list.children.map(
  (child) => [child.tagName, child.className, child.textContent],
);
const day = { value: '2026-10-28' };
const form = {
  fields: [['from', 'A'], ['day', day.value]],
  querySelector: (selector) => { results.found = selector; return list; },
};
globalThis.fetch = async (url) => {
  results.url = url;
  return { ok: true, text: async () => '<div class="suggestions">new</div>' };
};
await mod.loadSuggestions(form, 'later');
results.swapped = list.outerHTML;
globalThis.fetch = async () => { throw new Error('offline'); };
await mod.loadSuggestions(form);
results.failed = shown();
list.children = [];
globalThis.fetch = async () => ({ ok: false, status: 429 });
await mod.loadSuggestions(form);
results.limited = shown();
list.children = [];
delete list.dataset.suggestUrl;
globalThis.fetch = async (url) => { results.unwired = url; };
await mod.loadSuggestions(form);
results.unplugged = shown();
results.day = day.value;
console.log(JSON.stringify(results));
"""
FAILURE = [['P', 'failure', 'No <b>answer</b>']]


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_load_suggestions_in_node(tmp_path: Path) -> None:
    """
    The list is swapped in; a failed fetch shows the list's failure text.

    A list with no suggestion address fetches nothing and shows the
    failure text.
    """
    assert run_in_node(
        tmp_path=tmp_path,
        source=BOARD_WEB / 'suggest.js',
        name='suggest.mjs',
        harness=HARNESS,
    ) == {
        'selector': '[data-suggestions]',
        'listens': 'click',
        'url': (
            '/x/suggest?from=A&day=2026-10-28'
            '&part=suggestions&page=later'
        ),
        'found': '[data-suggestions]',
        'swapped': '<div class="suggestions">new</div>',
        'failed': FAILURE,
        'limited': FAILURE,
        'unplugged': FAILURE,
        'day': '2026-10-28',
    }
