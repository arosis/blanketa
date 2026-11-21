window.zfoNative = (() => {
  const mac = window.webkit?.messageHandlers?.app;
  if (mac) return {platform: 'mac', printsNatively: true, postMessage: message => mac.postMessage(message)};
  if (!window.ZFO_DESKTOP) return null;
  const ready = new Promise(resolve => window.pywebview?.api
    ? resolve() : window.addEventListener('pywebviewready', resolve, {once: true}));
  return {
    platform: 'desktop', printsNatively: false,
    get printsPdfInPage() { return window.pywebview?.platform === 'edgechromium'; },
    postMessage: message => ready.then(() => window.pywebview.api.message(message)),
  };
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

window.zfoAbout = (() => {
  const LINKS = [
    {title: 'Web blanketa.cz', url: 'https://blanketa.cz'},
    {title: 'Zdrojový kód', url: 'https://github.com/arosis/blanketa'},
    {title: 'Navrhnout zlepšení', url: 'https://github.com/arosis/blanketa/issues'},
  ];
  let dialog;
  return () => {
    if (!dialog) {
      const version = JSON.parse(document.querySelector('script[data-zfo-page]')?.textContent || '{}').app_version;
      dialog = document.createElement('dialog');
      dialog.className = 'zfo-dialog zfo-about';
      dialog.innerHTML = `<style>
          .zfo-about { width: 400px; font: 13px/1.45 -apple-system, "Segoe UI", system-ui, sans-serif; }
          .zfo-about h2 { margin-bottom: 0; }
          .zfo-about-version { margin: 0 0 10px; color: #6e6e73; }
          .zfo-about dl { margin: 10px 0; display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; }
          .zfo-about dt { color: #6e6e73; }
          .zfo-about dd { margin: 0; }
          .zfo-about ul { margin: 6px 0 12px; padding-left: 18px; }
          .zfo-about a, .zfo-about-link { color: var(--accent, #0a64d6); cursor: pointer; }
          .zfo-about-link { padding: 0; border: 0; background: none; font: inherit; text-decoration: underline; }
        </style>
        <h2>Blanketa</h2>
        <p class="zfo-about-version" data-version></p>
        <p>Vyplňování, podepisování a ověřování úředních formulářů 602XML (.zfo) a PDF na macOS, Windows a Linuxu.</p>
        <dl><dt>Autor</dt><dd><a href="https://winkler-bejrovi.cz">Richard Winkler Bejr</a></dd>
          <dt>Licence</dt><dd>Svobodný software pod licencí
            <a href="https://www.gnu.org/licenses/gpl-3.0.html">GNU GPL 3.0</a></dd></dl>
        <ul data-links><li><button type="button" class="zfo-about-link" data-report>Nahlásit chybu…</button></li></ul>
        <p>Blanketa je zdarma a žije z dobrovolných příspěvků.</p>
        <div class="zfo-dialog-buttons">
          <button type="button" class="zfo-btn" data-support>Podpořit Blanketu…</button>
          <button type="button" class="zfo-btn primary" data-close>Zavřít</button></div>`;
      dialog.querySelector('[data-version]').textContent = version ? `Verze ${version}` : '';
      for (const link of LINKS) {
        const item = document.createElement('li');
        const anchor = document.createElement('a');
        anchor.href = link.url;
        anchor.textContent = link.title;
        item.append(anchor);
        dialog.querySelector('[data-links]').append(item);
      }
      dialog.querySelector('[data-support]').addEventListener('click', () => { dialog.close(); window.zfoSupport(); });
      dialog.querySelector('[data-report]').addEventListener('click', () => { dialog.close(); window.zfoReportError(); });
      dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
      document.body.append(dialog);
    }
    dialog.showModal();
  };
})();

window.zfoReportError = (() => {
  const ADDRESS = 'podpora@blanketa.cz';
  const MAX_ERRORS = 5;
  const MAX_ENCODED_BODY = 1800;
  const SHORTENED_NOTE = '\n…\n[Text se do odkazu pro poštu celý nevešel. Celý je ve schránce – vložte ho sem místo tohoto.]';
  const pageErrors = [];
  const scriptName = url => { try { return new URL(url).pathname.split('/').pop(); } catch { return ''; } };
  const withoutQuotedValues = message => String(message)
    .replace(/"[^"]*"|'[^']*'|„[^“]*“|`[^`]*`/g, '…').replace(/\d{4,}/g, '…').slice(0, 200);
  const record = (message, file, line) => {
    const place = file ? ` (${scriptName(file)}:${line || '?'})` : '';
    pageErrors.push(withoutQuotedValues(message) + place);
    if (pageErrors.length > MAX_ERRORS) pageErrors.shift();
  };
  window.addEventListener('error', event => record(event.message, event.filename, event.lineno));
  window.addEventListener('unhandledrejection', event => record(event.reason?.message ?? event.reason));

  const pageData = () => JSON.parse(document.querySelector('script[data-zfo-page]')?.textContent || '{}');
  const template = (crash = '') => {
    const data = pageData();
    const errors = pageErrors.length ? pageErrors.map(error => `- ${error}`).join('\n') : '- žádné';
    return `Co se stalo a co jste dělali těsně předtím:\n\n\n`
      + `Kontakt pro doplňující dotaz (nepovinné):\n\n\n`
      + `— Technické údaje (můžete je upravit nebo smazat) —\n`
      + `Blanketa: ${data.app_version || '?'}\n`
      + `Systém: ${data.system || navigator.platform}\n`
      + `Dokument: ${data.document || 'neuvedený'}\n`
      + `Chyby na stránce:\n${errors}\n`
      + (crash ? `Záznam o chybě z minulé práce:\n${crash}\n` : '');
  };
  let crashOffersEnabled;
  const storeCrashOffers = async enabled => {
    crashOffersEnabled = enabled;
    try {
      const response = await fetch(`/crash-report?s=${pageData().session}`, {method: 'POST', body: JSON.stringify({enabled})});
      if (response.ok) crashOffersEnabled = (await response.json()).enabled;
    } catch {}
  };
  const fitIntoMailLink = text => {
    if (encodeURIComponent(text).length <= MAX_ENCODED_BODY) return text;
    let shortened = text;
    while (encodeURIComponent(shortened + SHORTENED_NOTE).length > MAX_ENCODED_BODY) shortened = shortened.slice(0, -20);
    return shortened + SHORTENED_NOTE;
  };
  const copy = async (text, textarea) => {
    try { await navigator.clipboard.writeText(text); } catch { textarea.select(); document.execCommand('copy'); }
  };

  let dialog;
  const open = crash => {
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.className = 'zfo-dialog zfo-report';
      dialog.innerHTML = `<style>
          .zfo-report { width: 480px; font: 13px/1.45 -apple-system, "Segoe UI", system-ui, sans-serif; }
          .zfo-report textarea { box-sizing: border-box; width: 100%; height: 260px; font: 12px/1.4 ui-monospace, Menlo, Consolas, monospace; }
          .zfo-report-note { margin: 8px 0 0; font-size: 12px; color: #6e6e73; }
          .zfo-report-note b { user-select: text; -webkit-user-select: text; }
          .zfo-report label { display: block; margin-top: 8px; font-size: 12px; }
        </style>
        <h2>Nahlásit chybu</h2>
        <p>Popište, co se stalo. Hlášení odejde až z vaší pošty a neobsahuje vyplněné údaje, názvy souborů ani certifikáty.
          Před odesláním ho můžete libovolně upravit.</p>
        <textarea data-report-text spellcheck="false" aria-label="Text hlášení"></textarea>
        <p class="zfo-report-note">Hlášení čte jen autor Blankety. Pokud se pošta neotevře, zkopírujte text a pošlete ho
          na <b data-address></b>.</p>
        <label><input type="checkbox" data-crash-offers> Po chybě nabídnout hlášení při dalším otevření okna</label>
        <div class="zfo-dialog-buttons">
          <button type="button" class="zfo-btn" data-copy>Zkopírovat text</button>
          <button type="button" class="zfo-btn" data-close>Zrušit</button>
          <button type="button" class="zfo-btn primary" data-send>Otevřít v poště</button></div>`;
      dialog.querySelector('[data-address]').textContent = ADDRESS;
      const text = dialog.querySelector('[data-report-text]');
      const subject = () => `Hlášení chyby – Blanketa ${pageData().app_version || ''}`.trim();
      dialog.querySelector('[data-copy]').addEventListener('click', async event => {
        await copy(text.value, text);
        event.target.textContent = 'Zkopírováno';
      });
      dialog.querySelector('[data-send]').addEventListener('click', async () => {
        const body = fitIntoMailLink(text.value);
        if (body !== text.value) await copy(text.value, text);
        const message = {action: 'reportError', subject: subject(), body};
        if (window.zfoNative) window.zfoNative.postMessage(message);
        else window.location.href = `mailto:${ADDRESS}?subject=${encodeURIComponent(message.subject)}&body=${encodeURIComponent(message.body)}`;
        dialog.close();
      });
      dialog.querySelector('[data-crash-offers]').addEventListener('change', event => storeCrashOffers(event.target.checked));
      dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
      document.body.append(dialog);
    }
    const text = dialog.querySelector('[data-report-text]');
    text.value = template(crash);
    crashOffersEnabled ??= pageData().crash_reports !== false;
    dialog.querySelector('[data-crash-offers]').checked = crashOffersEnabled;
    dialog.querySelector('[data-copy]').textContent = 'Zkopírovat text';
    dialog.showModal();
    const answerLine = text.value.indexOf('\n') + 1;
    text.setSelectionRange(answerLine, answerLine);
  };
  const offerAfterCrash = () => {
    const crash = pageData().crash_report;
    if (!crash) return;
    const offer = document.createElement('dialog');
    offer.className = 'zfo-dialog zfo-crash-offer';
    offer.innerHTML = `<style>.zfo-crash-offer { width: 420px; font: 13px/1.45 -apple-system, "Segoe UI", system-ui, sans-serif; }</style>
      <h2>Blanketa minule narazila na chybu</h2>
      <p>Pomůžete ji opravit? Blanketa připraví hlášení, které před odesláním uvidíte celé a můžete ho upravit.
        Neobsahuje vyplněné údaje, názvy souborů ani certifikáty.</p>
      <div class="zfo-dialog-buttons">
        <button type="button" class="zfo-btn" data-never>Už nenabízet</button>
        <button type="button" class="zfo-btn" data-later>Teď ne</button>
        <button type="button" class="zfo-btn primary" data-report>Připravit hlášení…</button></div>`;
    offer.querySelector('[data-never]').addEventListener('click', () => { offer.close(); storeCrashOffers(false); });
    offer.querySelector('[data-later]').addEventListener('click', () => offer.close());
    offer.querySelector('[data-report]').addEventListener('click', () => { offer.close(); open(crash); });
    offer.addEventListener('close', () => offer.remove());
    document.body.append(offer);
    offer.showModal();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', offerAfterCrash);
  else offerAfterCrash();
  return Object.assign(() => open(), {record, template, fitIntoMailLink});
})();

window.zfoSupportReminder = async (sessionToken, status) => {
  const ask = action => fetch('/support-reminder?s=' + sessionToken, {method: 'POST', body: JSON.stringify({action})})
    .then(response => (response.ok ? response.json() : {show: false})).catch(() => ({show: false}));
  if (!(await ask('offer')).show) return;
  const reminder = document.createElement('span');
  reminder.className = 'zfo-support-reminder';
  reminder.innerHTML = ` · Blanketa je zdarma – <button type="button" class="zfo-link" data-support>podpořit vývoj</button>`
    + ` · <button type="button" class="zfo-link" data-hide>Už nezobrazovat</button>`;
  reminder.querySelector('[data-support]').addEventListener('click', () => window.zfoSupport());
  reminder.querySelector('[data-hide]').addEventListener('click', () => { reminder.remove(); ask('hide'); });
  status.append(reminder);
};

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
  const formatDateTime = iso => new Date(iso).toLocaleString('cs-CZ', {dateStyle: 'medium', timeStyle: 'short'});
  const escapeHtml = text => String(text).replace(/[&<>"]/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[character]));
  const isSettled = state => state?.status === 'good' || state?.status === 'revoked';
  // What the issuer says today counts first. Once the certificate has expired the issuer may not say anything, and then
  // the status the file carries from the time of signing (signature.status_in_file) is all there is.
  const stateOf = signature => {
    const asked = statesByCertificate.get(signature.certificate), kept = signature.status_in_file;
    return isSettled(asked) || !isSettled(kept) ? asked : kept;
  };
  const statementMadeBeforeSigning = (isList, issued) =>
    `Podle ${isList ? 'seznamu zrušených certifikátů' : 'odpovědi vydavatele'} z ${formatDateTime(issued)}, ${isList ? 'který v době podpisu platil' : 'která v době podpisu platila'}, certifikát zrušený nebyl. Stav je uložený v souboru a vznikl před podpisem.`;
  const keptStatement = state => {
    const isList = state.from_file.kind === 'crl';
    if (state.status === 'good' && state.from_file.before_signing) return statementMadeBeforeSigning(isList, state.from_file.issued_at);
    const source = `${isList ? 'seznam zrušených certifikátů' : 'odpověď vydavatele'} z ${formatDate(state.from_file.issued_at)}`;
    return `${state.status === 'good' ? `V době podpisu nebyl certifikát zrušený. ` : ''}Stav je uložený v souboru: ${source}.`;
  };
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
      const state = stateOf(signature), kept = signature.status_in_file;
      if (!state) return '';
      const text = state.from_file ? keptStatement(state)
        : state.status === 'pending' ? 'Ověřuje se, zda certifikát nebyl zrušen…'
        : state.status === 'good' ? 'Certifikát není zrušený.'
        : state.status === 'unknown' ? `Zda je certifikát zrušený, se nepodařilo ověřit. ${escapeHtml(state.reason || '')} ${escapeHtml(kept?.reason || '')}` : '';
      return text ? `<div class="meta">${text}</div>` : '';
    },
  };
})();
