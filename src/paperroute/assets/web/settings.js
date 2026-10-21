/** Save Settings changes in place and confirm Device actions in a dialog. */
const autoRebuild = document.querySelector('#auto-rebuild');
const saveButton = document.querySelector('#timetable-save');
const statusLine = document.querySelector('#settings-status');
const deviceStatus = document.querySelector('#device-status');
const NOT_SAVED = {
  refused: 'Not saved',
  network: 'Not saved: network error',
};
const NOT_SENT = {
  refused: 'Not sent',
  network: 'Not sent: network error',
};
const POWER_STATUS = {
  poweroff: 'Shutting down...',
  reboot: 'Restarting...',
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
    if (settings && !touched) autoRebuild.checked = settings.auto_rebuild;
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

/**
 * Open a Device button's confirm dialog instead of the confirm page.
 * @param {SubmitEvent} event - The Device form's submit.
 * @returns {void}
 */
function askFirst(event) {
  event.preventDefault();
  const action = event.currentTarget.dataset.power;
  const dialog = document.querySelector(`#${action}-dialog`);
  dialog.returnValue = '';
  dialog.showModal();
}

/**
 * Send the power action when its dialog closes confirmed.
 * @param {Event} event - The dialog's close.
 * @returns {Promise<void>}
 */
async function confirmPower(event) {
  const dialog = event.currentTarget;
  if (dialog.returnValue !== 'confirm') return;
  const action = dialog.dataset.power;
  const outcome = await postJson('/power', { action });
  deviceStatus.textContent =
    outcome === 'ok' ? POWER_STATUS[action] : NOT_SENT[outcome];
}

saveButton.hidden = true;
autoRebuild.addEventListener('change', saveAutoRebuild);
for (const form of document.querySelectorAll('form[data-power]')) {
  form.addEventListener('submit', askFirst);
}
for (const dialog of document.querySelectorAll('dialog[data-power]')) {
  dialog.addEventListener('close', confirmPower);
}
loadSettings();
