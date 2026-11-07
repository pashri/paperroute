/** Keyboard shortcuts, mirroring the Tk monitor. */
const KEYS = {
  a: ['top', 'left'],
  s: ['top', 'press'],
  d: ['top', 'right'],
  j: ['bottom', 'left'],
  k: ['bottom', 'press'],
  l: ['bottom', 'right'],
};
const HOLD_MS = 800;
const DOUBLE_MS = 300;
const EDITABLE = 'input, textarea, select, [contenteditable]';
const STATUS_MS = 10000;

const frame = document.querySelector('#frame');
const statusLine = document.querySelector('#status');

/**
 * Show a short message on the status line.
 * @param {string} message The text to show.
 * @returns {void}
 */
function showError(message) {
  statusLine.textContent = message;
}

/**
 * Send a knob action to the device, reporting any failure.
 * @param {string} knob `top` or `bottom`.
 * @param {string} action `left`, `right`, `press` or `hold`.
 * @returns {Promise<void>}
 */
function sendKnob(knob, action) {
  const body = new FormData();
  body.set('knob', knob);
  body.set('action', action);
  return fetch('/knob', { method: 'POST', body })
    .then((reply) => {
      if (!reply.ok) showError(`Knob refused (${reply.status})`);
    })
    .catch(() => showError('Knob not sent: network error'));
}

/**
 * Format a number for the status line.
 * @param {number | null | undefined} value The number, if known.
 * @param {number} digits Decimal places.
 * @returns {string} The number, or a dash when absent.
 */
function formatNumber(value, digits) {
  return typeof value === 'number' ? value.toFixed(digits) : '-';
}

/**
 * Write the status line from the `/status` JSON.
 * @param {object} status The parsed response.
 * @returns {void}
 */
function renderStatus(status) {
  const parse = status.feeds?.['v1/gtfs/realtime/buses']?.parse_ms;
  statusLine.textContent =
    `Frame age ${formatNumber(status.frame_age_s, 0)} s, ` +
    `render ${formatNumber(status.last_render_ms, 0)} ms, ` +
    `bus parse ${formatNumber(parse, 0)} ms`;
}

/**
 * Fetch and show the status, leaving it as is on failure.
 * @returns {Promise<void>}
 */
async function refreshStatus() {
  try {
    const reply = await fetch('/status');
    renderStatus(await reply.json());
  } catch {
    statusLine.textContent = 'Status: unavailable';
  }
}

/**
 * Track one knob's press: a tap, a hold past `HOLD_MS`, or turns while
 * it is held, which send `hold-left` or `hold-right` and stop the hold.
 * @param {string} knob `top` or `bottom`.
 * @returns {{start: () => void, turn: (direction: string) => boolean,
 *   finish: () => (string | null), cancel: () => void,
 *   absorb: () => void, isDown: () => boolean}}
 *   `start` on down; `turn` sends a turn while held and says whether it
 *   did; `finish` on up gives `press`, `hold`, `turned`, `chord` after
 *   an absorb, or null when not down; `cancel` forgets the press;
 *   `absorb` drops the pending hold, leaving the press down until it
 *   is released; `isDown` says whether the press is down.
 */
function holdable(knob) {
  const press = {
    timer: null, down: false, long: false, turned: false, absorbed: false,
  };
  const cancel = () => {
    clearTimeout(press.timer);
    Object.assign(press, { timer: null, down: false, absorbed: false });
  };
  const absorb = () => {
    if (!press.down) return;
    clearTimeout(press.timer);
    Object.assign(press, { timer: null, absorbed: true });
  };
  const start = () => {
    if (press.down) return;
    Object.assign(press, { down: true, long: false, turned: false });
    press.timer = setTimeout(() => {
      Object.assign(press, { timer: null, long: true });
    }, HOLD_MS);
  };
  const turn = (direction) => {
    if (!press.down || press.absorbed) return false;
    press.turned = true;
    sendKnob(knob, `hold-${direction}`);
    return true;
  };
  const finish = () => {
    if (!press.down) return null;
    const absorbed = press.absorbed;
    cancel();
    if (absorbed) return 'chord';
    if (press.turned) return 'turned';
    return press.long ? 'hold' : 'press';
  };
  const isDown = () => press.down;
  return { start, turn, finish, cancel, absorb, isDown };
}

const holders = {
  top: holdable('top'),
  bottom: holdable('bottom'),
};

const chord = { timer: null, fired: false };
const downKeys = new Set();
const pressCancels = [];

/**
 * Start the Settings chord when both knobs are down: both presses are
 * absorbed and a `HOLD_MS` timer sends the `both` hold, once for as
 * long as either knob stays down.
 * @returns {void}
 */
