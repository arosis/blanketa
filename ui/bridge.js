// Link between the page and the desktop app around it, shared by the ZFO and PDF pages:
//  - macOS app: WKWebView message handler "app" (macapp/main.swift); it prints and has native menus;
//  - Windows/Linux app: pywebview, window.pywebview.api.message (desktop.py); shortcuts are handled here;
//  - plain browser: null.
window.zfoNative = (() => {
  const mac = window.webkit?.messageHandlers?.app;
  if (mac) return {platform: 'mac', printsNatively: true, postMessage: m => mac.postMessage(m)};
  if (!window.ZFO_DESKTOP) return null;
  const ready = new Promise(resolve => window.pywebview?.api
    ? resolve() : window.addEventListener('pywebviewready', resolve, {once: true}));
  return {platform: 'desktop', printsNatively: false, postMessage: m => ready.then(() => window.pywebview.api.message(m))};
})();

// Shortcut modifier and labels: ⌘ on a Mac, Ctrl elsewhere.
window.zfoIsMac = /Mac/.test(navigator.platform);
window.zfoShortcut = e => (window.zfoIsMac ? e.metaKey : e.ctrlKey);
if (!window.zfoIsMac) {
  const relabel = text => text.replace(/⇧⌘/g, 'Ctrl+Shift+').replace(/⌥⌘/g, 'Ctrl+Alt+').replace(/⌘/g, 'Ctrl+')
    .replace(/⇧/g, 'Shift+').replace(/⌫/g, 'Backspace');
  document.addEventListener('DOMContentLoaded', () => {
    for (const el of document.querySelectorAll('[title*="⌘"], [title*="⇧"]')) el.title = relabel(el.title);
    for (const el of document.querySelectorAll('kbd')) el.textContent = relabel(el.textContent);
  });
}

// Certificate choice for the signing dialog, shared by both pages. Builds (detached, so form scripts never
// see it as a field) a select filled from /identities, a password field shown for certificate files, and
// "Přidat certifikát ze souboru…" for .pfx/.p12 files. Returns {element, select, password(), remember()}.
window.zfoCertificates = () => {
  const element = document.createElement('div');
  element.className = 'zfo-cert-choice';
  element.innerHTML = `<select class="zfo-cert" title="Certifikát pro podpis"></select>
    <label data-cert-password hidden>Heslo k souboru s certifikátem
      <input type="password" class="zfo-cert" autocomplete="off"></label>
    <button type="button" class="zfo-link" data-cert-add>Přidat certifikát ze souboru (.pfx, .p12)…</button>`;
  const select = element.querySelector('select');
  const passwordRow = element.querySelector('[data-cert-password]');
  const password = passwordRow.querySelector('input');
  let list = [];
  const update = () => { passwordRow.hidden = !list.find(c => c.id === select.value)?.needs_password; };
  const render = wanted => {
    select.innerHTML = list.length ? '<option value="">Vyber certifikát pro podpis…</option>'
                                   : '<option value="">Žádný podpisový certifikát – přidejte ho ze souboru</option>';
    for (const c of list) {
      const option = document.createElement('option');
      option.value = c.id;
      option.textContent = (c.token ? '🔑 ' : '') + c.subject + (c.source === 'test' ? ' (TEST)' : '')
        + (c.source === 'file' ? ' (soubor)' : '') + (c.not_after ? ' – do ' + c.not_after : '');
      select.append(option);
    }
    try { wanted ??= localStorage.getItem('zfo-cert'); } catch {}
    if (list.some(c => c.id === wanted)) select.value = wanted;
    update();
  };
  select.addEventListener('change', update);
  fetch('/identities').then(r => r.json()).then(found => { list = found; render(); });
  element.querySelector('[data-cert-add]').addEventListener('click', () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.pfx,.p12,application/x-pkcs12';
    input.addEventListener('change', async () => {
      const file = input.files[0];
      if (!file) return;
      const secret = prompt(`Heslo k souboru ${file.name}`);
      if (secret === null) return;
      let binary = '';
      for (const byte of new Uint8Array(await file.arrayBuffer())) binary += String.fromCharCode(byte);
      const res = await fetch('/add-certificate', {method: 'POST', body: JSON.stringify({data: btoa(binary), password: secret})});
      if (!res.ok) { alert(await res.text()); return; }
      const known = new Set(list.map(c => c.id));
      list = await res.json();
      render(list.find(c => !known.has(c.id))?.id);
    });
    input.click();
  });
  return {
    element, select,
    password: () => (passwordRow.hidden ? null : password.value),
    remember: () => { try { localStorage.setItem('zfo-cert', select.value); } catch {} },
  };
};
