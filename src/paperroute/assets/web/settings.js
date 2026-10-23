/** Save Settings changes in place. */
const autoRebuild = document.querySelector('#auto-rebuild');
const saveButton = document.querySelector('#timetable-save');
const statusLine = document.querySelector('#settings-status');
const NOT_SAVED = {
  refused: 'Not saved',
  network: 'Not saved: network error',
};
let touched = false;

/**
 * Post a JSON body and say how it went.
 * @param {string} url - Where to post.
 * @param {object} body - The fields.
 * @returns {Promise<'ok' | 'refused' | 'network'>} The outcome.
 */
async function postJson(url, body) {
  try {
    const reply = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return reply.ok ? 'ok' : 'refused';
  } catch {
    return 'network';
  }
}

/**
 * Set the switch from the device's `/status`, unless the user has
 * already changed it; leave it on failure.
 * @returns {Promise<void>}
 */
async function loadSettings() {
  try {
    const reply = await fetch('/status');
    const settings = (await reply.json()).settings;
    if (settings && !touched) {
      autoRebuild.checked = settings.auto_rebuild === 'on';
    }
  } catch {
    // The server-drawn state stays.
  }
}

/**
 * Post the switch's state, undoing it and saying so when not saved.
 * @returns {Promise<void>}
 */
async function saveAutoRebuild() {
  touched = true;
  statusLine.textContent = '';
  const value = autoRebuild.checked ? 'on' : 'off';
  const outcome = await postJson('/settings', { auto_rebuild: value });
  if (outcome === 'ok') return;
  autoRebuild.checked = !autoRebuild.checked;
  statusLine.textContent = NOT_SAVED[outcome];
}

saveButton.hidden = true;
autoRebuild.addEventListener('change', saveAutoRebuild);
loadSettings();
