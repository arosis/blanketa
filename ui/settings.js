const pageData = JSON.parse(document.querySelector('script[data-zfo-page]').textContent);
const SESSION = pageData.session;
const SAVE_FAILED = 'Nastavení se nepodařilo uložit.';
const TYPING_PAUSE_MS = 400;
const SWITCHES = {
  update_checks: document.querySelector('[data-update-checks]'),
  crash_offers: document.querySelector('[data-crash-offers]'),
  support_reminder: document.querySelector('[data-support-reminder]'),
};
const SERVICES = [...document.querySelectorAll('[name="time-stamp-service"]')];
const OWN_FIELDS = document.querySelector('[data-time-stamp-own]');
const URL_FIELD = document.querySelector('[data-time-stamp-url]');
const USER_FIELD = document.querySelector('[data-time-stamp-user]');
let stored = pageData.settings;
let typingTimer = null;

const showError = (key, text) => {
  const line = document.querySelector(`[data-error="${key}"]`);
  line.textContent = text;
  line.hidden = !text;
};
const chosenService = () => SERVICES.find(radio => radio.checked)?.value;
const showOwnFields = () => { OWN_FIELDS.hidden = chosenService() !== 'own'; };
const render = settings => {
  for (const [key, control] of Object.entries(SWITCHES)) control.checked = settings[key];
  for (const radio of SERVICES) radio.checked = radio.value === settings.time_stamp.service;
  URL_FIELD.value = settings.time_stamp.url;
  USER_FIELD.value = settings.time_stamp.user;
  showOwnFields();
};
const save = async (key, body, keepTyping = false) => {
  try {
    const response = await fetch(`/settings?s=${encodeURIComponent(SESSION)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await response.text());
    stored = await response.json();
    showError(key, '');
  } catch (error) {
    showError(key, error.message || SAVE_FAILED);
    if (!keepTyping) render(stored);
  }
};
const timeStampBody = () => ({time_stamp: {service: chosenService(), url: URL_FIELD.value.trim(), user: USER_FIELD.value.trim()}});
const saveTimeStamp = keepTyping => save('time_stamp', timeStampBody(), keepTyping);

for (const [key, control] of Object.entries(SWITCHES)) {
  control.addEventListener('change', () => save(key, {[key]: control.checked}));
}
for (const radio of SERVICES) {
  radio.addEventListener('change', () => {
    showOwnFields();
    if (chosenService() === 'own' && !URL_FIELD.value.trim()) return;
    saveTimeStamp(false);
  });
}
for (const field of [URL_FIELD, USER_FIELD]) {
  field.addEventListener('input', () => {
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => saveTimeStamp(true), TYPING_PAUSE_MS);
  });
}
render(stored);
