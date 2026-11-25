/** Confirm the Device page's power and app switch in a dialog first. */
const deviceStatus = document.querySelector('#device-status');
const switchStatus = document.querySelector('#switch-status');
const switchForm = document.querySelector('form[data-switch]');
const NOT_SENT = {
  refused: 'Not sent',
  network: 'Not sent: network error',
};
const POWER_STATUS = {
  poweroff: 'Shutting down...',
  reboot: 'Restarting...',
};
const POLL_MS = 3000;
const GIVE_UP_MS = 120000;
const NOT_SWITCHED = {
  refused: 'Not switched',
  network: NOT_SENT.network,
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
 * Find the app the board shows in the Device page's HTML.
 * @param {string} page - The page text.
 * @returns {string | null} The key of the app marked showing.
 */
function shownApp(page) {
  const found = /value="([^"]+)"[^>]* disabled/.exec(page);
  return found ? found[1] : null;
}

/**
 * Fetch the Device page past the browser's HTTP cache.
 * @returns {Promise<string | null>} The app showing, or null on a miss.
 */
async function fetchShown() {
  try {
    const reply = await fetch('/device', { cache: 'no-store' });
    return shownApp(await reply.text());
  } catch {
    return null;
  }
}

/**
 * Poll the Device page until the app showing changes, then reload.
 * @param {string | undefined} before - The app showing now.
 * @returns {void}
 */
function followSwitch(before) {
  let waited = 0;
  const timer = setInterval(async () => {
    waited += POLL_MS;
    const now = await fetchShown();
    if (now && now !== before) location.reload();
    if ((now && now !== before) || waited >= GIVE_UP_MS) {
      clearInterval(timer);
    }
  }, POLL_MS);
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

/**
 * Find the app chosen in the switch form.
 * @returns {HTMLInputElement | null} The checked radio, or null.
 */
function chosenApp() {
  return switchForm.querySelector('input[name="app"]:checked');
}

/**
 * Find the switch form's radio for an app.
 * @param {string} key - The app.
 * @returns {HTMLInputElement | null} The radio, or null.
 */
function appRadio(key) {
  return switchForm.querySelector(`input[name="app"][value="${key}"]`);
}

/**
 * Open the chosen app's confirm dialog instead of the confirm page.
 * @param {SubmitEvent} event - The switch form's submit.
 * @returns {void}
 */
function askSwitch(event) {
  event.preventDefault();
  const key = chosenApp().value;
  const dialog = document.querySelector(`#switch-${key}-dialog`);
  dialog.returnValue = '';
  dialog.showModal();
}

/**
 * Send the switch when its dialog closes confirmed.
 * @param {Event} event - The dialog's close.
 * @returns {Promise<void>}
 */
async function confirmSwitch(event) {
  const dialog = event.currentTarget;
  if (dialog.returnValue !== 'confirm') return;
  const key = dialog.dataset.switch;
  const before = switchForm
    .querySelector('input[name="app"]:disabled')?.value;
  const outcome = await postJson('/switch', { app: key });
  if (outcome === 'ok') followSwitch(before);
  switchStatus.textContent = outcome === 'ok'
    ? `Switching to ${appRadio(key).dataset.name}...`
    : NOT_SWITCHED[outcome];
}

/**
 * Name the chosen app on the switch button.
 * @returns {void}
 */
function nameButton() {
  const button = switchForm.querySelector('button[type="submit"]');
  const chosen = chosenApp();
  if (!button || !chosen) return;
  button.textContent = `Switch to ${chosen.dataset.name}`;
}

/**
 * Ask in a dialog before switching, and name the chosen app.
 * @param {HTMLFormElement} form - The switch form.
 * @returns {void}
 */
function wireSwitch(form) {
  form.addEventListener('submit', askSwitch);
  form.addEventListener('change', nameButton);
  for (const dialog of document.querySelectorAll('dialog[data-switch]')) {
    dialog.addEventListener('close', confirmSwitch);
  }
  nameButton();
}

if (switchForm) wireSwitch(switchForm);