function startChord() {
  if (!holders.top.isDown() || !holders.bottom.isDown()) return;
  Object.values(holders).forEach((holder) => holder.absorb());
  if (chord.timer !== null || chord.fired) return;
  chord.timer = setTimeout(() => {
    Object.assign(chord, { timer: null, fired: true });
    sendKnob('both', 'hold');
  }, HOLD_MS);
}

/**
 * Finish a knob's press, ending the chord if it was part of one.
 * @param {string} knob `top` or `bottom`.
 * @returns {string | null} The holder's outcome.
 */
function finishKnob(knob) {
  const outcome = holders[knob].finish();
  if (outcome === 'chord') {
    clearTimeout(chord.timer);
    chord.timer = null;
  }
  if (!holders.top.isDown() && !holders.bottom.isDown()) {
    chord.fired = false;
  }
  return outcome;
}

/**
 * Turn a knob, as a turn while held when its press is down.
 * @param {string} knob `top` or `bottom`.
 * @param {string} direction `left` or `right`.
 * @returns {void}
 */
function turnKnob(knob, direction) {
  if (!holders[knob].turn(direction)) sendKnob(knob, direction);
}

/**
 * Act on a key going down, ignoring repeats, typing and chords.
 * @param {KeyboardEvent} event The key event.
 * @returns {void}
 */
function onKeyDown(event) {
  if (event.repeat || event.target.closest(EDITABLE)) return;
  const key = event.key.toLowerCase();
  const target = KEYS[key];
  if (!target || event.metaKey || event.ctrlKey || event.altKey) return;
  if (target[1] !== 'press') turnKnob(...target);
  else if (!downKeys.has(key)) {
    downKeys.add(key);
    holders[target[0]].start();
    startChord();
  }
}

/**
 * Finish a key that started a press, whatever modifiers are down now.
 * @param {KeyboardEvent} event The key event.
 * @returns {void}
 */
function onKeyUp(event) {
  const key = event.key.toLowerCase();
  if (!downKeys.delete(key)) return;
  const knob = KEYS[key][0];
  const outcome = finishKnob(knob);
  if (outcome === 'press' || outcome === 'hold') sendKnob(knob, outcome);
}

/**
 * Drop every pending press or hold without sending anything.
 * @returns {void}
 */
function cancelAll() {
  downKeys.clear();
  Object.values(holders).forEach((holder) => holder.cancel());
  clearTimeout(chord.timer);
  Object.assign(chord, { timer: null, fired: false });
  pressCancels.forEach((cancel) => cancel());
}

/**
 * Wire a Press button: click presses, double click or long press holds,
 * and turns while it is held down are turns while held.
 * @param {HTMLButtonElement} button A `press` button.
 * @returns {void}
 */
function wirePress(button) {
  const knob = button.dataset.knob;
  const holder = holders[knob];
  let clickTimer = null;
  let swallow = false;
  const cancel = () => {
    swallow = false;
    clearTimeout(clickTimer);
    clickTimer = null;
  };
  pressCancels.push(cancel);
  const onDown = (event) => {
    if (event.button !== 0) return;
    swallow = false;
    holder.start();
    startChord();
  };
  const onUp = () => {
    const outcome = finishKnob(knob);
    if (outcome === 'hold') sendKnob(knob, 'hold');
    if (outcome !== 'press' && outcome !== null) swallow = true;
  };
  const onClick = (event) => {
    if (event.detail === 0) {
      swallow = false;
      sendKnob(knob, 'press');
    } else if (swallow) swallow = false;
    else if (clickTimer !== null) {
      clearTimeout(clickTimer);
      clickTimer = null;
      sendKnob(knob, 'hold');
    } else {
      clickTimer = setTimeout(() => {
        clickTimer = null;
        sendKnob(knob, 'press');
      }, DOUBLE_MS);
    }
  };
  button.addEventListener('pointerdown', onDown);
  ['pointerup', 'pointercancel', 'pointerleave'].forEach((name) => {
    button.addEventListener(name, onUp);
  });
  button.addEventListener('contextmenu', (e) => e.preventDefault());
  button.addEventListener('click', onClick);
}

new EventSource('/events').addEventListener('frame', (event) => {
  frame.src = `/frame.png?v=${event.data}`;
});
document.addEventListener('keydown', onKeyDown);
document.addEventListener('keyup', onKeyUp);
window.addEventListener('blur', cancelAll);
document.querySelectorAll('button[data-knob]').forEach((button) => {
  if (button.dataset.action === 'press') {
    wirePress(button);
    return;
  }
  button.addEventListener('click', () => {
    turnKnob(button.dataset.knob, button.dataset.action);
  });
});
refreshStatus();
setInterval(refreshStatus, STATUS_MS);
