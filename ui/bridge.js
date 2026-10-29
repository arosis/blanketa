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

// Opens a web page in the system browser; the app windows only ever show local forms.
window.zfoOpenUrl = url => {
  if (!/^https:\/\//.test(url)) return;
  if (window.zfoNative) window.zfoNative.postMessage({action: 'openUrl', url});
  else window.open(url, '_blank', 'noopener');
};

// "Podpořit Blanketu": the app is free; this shows how to send a voluntary contribution. The QR code is a Czech
// QR platba (SPD) for the account below without an amount, so the payer chooses it in their bank app.
window.zfoSupport = (() => {
  const SUPPORT = {
    account: '7133557349/0800',
    iban: 'CZ43 0800 0000 0071 3355 7349',
    qr: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 164 164"><path transform="scale(4)" stroke="#000" d="M2 2.5h7m1 0h1m1 0h1m2 0h2m4 0h2m1 0h2m1 0h4m1 0h7m-37 1h1m5 0h1m4 0h5m1 0h3m6 0h1m3 0h1m5 0h1m-37 1h1m1 0h3m1 0h1m2 0h1m1 0h4m1 0h3m2 0h4m1 0h3m1 0h1m1 0h3m1 0h1m-37 1h1m1 0h3m1 0h1m1 0h2m4 0h1m5 0h1m1 0h4m1 0h1m2 0h1m1 0h3m1 0h1m-37 1h1m1 0h3m1 0h1m1 0h3m5 0h2m2 0h1m1 0h3m2 0h1m2 0h1m1 0h3m1 0h1m-37 1h1m5 0h1m1 0h2m1 0h1m1 0h1m1 0h3m1 0h1m2 0h1m1 0h1m5 0h1m5 0h1m-37 1h7m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h7m-29 1h5m1 0h3m2 0h5m3 0h1m-28 1h1m3 0h1m1 0h4m2 0h1m2 0h2m2 0h4m1 0h1m4 0h5m2 0h1m-37 1h4m5 0h3m2 0h1m3 0h2m2 0h2m1 0h3m1 0h1m2 0h3m1 0h1m-35 1h7m4 0h1m1 0h1m1 0h3m1 0h4m2 0h1m1 0h1m1 0h1m2 0h1m-35 1h1m4 0h1m2 0h1m1 0h1m1 0h2m1 0h3m1 0h4m5 0h1m3 0h1m1 0h2m-36 1h2m1 0h1m2 0h7m3 0h3m2 0h1m1 0h1m2 0h8m2 0h1m-35 1h1m2 0h1m1 0h1m5 0h2m1 0h1m3 0h1m1 0h4m1 0h1m2 0h1m1 0h2m2 0h1m-36 1h1m2 0h3m1 0h3m1 0h1m2 0h1m1 0h1m2 0h2m1 0h1m3 0h3m2 0h3m-35 1h1m1 0h4m1 0h2m1 0h1m1 0h1m3 0h3m1 0h2m1 0h1m2 0h1m1 0h1m1 0h1m1 0h5m-37 1h1m1 0h3m1 0h1m2 0h1m2 0h3m3 0h2m4 0h1m1 0h2m1 0h3m2 0h1m-33 1h1m2 0h1m4 0h2m1 0h2m1 0h2m1 0h2m2 0h1m2 0h4m2 0h1m2 0h2m-34 1h4m6 0h2m1 0h2m2 0h1m2 0h1m3 0h1m1 0h1m2 0h1m-33 1h2m6 0h2m1 0h2m4 0h2m2 0h1m2 0h1m2 0h1m2 0h1m3 0h1m1 0h1m-37 1h1m1 0h1m1 0h3m1 0h3m2 0h1m1 0h1m1 0h3m1 0h1m1 0h1m2 0h1m1 0h4m4 0h1m-34 1h1m5 0h5m2 0h3m1 0h1m1 0h4m2 0h1m1 0h3m1 0h1m-33 1h2m1 0h4m1 0h1m2 0h1m1 0h2m1 0h1m1 0h4m3 0h1m1 0h1m3 0h1m1 0h1m-34 1h1m2 0h1m3 0h1m3 0h2m1 0h1m3 0h4m4 0h2m3 0h2m1 0h1m-36 1h1m1 0h4m1 0h1m5 0h3m2 0h2m3 0h2m3 0h2m2 0h2m-35 1h2m1 0h1m1 0h1m1 0h2m2 0h4m2 0h3m3 0h1m1 0h4m3 0h2m1 0h2m-35 1h1m2 0h2m2 0h7m3 0h1m1 0h4m2 0h3m-25 1h1m2 0h1m3 0h6m1 0h3m1 0h1m6 0h1m2 0h2m-35 1h2m1 0h2m1 0h7m1 0h1m1 0h1m4 0h1m4 0h1m1 0h9m-29 1h2m1 0h1m1 0h3m2 0h1m1 0h6m2 0h1m3 0h1m3 0h1m-37 1h7m1 0h3m1 0h1m2 0h1m1 0h1m2 0h1m2 0h1m1 0h1m1 0h2m1 0h1m1 0h1m1 0h1m-35 1h1m5 0h1m2 0h1m2 0h2m2 0h4m2 0h2m4 0h1m3 0h5m-37 1h1m1 0h3m1 0h1m1 0h1m1 0h2m6 0h2m2 0h1m1 0h1m3 0h5m1 0h1m-35 1h1m1 0h3m1 0h1m3 0h3m1 0h1m1 0h1m2 0h2m2 0h1m1 0h2m1 0h1m1 0h1m3 0h1m1 0h1m-37 1h1m1 0h3m1 0h1m3 0h1m2 0h2m1 0h1m3 0h1m1 0h2m3 0h1m2 0h1m2 0h1m-34 1h1m5 0h1m2 0h1m1 0h1m1 0h2m2 0h2m2 0h1m1 0h2m7 0h1m1 0h2m-36 1h7m1 0h4m1 0h3m3 0h1m1 0h1m1 0h1m2 0h2m1 0h2m2 0h1m1 0h2"/></svg>',
    links: [
      {title: 'Přispět přes GitHub Sponsors', url: 'https://github.com/sponsors/arosis'},
      // {title: 'Ko-fi', url: 'https://ko-fi.com/…'},
    ],
  };
  let dialog;
  return () => {
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.className = 'zfo-dialog zfo-support';
      dialog.innerHTML = `<style>
          .zfo-support { width: 400px; font: 13px/1.45 -apple-system, "Segoe UI", system-ui, sans-serif; }
          .zfo-support-qr { display: grid; grid-template-columns: 140px 1fr; gap: 16px; align-items: center; margin: 4px 0 6px; }
          .zfo-support-qr svg { width: 140px; height: 140px; display: block; }
          .zfo-support dl { margin: 0; display: grid; gap: 2px; }
          .zfo-support dt { color: #6e6e73; font-size: 12px; }
          .zfo-support dd { margin: 0 0 6px; font-weight: 600; user-select: text; -webkit-user-select: text; }
          .zfo-support-links { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
        </style>
        <h2>Podpořit Blanketu</h2>
        <p>Blanketa je zdarma a zdarma zůstane. Pokud vám ušetřila čas, můžete na její další vývoj přispět
          libovolnou částkou. Děkuji!</p>
        <div class="zfo-support-qr">${SUPPORT.qr}
          <dl><dt>Číslo účtu</dt><dd data-account></dd><dt>IBAN</dt><dd data-iban></dd>
            <dt>QR platbu naskenujte v aplikaci banky, částku zvolíte sami.</dt></dl></div>
        <div class="zfo-support-links" data-links></div>
        <div class="zfo-dialog-buttons"><button type="button" class="zfo-btn primary" data-close>Zavřít</button></div>`;
      dialog.querySelector('[data-account]').textContent = SUPPORT.account;
      dialog.querySelector('[data-iban]').textContent = SUPPORT.iban;
      const links = dialog.querySelector('[data-links]');
      for (const link of SUPPORT.links) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'zfo-btn';
        button.textContent = link.title;
        button.addEventListener('click', () => window.zfoOpenUrl(link.url));
        links.append(button);
      }
      links.hidden = !SUPPORT.links.length;
      dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
      document.body.append(dialog);
    }
    dialog.showModal();
  };
})();
