"""Run the suggestion script in node against stub browser globals."""

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
const results = {};
const forms = [];
globalThis.document = {
  querySelectorAll: (selector) => {
    results.selector = selector;
    return [{ closest: () => { const f = { addEventListener: (type) => { results.listens = type; } }; forms.push(f); return f; } }];
  },
};
const mod = await import(pathToFileURL(process.argv[2]).href);
const list = { outerHTML: '<div data-suggestions></div>', innerHTML: '' };
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
results.failed = list.innerHTML;
list.innerHTML = '';
globalThis.fetch = async () => ({ ok: false, status: 429 });
await mod.loadSuggestions(form);
results.limited = list.innerHTML;
results.day = day.value;
console.log(JSON.stringify(results));
"""
FAILURE = (
    '<p class="failure">'
    "Trip Planner didn't answer. Try again in a minute.</p>"
)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_load_suggestions_in_node(tmp_path: Path) -> None:
    """The list is swapped in; a failed fetch shows the failure line."""
    web_dir = files('paperroute') / 'assets' / 'web'
    script = tmp_path / 'suggest.mjs'
    script.write_text(
        (web_dir / 'suggest.js').read_text(encoding='utf-8'),
        encoding='utf-8',
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
        'selector': '[data-suggestions]',
        'listens': 'click',
        'url': (
            '/pins/ways/suggest?from=A&day=2026-10-28'
            '&part=suggestions&page=later'
        ),
        'found': '[data-suggestions]',
        'swapped': '<div class="suggestions">new</div>',
        'failed': FAILURE,
        'limited': FAILURE,
        'day': '2026-10-28',
    }
