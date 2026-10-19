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
 * Press, or hold after `HOLD_MS`, for one control.
 * @param {string} knob `top` or `bottom`.
 * @returns {{start: () => void, finish: () => void, cancel: () => void}}
 *   Call `start` on down, `finish` on up and `cancel` to send nothing.
 */
function holdable(knob) {
  let timer = null;
  const cancel = () => {
    clearTimeout(timer);
    timer = null;
  };
  const start = () => {
    if (timer !== null) return;
    timer = setTimeout(() => {
      timer = null;
      sendKnob(knob, 'hold');
    }, HOLD_MS);
  };
  const finish = () => {
    if (timer === null) return;
    cancel();
    sendKnob(knob, 'press');
  };
  return { start, finish, cancel };
}

const holders = {
  top: holdable('top'),
  bottom: holdable('bottom'),
};

/**
 * Handle a key going down or up, ignoring repeats, typing and chords.
 * @param {KeyboardEvent} event The key event.
 * @param {boolean} isDown True for keydown.
 * @returns {void}
 */
function onKey(event, isDown) {
  if (event.repeat || event.target.closest(EDITABLE)) return;
  const target = KEYS[event.key.toLowerCase()];
  if (!target || event.metaKey || event.ctrlKey || event.altKey) return;
  const [knob, action] = target;
  if (action !== 'press') {
    if (isDown) sendKnob(knob, action);
  } else if (isDown) {
    holders[knob].start();
  } else {
    holders[knob].finish();
  }
}

/**
 * Wire a Press button to press on tap and hold on long-press.
 * @param {HTMLButtonElement} button A `press` button.
 * @returns {void}
 */
function wirePress(button) {
  const holder = holders[button.dataset.knob];
  button.addEventListener('pointerdown', holder.start);
  button.addEventListener('pointerup', holder.finish);
  button.addEventListener('pointercancel', holder.cancel);
  button.addEventListener('pointerleave', holder.cancel);
  button.addEventListener('contextmenu', (e) => e.preventDefault());
  button.addEventListener('click', (event) => {
    if (event.detail === 0) sendKnob(button.dataset.knob, 'press');
  });
}

new EventSource('/events').addEventListener('frame', (event) => {
  frame.src = `/frame.png?v=${event.data}`;
});
document.addEventListener('keydown', (event) => onKey(event, true));
document.addEventListener('keyup', (event) => onKey(event, false));
document.querySelectorAll('button[data-knob]').forEach((button) => {
  if (button.dataset.action === 'press') {
    wirePress(button);
    return;
  }
  button.addEventListener('click', () => {
    sendKnob(button.dataset.knob, button.dataset.action);
  });
});
refreshStatus();
setInterval(refreshStatus, STATUS_MS);
