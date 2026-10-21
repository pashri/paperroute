/** Save each Settings change as it is made, without reloading. */
const autoRebuild = document.querySelector('#auto-rebuild');
const saveButton = document.querySelector('#timetable-save');
const statusLine = document.querySelector('#settings-status');

/**
 * Set the switch from the device's `/status`, leaving it on failure.
 * @returns {Promise<void>}
 */
async function loadSettings() {
  try {
    const reply = await fetch('/status');
    const settings = (await reply.json()).settings;
    if (settings) autoRebuild.checked = settings.auto_rebuild;
  } catch {
    // The server-drawn state stays.
  }
}

/**
 * Post the switch's state, undoing it and saying so when not saved.
 * @returns {Promise<void>}
 */
async function saveAutoRebuild() {
  const value = autoRebuild.checked ? 'on' : 'off';
  statusLine.textContent = '';
  try {
    const reply = await fetch('/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_rebuild: value }),
    });
    if (!reply.ok) throw new Error(`refused (${reply.status})`);
  } catch {
    autoRebuild.checked = !autoRebuild.checked;
    statusLine.textContent = 'Not saved: network error';
  }
}

saveButton.hidden = true;
autoRebuild.addEventListener('change', saveAutoRebuild);
loadSettings();
