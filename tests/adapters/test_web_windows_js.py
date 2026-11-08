"""Run the window preview script in node against stub browser globals."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from paperroute_transit.adapters.board_web import BOARD_WEB

HARNESS = r"""
import { pathToFileURL } from 'node:url';
globalThis.document = { querySelectorAll: () => [] };
const mod = await import(pathToFileURL(process.argv[2]).href);
const copy = { noDay: 'NO DAY', noTime: 'NO TIME', sameTime: 'SAME' };
const sum = (days, start, end) => mod.windowSummary(days, start, end, copy);
const results = {};
results.weekdays = sum(['mon', 'tue', 'wed', 'thu', 'fri'], '07:00', '09:00');
results.overnight = sum(['sat'], '22:00', '02:00');
results.every = sum(
  ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], '07:00', '09:00',
);
results.weekends = sum(['sun', 'sat'], '08:00', '09:00');
results.some = sum(['wed', 'mon'], '07:00', '09:00');
results.noDay = sum([], '07:00', '09:00');
results.noTime = [sum(['mon'], '', '09:00'), sum(['mon'], '07:00', '')];
results.same = sum(['mon'], '07:00', '07:00');

const box = (value, checked) => ({ type: 'checkbox', value, checked });
const time = (value) => ({ type: 'time', value });
const row = (days, start, end) => {
  const out = { textContent: 'stale' };
  const fields = [...days.map((d) => box(d, true)), time(start), time(end)];
  return {
    fields, out,
    querySelectorAll: (selector) => fields.filter((f) =>
      selector === 'input[type="checkbox"]'
        ? f.type === 'checkbox' : f.type === 'time'),
    querySelector: () => out,
  };
};
const rows = [row(['mon', 'wed'], '07:00', '09:00'), row([], '', '')];
const handlers = {};
const fieldset = {
  dataset: { noDay: 'NO DAY', noTime: 'NO TIME', sameTime: 'SAME' },
  querySelectorAll: () => rows,
  addEventListener: (name, fn) => { handlers[name] = fn; },
};
mod.attachWindowPreview(fieldset);
results.listening = Object.keys(handlers).sort();
results.initial = rows.map((r) => r.out.textContent);
rows[1].fields.push(box('fri', true));
rows[1].fields[0].value = '10:00';
rows[1].fields[1].value = '10:00';
handlers.input({});
results.afterInput = rows.map((r) => r.out.textContent);
rows[0].fields[2].value = '';
handlers.change({});
results.afterChange = rows[0].out.textContent;
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_windows_script_in_node(tmp_path: Path) -> None:
    """Summaries read well, and the preview follows typing."""
    script = tmp_path / 'windows.mjs'
    script.write_text(
        (BOARD_WEB / 'windows.js').read_text(encoding='utf-8'),
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
        'weekdays': 'Shows Weekdays, 07:00 to 09:00',
        'overnight': 'Shows Sat, 22:00 to 02:00 (ends next day)',
        'every': 'Shows Every day, 07:00 to 09:00',
        'weekends': 'Shows Weekends, 08:00 to 09:00',
        'some': 'Shows Mon, Wed, 07:00 to 09:00',
        'noDay': 'NO DAY',
        'noTime': ['NO TIME', 'NO TIME'],
        'same': 'SAME',
        'listening': ['change', 'click', 'input'],
        'initial': ['Shows Mon, Wed, 07:00 to 09:00', 'NO DAY'],
        'afterInput': ['Shows Mon, Wed, 07:00 to 09:00', 'SAME'],
        'afterChange': 'NO TIME',
    }
