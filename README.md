# Blanketa

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/arosis/blanketa/badge)](https://scorecard.dev/viewer/?uri=github.com/arosis/blanketa)

**Web: [blanketa.cz](https://blanketa.cz)** · autor: [Richard Winkler Bejr](https://winkler-bejrovi.cz)

Aplikace pro macOS, Windows a Linux (Ubuntu, Debian, Mint …) na vyplňování a elektronické podepisování:
- **formulářů 602XML (`.zfo`)**, které jinak otevře jen Software602 Form Filler pro Windows (např. ČÚZK návrh na vklad, formuláře ČSSZ a krajů);
- **PDF formulářů (AcroForm)**.

Podepisuje se certifikátem:

| Systém | Odkud certifikát |
|---|---|
| macOS | klíčenka a tokeny (CryptoTokenKit) |
| Windows | úložiště certifikátů uživatele včetně čipových karet |
| všude | soubor `.pfx` / `.p12` přidaný v dialogu podpisu; heslo se zadává při podpisu a neukládá se |

Formuláře podepsané Blanketou otevře i Software602 Form Filler a podpis uzná.

## Podpora

Blanketa je zdarma a zdarma zůstane. Pokud vám ušetřila čas, můžete na její další vývoj přispět libovolnou částkou
převodem na účet **7133557349/0800** (IBAN CZ43 0800 0000 0071 3355 7349) nebo kartou přes
[GitHub Sponsors](https://github.com/sponsors/arosis) či [Ko-fi](https://ko-fi.com/rivel_arosis). QR platbu najdete v aplikaci pod
„Podpořit Blanketu“ (na Macu v menu Blanketa, jinde v menu Nápověda). Děkuji!

## Stažení a instalace

Hotové balíčky vytváří GitHub Actions (`.github/workflows/build.yml`) při každé značce `v*`, nebo ručně ze záložky Actions:

| Systém | Soubor | Instalace |
|---|---|---|
| macOS | `Blanketa-<verze>-macos.dmg` (nebo `.zip`) | přetáhnout do Aplikací; poprvé otevřít přes pravé tlačítko → Otevřít (není notarizovaná) |
| Windows 10/11 | `Blanketa-<verze>-setup.exe` | instalátor bez práv správce; nabídne otevírání `.zfo` v Blanketě. Přenosná verze: `…-windows-x64.zip` |
| Ubuntu, Debian, Mint | `blanketa_<verze>_amd64.deb` | `sudo apt install ./blanketa_<verze>_amd64.deb` |
| jiný Linux | `blanketa-<verze>-linux-amd64.tar.gz` | rozbalit a spustit `Blanketa/install.sh` (bez roota, do domovské složky) |

Windows potřebují Microsoft Edge WebView2 (Windows 10/11 ho mají; instalátor jinak nabídne stažení).

## Co umí

**ZFO**
- Zobrazení formuláře ve vzhledu Form Filleru:
  - opakované sekce;
  - přepínané části (multiswitch);
  - výpočty XPath;
  - nápověda k polím;
  - vložená písma a obrázky;
  - uživatelská tlačítka formuláře.
- Uložit bez podpisu a Podepsat a uložit.
- Další funkce:
  - tisk (i do PDF), tisk jen vyplněných hodnot;
  - přepočítat a zkontrolovat formulář;
  - najít;
  - obnovit výchozí hodnoty pole nebo celého formuláře;
  - import a export dat (XML).
- Panel podpisů: neporušenost každé vrstvy a důvěryhodnost certifikátu podle macOS.

**PDF**
- Vyplňování přes PDF.js:
  - texty, zaškrtávátka, přepínače, seznamy;
  - ukládá se jako přírůstková aktualizace, takže dřívější podpisy zůstanou platné.
- Podpis PAdES (pyHanko):
  - do připraveného podpisového pole s razítkem;
  - nebo na místo, které uživatel vyznačí tažením myši;
  - nebo neviditelně.
- Tisk vyplněného souboru v plné kvalitě.

Technický popis formátu ZFO: [docs/zfo-format.md](docs/zfo-format.md).

## Architektura

```
macOS:  build/Blanketa.app
        ├─ MacOS/Blanketa     Swift (macapp/main.swift): okna s WKWebView, menu, tisk, dialogy
        └─ Resources/
           ├─ zfo-server/     Python (app.py) zabalený PyInstallerem; jeden proces na okno, lokální HTTP
           └─ macsign         Swift (macsign/macsign.swift): identity, podpis a důvěra přes Security.framework

Windows, Linux:  dist/Blanketa/Blanketa(.exe)
        desktop.py (pywebview: WebView2 na Windows, Qt WebEngine na Linuxu) spouští pro každé okno
        sám sebe s --serve, tedy stejný server app.py; na Windows podepisuje winsign.ps1 (.NET)
```

Stránky jsou na všech systémech stejné; s aplikací kolem sebe mluví přes `ui/bridge.js`.

| Soubor | Úloha |
|---|---|
| `app.py` | HTTP server: převod FO → HTML (XSLT), relace formuláře, uložení, podpis, PDF |
| `zfoengine/` | port backendu zfo-editoru (Python 3, bez GTK): model, výpočty, opakované sekce, kontrola |
| `ui/shell.html` | rozhraní ZFO (lišta, panely, dialogy) vkládané do vykreslené stránky formuláře |
| `ui/pdf.html` | rozhraní PDF nad PDF.js |
| `ui/fo2html.xsl` | doplněk XSLT zfo-editoru (obrázky, rozměry) |
| `zfo_sign.py`, `zfo_verify.py` | podpis a ověření vrstev XMLDSig |
| `pdf_sign.py` | vyplnění a podpis PDF, ověření podpisů |
| `certstore.py` | podpisové identity na všech systémech: klíčenka (`keychain.py` + `macsign`), úložiště Windows (`winsign.ps1`), soubory `.pfx`; důvěryhodnost certifikátů podle systému |
| `desktop.py` | okna aplikace pro Windows a Linux |
| `ui/bridge.js` | spojení stránek s aplikací (macOS / Windows a Linux / prohlížeč), sdílený výběr certifikátu |

## Sestavení

PyInstaller neumí sestavovat pro jiný systém, takže každá verze se sestavuje na svém systému. Všechny tři najednou postaví GitHub Actions.

**macOS** (Xcode Command Line Tools, Python 3.12+):

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
./build.sh
```

Výsledek je `build/Blanketa.app` a `dist/Blanketa-<verze>-macos.dmg` / `.zip`, podepsané ad hoc (bez notarizace).

**Windows** (Python 3.12+, pro instalátor Inno Setup 6):

```bash
python -m pip install --require-hashes --only-binary :all: -r requirements-build.txt
```

```bash
python -m pip install --require-hashes --only-binary :all: --no-binary proxy-tools --no-build-isolation -r requirements-desktop.txt
```

```bash
python tools/build_desktop.py
```

**Linux** – nejjednodušeji v Dockeru, výsledek je v `dist/`:

```bash
docker build -f Dockerfile.linux-build -t blanketa-build .
```

```bash
docker run --rm -v "$PWD/dist:/src/dist" blanketa-build
```

Verze aplikace je v souboru `VERSION`, ikona se kreslí skriptem `tools/make-icon.py`.

## Vývoj

```bash
.venv/bin/python app.py 8602
```

Server ve vývojovém režimu nabídne vzorky ze `samples/` a `samples-pdf/` na http://localhost:8602.

Pozor: náhled v Chromu se liší od aplikace (WebKit). Rozvržení proto ověřujte nástrojem `tools/webkit-probe`:

```bash
swiftc -O tools/webkit-probe.swift -o tools/webkit-probe
```

Testy enginu:

```bash
.venv/bin/python -I tests/test_engine.py
```

## Testovací certifikát

```bash
tools/make-test-pki.sh
```

Skript vytvoří v `pki/` nedůvěryhodnou testovací CA, podpisový certifikát `pki/user.pfx` (heslo `test`) a na Macu i klíčenku `pki/test.keychain-db`. Aplikace spuštěná ze zdrojů tuto identitu nabízí s označením „(TEST)“; do sestavené aplikace se nejsnáz přidá jako soubor `user.pfx` v dialogu podpisu. Pro skutečné podání je potřeba kvalifikovaný certifikát.

Kontrola podpisu bez okna (používá ji i CI):

```bash
.venv/bin/python -I tools/selftest.py test
```

## Omezení

- Výpočty psané v JavaScriptu 602 se nevyhodnocují (výpočty XPath ano).
- Odesílání na portály (submit) není podporované.
- Formuláře s mnoha opakováními se otevírají pomalu (desítky sekund).
- PDF formuláře XFA nejsou podporované.
- Aplikace není notarizovaná (macOS) ani podepsaná certifikátem vydavatele (Windows SmartScreen ji při prvním spuštění může varovat).
- Na Linuxu se důvěryhodnost certifikátů hodnotí podle systémové sady kořenových CA; tokeny přes PKCS#11 zatím nejsou podporované (jen soubory `.pfx`).
- Na Windows a Linuxu se PDF tiskne přes výchozí prohlížeč PDF v systému.

## Bezpečnost

Bezpečnostní chyby prosím hlaste soukromě, ne veřejným issue – postup je v [SECURITY.md](SECURITY.md).

## Licence a cizí kód

Blanketa je svobodný software pod licencí [GNU GPL 3.0](LICENSE): kdokoli ji smí zdarma používat, šířit a upravovat,
upravené verze ale musí šířit se zdrojovým kódem pod stejnou licencí. Zdrojový kód každé vydané verze je v tomto repozitáři.

- `vendor/zfo-editor-src/` – zfo-editor, © CZ.NIC, GNU LGPL 2.0 nebo novější. `zfoengine/` je jeho upravený port pod stejnou licencí.
- `vendor/pdfjs/` – PDF.js 4.10.38, © Mozilla, Apache 2.0.
- Python knihovny podle `requirements.txt` mají vlastní licence (pyHanko MIT, pypdf BSD, lxml BSD…).

Přehled a podmínky jsou v [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Sestavená aplikace nese licence a zdroj `zfoengine/` ve složce `Licence` (na Macu `Contents/Resources/Licence/`).
