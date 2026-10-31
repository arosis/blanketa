window.zfoNative = (() => {
  const mac = window.webkit?.messageHandlers?.app;
  if (mac) return {platform: 'mac', printsNatively: true, postMessage: message => mac.postMessage(message)};
  if (!window.ZFO_DESKTOP) return null;
  const ready = new Promise(resolve => window.pywebview?.api
    ? resolve() : window.addEventListener('pywebviewready', resolve, {once: true}));
  return {platform: 'desktop', printsNatively: false, postMessage: message => ready.then(() => window.pywebview.api.message(message))};
})();

window.zfoIsMac = /Mac/.test(navigator.platform);
window.zfoShortcut = event => (window.zfoIsMac ? event.metaKey : event.ctrlKey);
if (!window.zfoIsMac) {
  const relabel = text => text.replace(/⇧⌘/g, 'Ctrl+Shift+').replace(/⌥⌘/g, 'Ctrl+Alt+').replace(/⌘/g, 'Ctrl+')
    .replace(/⇧/g, 'Shift+').replace(/⌫/g, 'Backspace');
  document.addEventListener('DOMContentLoaded', () => {
    for (const element of document.querySelectorAll('[title*="⌘"], [title*="⇧"]')) element.title = relabel(element.title);
    for (const element of document.querySelectorAll('kbd')) element.textContent = relabel(element.textContent);
  });
}

// Built detached, so form scripts never see it as a field.
window.zfoCertificates = sessionToken => {
  const element = document.createElement('div');
  element.className = 'zfo-cert-choice';
  element.innerHTML = `<select class="zfo-cert" title="Certifikát pro podpis"></select>
    <label data-cert-password hidden>Heslo k souboru s certifikátem
      <input type="password" class="zfo-cert" autocomplete="off"></label>
    <button type="button" class="zfo-link" data-cert-add>Přidat certifikát ze souboru (.pfx, .p12)…</button>`;
  const select = element.querySelector('select');
  const passwordRow = element.querySelector('[data-cert-password]');
  const password = passwordRow.querySelector('input');
  let identities = [];
  const updatePasswordRow = () => { passwordRow.hidden = !identities.find(identity => identity.id === select.value)?.needs_password; };
  const renderOptions = wanted => {
    select.innerHTML = identities.length ? '<option value="">Vyber certifikát pro podpis…</option>'
                                   : '<option value="">Žádný podpisový certifikát – přidejte ho ze souboru</option>';
    for (const identity of identities) {
      const option = document.createElement('option');
      option.value = identity.id;
      option.textContent = (identity.token ? '🔑 ' : '') + identity.subject + (identity.source === 'test' ? ' (TEST)' : '')
        + (identity.source === 'file' ? ' (soubor)' : '') + (identity.not_after ? ' – do ' + identity.not_after : '');
      select.append(option);
    }
    try { wanted ??= localStorage.getItem('zfo-cert'); } catch {}
    if (identities.some(identity => identity.id === wanted)) select.value = wanted;
    updatePasswordRow();
  };
  select.addEventListener('change', updatePasswordRow);
  fetch('/identities').then(response => response.json()).then(found => { identities = found; renderOptions(); });
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
      const response = await fetch('/add-certificate?s=' + sessionToken, {method: 'POST', body: JSON.stringify({data: btoa(binary), password: secret})});
      if (!response.ok) { alert(await response.text()); return; }
      const knownIds = new Set(identities.map(identity => identity.id));
      identities = await response.json();
      renderOptions(identities.find(identity => !knownIds.has(identity.id))?.id);
    });
    input.click();
  });
  return {
    element, select,
    password: () => (passwordRow.hidden ? null : password.value),
    remember: () => { try { localStorage.setItem('zfo-cert', select.value); } catch {} },
  };
};

