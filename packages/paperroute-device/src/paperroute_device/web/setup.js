/** Let the setup page show the Wi-Fi password while it is typed. */
const showButton = document.querySelector('#show-password');
const password = document.querySelector('#password');

/**
 * Show or hide the password, and say which on the button.
 * @returns {void}
 */
function togglePassword() {
  const showing = password.type === 'password';
  password.type = showing ? 'text' : 'password';
  showButton.setAttribute('aria-pressed', String(showing));
  showButton.textContent = showing ? 'Hide password' : 'Show password';
}

if (showButton && password) {
  showButton.setAttribute('aria-pressed', 'false');
  showButton.addEventListener('click', togglePassword);
  showButton.hidden = false;
}
