/** Keyboard shortcuts, mirroring the Tk monitor. */
const KEYS = {
  a: ['top', 'left'],
  s: ['top', 'press'],
  d: ['top', 'right'],
  j: ['bottom', 'left'],
  k: ['bottom', 'press'],
  l: ['bottom', 'right'],
};
const STATUS_MS = 10000;

const frame = document.querySelector('#frame');
const statusLine = document.querySelector('#status');

/**
 * Send a knob action to the device.
 * @param {string} knob `top` or `bottom`.
 * @param {string} action `left`, `right` or `press`.
 * @returns {Promise<Response>} The server's reply.
 */
function sendKnob(knob, action) {
  const body = new FormData();
  body.set('knob', knob);
  body.set('action', action);
  return fetch('/knob', { method: 'POST', body });
}

/**
 * Format a number for the status line.
 * @param {number | null | undefined} value The number, if known.
 * @param {number} digits Decimal places.
 * @returns {string} The number, or a dash when absent.
 */
function show(value, digits) {
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
    `Frame age ${show(status.frame_age_s, 0)} s, ` +
    `render ${show(status.last_render_ms, 0)} ms, ` +
    `bus parse ${show(parse, 0)} ms`;
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
 * Press the knob for a key, ignoring other keys.
 * @param {KeyboardEvent} event The key event.
 * @returns {void}
 */
function onKey(event) {
  const target = KEYS[event.key.toLowerCase()];
  if (target && !event.metaKey && !event.ctrlKey && !event.altKey) {
    sendKnob(...target);
  }
}

new EventSource('/events').addEventListener('frame', (event) => {
  frame.src = `/frame.png?v=${event.data}`;
});
document.addEventListener('keydown', onKey);
document.querySelectorAll('button[data-knob]').forEach((button) => {
  button.addEventListener('click', () => {
    sendKnob(button.dataset.knob, button.dataset.action);
  });
});
refreshStatus();
setInterval(refreshStatus, STATUS_MS);
