// The file list, read-only preview and saving shared by the container and the data-message windows.
window.zfoFiles = (() => {
  const style = document.createElement('style');
  style.textContent = `
  .zfo-stage.zfo-file-stage { overflow: hidden; }
  .zfo-viewer { position: absolute; inset: 0; overflow: auto; }
  .zfo-viewer .pdfViewer { --page-margin: 24px auto; --page-border: 0; padding-bottom: 24px; }
  .zfo-viewer .pdfViewer .page { box-shadow: 0 1px 3px rgba(0, 0, 0, .12), 0 8px 28px rgba(0, 0, 0, .10); }
  .zfo-file-card { margin: 14vh auto 0; width: min(420px, calc(100% - 48px)); box-sizing: border-box; padding: 28px 24px;
    background: #fff; border: 1px solid var(--line); border-radius: 14px; text-align: center; }
  .zfo-file-card > svg { width: 48px; height: 48px; color: var(--accent); }
  .zfo-file-card h1 { font-size: 16px; font-weight: 600; margin: 12px 0 6px; overflow-wrap: anywhere; }
  .zfo-file-card p { margin: 0 0 6px; color: var(--muted); }
  .zfo-file-card .zfo-btn { margin: 16px auto 0; display: inline-flex; }
  .zfo-files { margin: 0 12px 8px; background: #fff; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
  .zfo-files button { display: flex; align-items: center; gap: 8px; width: 100%; padding: 8px 10px; border: 0;
    border-top: 1px solid var(--line); background: none; text-align: left; font: inherit; color: inherit; cursor: pointer; }
  .zfo-files button:first-child { border-top: 0; }
  .zfo-files button:hover { background: #f2f4f8; }
  .zfo-files button[aria-current="true"] { background: #e8f0fc; }
  .zfo-files .name { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .zfo-files .name span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .zfo-files small { color: var(--muted); font-size: 11px; }
  .zfo-chip { flex: none; padding: 1px 8px; border-radius: 999px; font-size: 11px; white-space: nowrap; }
  .zfo-chip.signed { background: #e7f5ea; color: var(--ok); }
  .zfo-chip.changed, .zfo-chip.invalid { background: #fde8e7; color: var(--bad); }
  .zfo-chip.unsigned { background: #fff4e0; color: #7a4a00; }
  .zfo-chip.unverified { background: #eef0f3; color: var(--muted); }
  .zfo-chip.main { background: #e8f0fc; color: var(--accent); }`;
  document.head.append(style);

  const TOOLS = `<div class="zfo-group">
      <button class="zfo-btn primary" data-file-action="save-file" title="Uložit vybraný soubor">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M5 3h11l3 3v15H5z"/><path d="M8 3v5h7V3M8 21v-7h8v7"/></svg>Uložit soubor…
      </button>
      <button class="zfo-btn" data-file-action="save-all-files" hidden title="Uložit všechny soubory do zvolené složky">Uložit všechny…</button>
      <button class="zfo-btn" data-file-action="open-copy" hidden title="Uložit kopii souboru a otevřít ji v novém okně">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1H3z"/><path d="M3 10h18l-2 9H5z"/></svg>Otevřít v Blanketě
      </button>
    </div>
    <div class="zfo-group">
      <button class="zfo-btn icon" data-file-action="zoom-out" data-pdf-only title="Zmenšit (⌘−)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7"/><path d="M8 11h6M16.5 16.5 21 21"/></svg></button>
      <span class="zfo-zoom">100&nbsp;%</span>
      <button class="zfo-btn icon" data-file-action="zoom-in" data-pdf-only title="Zvětšit (⌘+)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7"/><path d="M8 11h6M11 8v6M16.5 16.5 21 21"/></svg></button>
      <button class="zfo-btn icon" data-file-action="zoom-fit" data-pdf-only title="Přizpůsobit šířce okna">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="5" y="3" width="14" height="18" rx="1.5"/><path d="M2 12h4M18 12h4M4 10l-2 2 2 2M20 10l2 2-2 2"/></svg>
      </button>
    </div>`;
  const STAGE = `<div class="zfo-viewer" hidden><div class="pdfViewer"></div></div>
    <div class="zfo-file-card" hidden>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4"/></svg>
      <h1 data-card-name></h1>
      <p data-card-size></p>
      <p data-card-note hidden>Náhled souboru se nepodařilo zobrazit.</p>
      <span class="zfo-chip" data-card-chip></span><br>
      <button class="zfo-btn primary" data-file-action="save-file">Uložit soubor…</button>
    </div>
    <div class="zfo-empty-state" data-no-files hidden></div>`;

  const formatSize = bytes => bytes < 1024 ? `${bytes} B`
    : bytes < 1024 * 1024 ? `${(bytes / 1024).toLocaleString('cs-CZ', {maximumFractionDigits: 1})} kB`
    : `${(bytes / 1024 / 1024).toLocaleString('cs-CZ', {maximumFractionDigits: 1})} MB`;
  // The hosts drop the whole request for a name with a path, a colon or NUL: the file is saved under its last segment.
  const plainName = name => {
    const plain = name.split(/[\\/]/).pop().replace(/[:\x00-\x1f\x7f]/g, '_');
    return ['', '.', '..'].includes(plain) ? 'soubor' : plain;
  };
  const opensInBlanketa = name => /\.(pdf|zfo)$/i.test(name);
  const fragment = html => {
    const template = document.createElement('template');
    template.innerHTML = html;
    return template.content;
  };
  const chip = (element, file) => {
    element.hidden = !(file.state || file.main);
    element.className = 'zfo-chip ' + (file.state || 'main');
    element.textContent = file.state ? file.stateLabel : file.main ? 'hlavní dokument' : '';
    return element;
  };

  // files: [{name, size, url, pdf, main?, state?, stateLabel?}]
  return ({files, list, stage, toolbar, status, initial = 0, emptyText, failedText}) => {
    const native = window.zfoNative;
    const tools = fragment(TOOLS);
    const toolButton = name => tools.querySelector(`[data-file-action="${name}"]`);
    const pdfOnly = [...tools.querySelectorAll('[data-pdf-only]')];
    const zoomLabel = tools.querySelector('.zfo-zoom');
    const openCopy = toolButton('open-copy');
    const saveButtons = [toolButton('save-file')];
    openCopy.hidden = !native;
    toolButton('save-all-files').hidden = !native || files.length < 2;
    stage.classList.add('zfo-file-stage');
    stage.append(fragment(STAGE));
    saveButtons.push(stage.querySelector('[data-file-action="save-file"]'));
    const viewerContainer = stage.querySelector('.zfo-viewer');
    const card = stage.querySelector('.zfo-file-card');

    const rows = files.map((file, index) => {
      const button = document.createElement('button');
      const name = document.createElement('span');
      name.className = 'name';
      name.title = file.name;
      name.append(Object.assign(document.createElement('span'), {textContent: file.name}),
                  Object.assign(document.createElement('small'), {textContent: formatSize(file.size)}));
      button.append(name, chip(document.createElement('span'), file));
      button.addEventListener('click', () => select(index));
      return button;
    });
    list.append(...rows);
    list.hidden = !files.length;

    let viewer = null, linkService = null, pdfjsLib = null, loadingTask = null, selected = -1, autoFit = true;
    const viewerReady = files.some(file => file.pdf) ? (async () => {
      pdfjsLib = await import('/pdfjs/build/pdf.min.mjs');
      // pdf_viewer.mjs picks up the library from the global it expects.
      globalThis.pdfjsLib = pdfjsLib;
      pdfjsLib.GlobalWorkerOptions.workerSrc = '/pdfjs/build/pdf.worker.min.mjs';
      const {PDFViewer, EventBus, PDFLinkService} = await import('/pdfjs/web/pdf_viewer.mjs');
      const eventBus = new EventBus();
      linkService = new PDFLinkService({eventBus});
      viewer = new PDFViewer({
        container: viewerContainer, viewer: viewerContainer.firstElementChild, eventBus, linkService,
        annotationMode: pdfjsLib.AnnotationMode.ENABLE, textLayerMode: 1,
      });
      linkService.setViewer(viewer);
      eventBus.on('pagesinit', () => { if (autoFit) viewer.currentScaleValue = 'page-width'; });
      eventBus.on('scalechanging', event => { zoomLabel.textContent = Math.round(event.scale * 100) + ' %'; });
      new ResizeObserver(() => { if (autoFit && viewer.pdfDocument) viewer.currentScaleValue = 'page-width'; }).observe(viewerContainer);
    })() : Promise.reject(new Error('no PDF'));
    viewerReady.catch(() => {});

    const closePdf = () => {
      const task = loadingTask;
      loadingTask = null;
      if (!task) return;
      viewer.setDocument(null);
      linkService.setDocument(null);
      task.destroy();
    };
    const showCard = (index, previewFailed = false) => {
      const file = files[index];
      viewerContainer.hidden = true;
      card.querySelector('[data-card-name]').textContent = file.name;
      card.querySelector('[data-card-size]').textContent = formatSize(file.size);
      card.querySelector('[data-card-note]').hidden = !previewFailed;
      chip(card.querySelector('[data-card-chip]'), file);
      card.hidden = false;
    };
    const showPdf = async index => {
      card.hidden = true;
      viewerContainer.hidden = false;
      let task = null;
      try {
        await viewerReady;
        if (selected !== index) return;
        task = pdfjsLib.getDocument({
          url: files[index].url, standardFontDataUrl: '/pdfjs/standard_fonts/', cMapUrl: '/pdfjs/cmaps/', cMapPacked: true,
        });
        loadingTask = task;
        const pdf = await task.promise;
        if (loadingTask !== task) return;
        viewer.setDocument(pdf);
        linkService.setDocument(pdf);
      } catch {
        if (selected === index && loadingTask === task) showCard(index, true);
      }
    };
    const select = index => {
      if (index === selected) return;
      closePdf();
      selected = index;
      rows.forEach((row, position) => row.setAttribute('aria-current', String(position === index)));
      const file = files[index];
      for (const button of pdfOnly) button.disabled = !file.pdf;
      openCopy.disabled = !opensInBlanketa(file.name);
      if (file.pdf) showPdf(index);
      else showCard(index);
    };

    const save = (chosen, folder, open = false) => {
      if (!chosen.length) return;
      if (native) {
        native.postMessage({action: 'saveAttachment', files: chosen.map(file => ({url: file.url, name: plainName(file.name)})), folder, open});
        return;
      }
      // Detached links: bridge.js stops clicks on links inside the page from navigating.
      for (const file of chosen) Object.assign(document.createElement('a'), {href: file.url, download: plainName(file.name)}).click();
    };
    window.zfoAttachmentSaved = path => {
      status.replaceChildren(Object.assign(document.createElement('span'), {textContent: 'Uloženo: '}),
                             Object.assign(document.createElement('b'), {textContent: path.split(/[\\/]/).pop()}));
      if (!native) return;
      const reveal = Object.assign(document.createElement('a'), {textContent: window.zfoIsMac ? ' Zobrazit ve Finderu' : ' Zobrazit ve složce'});
      reveal.addEventListener('click', () => native.postMessage({action: 'reveal', path}));
      status.append(reveal);
    };
    window.zfoAttachmentFailed = reason => { status.textContent = `${failedText}: ${reason}`; };

    const zoomTo = scale => { autoFit = false; viewer.currentScale = Math.min(4, Math.max(.25, scale)); };
    const actions = {
      'save-file': () => save(files[selected] ? [files[selected]] : [], false),
      'save-all-files': () => { if (native && files.length > 1) save(files, true); },
      'open-copy': () => { if (native && opensInBlanketa(files[selected]?.name ?? '')) save([files[selected]], false, true); },
      'zoom-in': () => { if (viewer?.pdfDocument) zoomTo(viewer.currentScale + .1); },
      'zoom-out': () => { if (viewer?.pdfDocument) zoomTo(viewer.currentScale - .1); },
      'zoom-fit': () => { autoFit = true; if (viewer?.pdfDocument) viewer.currentScaleValue = 'page-width'; },
    };
    for (const button of [...tools.querySelectorAll('[data-file-action]'), ...stage.querySelectorAll('[data-file-action]')]) {
      button.addEventListener('click', () => actions[button.dataset.fileAction]());
    }
    document.addEventListener('keydown', event => {
      if (!window.zfoShortcut(event)) return;
      const shortcut = event.key === '+' || event.key === '=' ? 'zoom-in' : event.key === '-' ? 'zoom-out' : null;
      if (!shortcut || native?.platform === 'mac') return;
      event.preventDefault();
      actions[shortcut]();
    });
    toolbar.replaceWith(tools);

    if (files.length) select(files[initial] ? initial : 0);
    else {
      const empty = stage.querySelector('[data-no-files]');
      empty.textContent = emptyText;
      empty.hidden = false;
      for (const button of [...saveButtons, openCopy, ...pdfOnly]) button.disabled = true;
    }
    return {actions};
  };
})();
