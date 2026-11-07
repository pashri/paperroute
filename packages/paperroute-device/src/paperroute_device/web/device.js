/** Confirm the Device page's power actions in a dialog before sending. */
const deviceStatus = document.querySelector('#device-status');
const NOT_SENT = {
  refused: 'Not sent',
  network: 'Not sent: network error',
};
const POWER_STATUS = {
  poweroff: 'Shutting down...',
  reboot: 'Restarting...',
};

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
 * Open a power button's confirm dialog instead of the confirm page.
 * @param {SubmitEvent} event - The power form's submit.
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

for (const form of document.querySelectorAll('form[data-power]')) {
  form.addEventListener('submit', askFirst);
}
for (const dialog of document.querySelectorAll('dialog[data-power]')) {
  dialog.addEventListener('close', confirmPower);
}