window.zfoOpenUrl = url => {
  if (!/^https?:\/\//.test(url)) return;
  if (window.zfoNative) window.zfoNative.postMessage({action: 'openUrl', url});
  else window.open(url, '_blank', 'noopener');
};

window.zfoSupport = (() => {
  const SUPPORT = {
    account: '7133557349/0800',
    iban: 'CZ43 0800 0000 0071 3355 7349',
    qrPaymentWithoutAmount: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 164 164"><path transform="scale(4)" stroke="#000" d="M2 2.5h7m1 0h1m1 0h1m2 0h2m4 0h2m1 0h2m1 0h4m1 0h7m-37 1h1m5 0h1m4 0h5m1 0h3m6 0h1m3 0h1m5 0h1m-37 1h1m1 0h3m1 0h1m2 0h1m1 0h4m1 0h3m2 0h4m1 0h3m1 0h1m1 0h3m1 0h1m-37 1h1m1 0h3m1 0h1m1 0h2m4 0h1m5 0h1m1 0h4m1 0h1m2 0h1m1 0h3m1 0h1m-37 1h1m1 0h3m1 0h1m1 0h3m5 0h2m2 0h1m1 0h3m2 0h1m2 0h1m1 0h3m1 0h1m-37 1h1m5 0h1m1 0h2m1 0h1m1 0h1m1 0h3m1 0h1m2 0h1m1 0h1m5 0h1m5 0h1m-37 1h7m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h1m1 0h7m-29 1h5m1 0h3m2 0h5m3 0h1m-28 1h1m3 0h1m1 0h4m2 0h1m2 0h2m2 0h4m1 0h1m4 0h5m2 0h1m-37 1h4m5 0h3m2 0h1m3 0h2m2 0h2m1 0h3m1 0h1m2 0h3m1 0h1m-35 1h7m4 0h1m1 0h1m1 0h3m1 0h4m2 0h1m1 0h1m1 0h1m2 0h1m-35 1h1m4 0h1m2 0h1m1 0h1m1 0h2m1 0h3m1 0h4m5 0h1m3 0h1m1 0h2m-36 1h2m1 0h1m2 0h7m3 0h3m2 0h1m1 0h1m2 0h8m2 0h1m-35 1h1m2 0h1m1 0h1m5 0h2m1 0h1m3 0h1m1 0h4m1 0h1m2 0h1m1 0h2m2 0h1m-36 1h1m2 0h3m1 0h3m1 0h1m2 0h1m1 0h1m2 0h2m1 0h1m3 0h3m2 0h3m-35 1h1m1 0h4m1 0h2m1 0h1m1 0h1m3 0h3m1 0h2m1 0h1m2 0h1m1 0h1m1 0h1m1 0h5m-37 1h1m1 0h3m1 0h1m2 0h1m2 0h3m3 0h2m4 0h1m1 0h2m1 0h3m2 0h1m-33 1h1m2 0h1m4 0h2m1 0h2m1 0h2m1 0h2m2 0h1m2 0h4m2 0h1m2 0h2m-34 1h4m6 0h2m1 0h2m2 0h1m2 0h1m3 0h1m1 0h1m2 0h1m-33 1h2m6 0h2m1 0h2m4 0h2m2 0h1m2 0h1m2 0h1m2 0h1m3 0h1m1 0h1m-37 1h1m1 0h1m1 0h3m1 0h3m2 0h1m1 0h1m1 0h3m1 0h1m1 0h1m2 0h1m1 0h4m4 0h1m-34 1h1m5 0h5m2 0h3m1 0h1m1 0h4m2 0h1m1 0h3m1 0h1m-33 1h2m1 0h4m1 0h1m2 0h1m1 0h2m1 0h1m1 0h4m3 0h1m1 0h1m3 0h1m1 0h1m-34 1h1m2 0h1m3 0h1m3 0h2m1 0h1m3 0h4m4 0h2m3 0h2m1 0h1m-36 1h1m1 0h4m1 0h1m5 0h3m2 0h2m3 0h2m3 0h2m2 0h2m-35 1h2m1 0h1m1 0h1m1 0h2m2 0h4m2 0h3m3 0h1m1 0h4m3 0h2m1 0h2m-35 1h1m2 0h2m2 0h7m3 0h1m1 0h4m2 0h3m-25 1h1m2 0h1m3 0h6m1 0h3m1 0h1m6 0h1m2 0h2m-35 1h2m1 0h2m1 0h7m1 0h1m1 0h1m4 0h1m4 0h1m1 0h9m-29 1h2m1 0h1m1 0h3m2 0h1m1 0h6m2 0h1m3 0h1m3 0h1m-37 1h7m1 0h3m1 0h1m2 0h1m1 0h1m2 0h1m2 0h1m1 0h1m1 0h2m1 0h1m1 0h1m1 0h1m-35 1h1m5 0h1m2 0h1m2 0h2m2 0h4m2 0h2m4 0h1m3 0h5m-37 1h1m1 0h3m1 0h1m1 0h1m1 0h2m6 0h2m2 0h1m1 0h1m3 0h5m1 0h1m-35 1h1m1 0h3m1 0h1m3 0h3m1 0h1m1 0h1m2 0h2m2 0h1m1 0h2m1 0h1m1 0h1m3 0h1m1 0h1m-37 1h1m1 0h3m1 0h1m3 0h1m2 0h2m1 0h1m3 0h1m1 0h2m3 0h1m2 0h1m2 0h1m-34 1h1m5 0h1m2 0h1m1 0h1m1 0h2m2 0h2m2 0h1m1 0h2m7 0h1m1 0h2m-36 1h7m1 0h4m1 0h3m3 0h1m1 0h1m1 0h1m2 0h2m1 0h2m2 0h1m1 0h2"/></svg>',
    links: [
      {title: 'Přispět přes GitHub Sponsors', url: 'https://github.com/sponsors/arosis'},
      {title: 'Kafe přes Ko-fi', url: 'https://ko-fi.com/rivel_arosis'},
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
          .zfo-support-about { margin: 14px 0 0; font-size: 12px; color: #6e6e73; }
        </style>
        <h2>Podpořit Blanketu</h2>
        <p>Blanketa je zdarma a zdarma zůstane. Pokud vám ušetřila čas, můžete na její další vývoj přispět
          libovolnou částkou. Děkuji!</p>
        <div class="zfo-support-qr">${SUPPORT.qrPaymentWithoutAmount}
          <dl><dt>Číslo účtu</dt><dd data-account></dd><dt>IBAN</dt><dd data-iban></dd>
            <dt>QR platbu naskenujte v aplikaci banky, částku zvolíte sami.</dt></dl></div>
        <div class="zfo-support-links" data-links></div>
        <p class="zfo-support-about"><a href="https://blanketa.cz">blanketa.cz</a> · Vytvořil
          <a href="https://winkler-bejrovi.cz">Richard Winkler Bejr</a></p>
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

// Links in a form (fo:basic-link) or a PDF never navigate the app window: a web page there could reach the host.
// Web links open in the system browser, links within the page keep working, anything else (javascript:, file:) is
// dropped.
document.addEventListener('click', event => {
  const link = event.target.closest?.('a[href]');
  if (!link) return;
  const href = link.getAttribute('href');
  if (href.startsWith('#')) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  if (/^https?:\/\//i.test(link.href)) window.zfoOpenUrl(link.href);
}, true);

window.zfoRevocation = (() => {
  const statesByCertificate = new Map();
  const formatDate = iso => new Date(iso).toLocaleDateString('cs-CZ');
  const escapeHtml = text => String(text).replace(/[&<>"]/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[character]));
  const stateOf = signature => statesByCertificate.get(signature.certificate);
  return {
    ask(session, signatures, redraw) {
      const missing = [...new Set(signatures.map(signature => signature.certificate))].filter(id => id && !statesByCertificate.has(id));
      if (!missing.length) return;
      missing.forEach(id => statesByCertificate.set(id, {status: 'pending'}));
      redraw();
      const failed = {status: 'unknown', reason: 'Stav certifikátu se nepodařilo ověřit.'};
      fetch(`/revocation?s=${session}`, {method: 'POST', body: JSON.stringify(missing)})
        .then(response => response.ok ? response.json() : {}).catch(() => ({}))
        .then(answer => { missing.forEach(id => statesByCertificate.set(id, answer[id] || failed)); redraw(); });
    },
    // A signature made after the certificate was revoked is not valid. The signing time comes from the signer,
    // so an earlier time is no proof either: only the first case is a broken signature, the second is a warning.
    // A time stamp (signature.proof) does prove the time. One inside the signature was made with it; a document stamp can
    // come years later, so one after the revocation says nothing about when the signature was made.
    provenBeforeRevocation(signature) {
      const state = stateOf(signature);
      return state?.status === 'revoked' && !!signature.proof && new Date(signature.proof.at) < new Date(state.revoked_at);
    },
    revoked(signature) { return stateOf(signature)?.status === 'revoked' && !this.provenBeforeRevocation(signature); },
    revokedBeforeSigning(signature) {
      const state = stateOf(signature);
      if (state?.status !== 'revoked' || this.provenBeforeRevocation(signature)) return false;
      if (signature.proof?.kind === 'signature') return true;
      return !!signature.signed_at && new Date(state.revoked_at) <= new Date(signature.signed_at);
    },
    state(signature) {
      const state = stateOf(signature), what = state?.suspended ? 'pozastaven' : 'zrušen';
      if (state?.status !== 'revoked') return '';
      if (this.provenBeforeRevocation(signature)) return `Certifikát byl ${what} ${formatDate(state.revoked_at)} až po podpisu, čas potvrzuje časové razítko`;
      if (signature.proof?.kind === 'signature') return `Certifikát byl ${what} ${formatDate(state.revoked_at)}, před časem z časového razítka`;
      if (signature.proof && !this.revokedBeforeSigning(signature)) return `Certifikát byl ${what} ${formatDate(state.revoked_at)}; časové razítko dokumentu je pozdější a čas podpisu nepotvrzuje`;
      if (!signature.signed_at) return `Certifikát byl ${what} ${formatDate(state.revoked_at)}; čas podpisu není uvedený`;
      return this.revokedBeforeSigning(signature)
        ? `Certifikát byl ${what} ${formatDate(state.revoked_at)}, před uvedeným časem podpisu`
        : `Certifikát byl ${what} ${formatDate(state.revoked_at)}; čas podpisu uvádí podepisující a není potvrzený`;
    },
    line(signature) {
      const state = stateOf(signature);
      if (!state) return '';
      const text = state.status === 'pending' ? 'Ověřuje se, zda certifikát nebyl zrušen…'
        : state.status === 'good' ? 'Certifikát není zrušený.'
        : state.status === 'unknown' ? `Zda je certifikát zrušený, se nepodařilo ověřit. ${escapeHtml(state.reason || '')}` : '';
      return text ? `<div class="meta">${text}</div>` : '';
    },
  };
})();
