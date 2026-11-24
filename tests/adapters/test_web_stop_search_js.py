"""Run the stop search script in node against stub browser globals."""

import shutil
from pathlib import Path

import pytest

from paperroute_transit.adapters.board_web import BOARD_WEB
from tests.adapters.node_harness import run_in_node

HARNESS = r"""
import { pathToFileURL } from 'node:url';
const selects = [{ id: 'one' }, { id: 'two' }];
globalThis.document = {
  querySelectorAll: (selector) => {
    results.selector = selector;
    return selects;
  },
};
const results = {};
const made = [];
globalThis.TomSelect = class {
  constructor(select, options) { made.push([select.id, options]); }
};
const mod = await import(pathToFileURL(process.argv[2]).href);
const escape = (text) => text.replace(/</g, '&lt;');
results.option = mod.stopOption(
  { id: 'P', name: 'Parramatta', kinds: ['train'], routes: ['T1', '606'] },
);
results.html = mod.renderStop(
  { text: 'A <b>', routes: ['T1', '<6'] }, escape,
);
results.wrapped = made.map(([id]) => id);
const options = made[0][1];
let cleared = 0;
const search = { clearOptions: () => { cleared += 1; } };
results.load = [
  options.shouldLoad('p'), options.shouldLoad(' p '),
  options.shouldLoad('pa'),
];
globalThis.fetch = async (url) => {
  results.url = url;
  return { ok: true, json: async () => [{ id: 'P', name: 'N', routes: [] }] };
};
results.loaded = await new Promise((resolve) => {
  options.load.call(search, 'a&b', resolve);
});
globalThis.fetch = async () => ({ ok: false });
results.failed = await new Promise((resolve) => {
  options.load.call(search, 'zz', resolve);
});
globalThis.fetch = async () => { throw new Error('offline'); };
results.offline = await new Promise((resolve) => {
  options.load.call(search, 'zz', (rows) => resolve(rows ?? null));
});
results.cleared = cleared;
results.item = options.render.item({ text: 'N<' }, escape);
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_stop_search_script_in_node(tmp_path: Path) -> None:
    """Options map, rows escape, results replace old ones, all wrapped."""
    assert run_in_node(
        tmp_path=tmp_path,
        source=BOARD_WEB / 'stop-search.js',
        name='stop-search.mjs',
        harness=HARNESS,
    ) == {
        'selector': 'select[data-stop-search]',
        'option': {
            'value': 'P', 'text': 'Parramatta', 'routes': ['T1', '606'],
        },
        'html': (
            '<div><b>A &lt;b></b><div class="routes">'
            '<span class="chip">T1</span><span class="chip">&lt;6</span>'
            '</div></div>'
        ),
        'wrapped': ['one', 'two'],
        'load': [False, False, True],
        'url': '/pins/stops.json?q=a%26b',
        'loaded': [{'value': 'P', 'text': 'N', 'routes': []}],
        'failed': [],
        'offline': None,
        'cleared': 2,
        'item': '<div>N&lt;</div>',
    }
