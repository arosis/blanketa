# Blanketa

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/arosis/blanketa/badge)](https://scorecard.dev/viewer/?uri=github.com/arosis/blanketa)

**Web: [blanketa.cz](https://blanketa.cz)** · autor: [Richard Winkler Bejr](https://winkler-bejrovi.cz)

Aplikace pro macOS, Windows a Linux (Ubuntu, Debian, Mint …) na vyplňování a elektronické podepisování:
- **formulářů 602XML (`.zfo` a `.fo`)**, které jinak otevře jen Software602 Form Filler pro Windows (např. ČÚZK návrh na vklad, formuláře ČSSZ a krajů);
- **PDF formulářů (AcroForm)**.

Otevře také datové zprávy a doručenky z datových schránek uložené jako `.zfo`, jen ke čtení.

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
„Podpořit Blanketu“ (na Macu v menu Blanketa, jinde v menu Nápověda), kde je i okno „O aplikaci Blanketa“ s verzí,
licencí a odkazy. Po podpisu se ve stavovém řádku nejvýš jednou za 14 dní objeví nenápadná připomínka příspěvku;
volbou „Už nezobrazovat“ zmizí natrvalo. Děkuji!

## Nahlášení chyby

Chybu nahlásíte v aplikaci volbou „Nahlásit chybu…“ (na Macu v menu Blanketa, jinde v menu Nápověda, a také v okně
„O aplikaci Blanketa“). Blanketa připraví e-mail na **podpora@blanketa.cz** a otevře ho ve vašem poštovním programu;
nic neodešle sama. Hlášení obsahuje to, co do něj napíšete, a technické údaje: verzi Blankety, systém, druh dokumentu
(formulář ZFO, PDF, datová zpráva, podepsaný kontejner) a posledních nejvýš 5 chyb skriptu stránky. Neobsahuje vyplněné
údaje, obsah formuláře, názvy souborů, certifikáty ani hesla. Celý text vidíte a můžete ho před odesláním upravit nebo
smazat. Hlášení čte jen autor Blankety a slouží jen k opravě chyby. Návrhy na zlepšení můžete psát i do
[GitHub Issues](https://github.com/arosis/blanketa/issues).

Když při práci s dokumentem selže server okna nebo nečekaně skončí, Blanketa si uloží krátký technický záznam: druh
chyby, místa v kódu Blankety, kde vznikla, činnost a datum – bez textu chyby, hodnot a názvů souborů. Při dalším
otevření okna nabídne hlášení připravit; záznam se pak přidá k technickým údajům a nic neodejde bez vašeho potvrzení.
Volbou „Už nenabízet“ se nabídka vypne natrvalo a zapnout ji jde znovu zaškrtnutím v okně „Nahlásit chybu“.

## Nové verze

Blanketa se nejvýš jednou za 24 hodin na pozadí zeptá služby GitHub (`api.github.com`), zda vyšla novější verze. Odešle
jen samotný dotaz bez údajů o verzi, dokumentech nebo certifikátech, GitHub tedy vidí jen vaši IP adresu a čas dotazu.
Když novější verze je, nabídne ji při dalším otevření okna, nikdy uprostřed práce: „Stáhnout…“ otevře v prohlížeči
balíček pro váš systém (na Linuxu stránku vydání s postupem instalace), „Připomenout za týden“ nabídku o sedm dní odloží.

Automatické kontroly vypnete v okně „O aplikaci Blanketa“ volbou „Automaticky kontrolovat nové verze“; tamní tlačítko
„Zkontrolovat aktualizace“ zjistí novou verzi i při vypnutých kontrolách. Instalace zůstává ruční, Blanketa se sama
nepřepíše.

## Stažení a instalace

Hotové balíčky vytváří GitHub Actions (`.github/workflows/build.yml`) při každé značce `v*`, nebo ručně ze záložky Actions:

| Systém | Soubor | Instalace |
|---|---|---|
| macOS | `Blanketa-<verze>-macos.dmg` (nebo `.zip`) | přetáhnout do Aplikací; první otevření macOS zablokuje, postup je [níže](#první-spuštění-na-macos) |
| Windows 10/11 | `Blanketa-<verze>-setup.exe` | instalátor bez práv správce; nabídne otevírání `.zfo` a `.fo` v Blanketě. Windows před spuštěním mohou varovat, postup je [níže](#první-spuštění-na-windows). Přenosná verze: `…-windows-x64.zip` |
| Ubuntu 22.04 a novější, Debian 12 a novější, Mint 21 a novější | `blanketa_<verze>_amd64.deb` | `sudo apt install ./blanketa_<verze>_amd64.deb` |
| jiný Linux s glibc 2.35 a novější | `blanketa-<verze>-linux-amd64.tar.gz` | rozbalit a spustit `Blanketa/install.sh` (bez roota, do domovské složky) |

Windows potřebují Microsoft Edge WebView2 (Windows 10/11 ho mají; instalátor jinak nabídne stažení).

Blanketa není podepsaná certifikátem vydavatele: na Macu není notarizovaná u Applu a instalátor pro Windows nemá placený podpis. Oba systémy proto při prvním spuštění varují. Varování neznamená, že v aplikaci něco našly, jen že vydavatele neznají. Balíčky stahujte jen ze [stránky vydání](https://github.com/arosis/blanketa/releases) tohoto repozitáře.

### První spuštění na macOS

1. Přetáhněte Blanketu do Aplikací a otevřete ji. macOS oznámí, že Apple nemůže zkontrolovat, zda aplikace neobsahuje škodlivý software. Klikněte na „Hotovo“, ne na „Přesunout do Koše“.
2. Otevřete nabídku Apple → Nastavení systému → Soukromí a zabezpečení a sjeďte dolů k části Zabezpečení.
3. U zprávy o zablokované Blanketě klikněte na „Přesto otevřít“. Systém se zeptá ještě jednou a vyžádá heslo nebo Touch ID; potvrďte volbou „Otevřít“. Tlačítko „Přesto otevřít“ je v nastavení asi hodinu od pokusu o otevření; když zmizí, otevřete Blanketu znovu.

Stačí to udělat jednou, příště se Blanketa otevře rovnou. Po instalaci nové verze Blankety je potřeba postup zopakovat. Na macOS 14 a starším stačí kliknout na Blanketu pravým tlačítkem, zvolit Otevřít a potvrdit.

### První spuštění na Windows

1. Prohlížeč může stažený instalátor označit jako neobvyklý nebo nebezpečný. V seznamu stažených souborů u něj zvolte jeho ponechání.
2. Po spuštění instalátoru se může objevit modré okno „Systém Windows ochránil váš počítač“. Klikněte na „Další informace“ a pak na „Přesto spustit“.

Totéž platí pro `Blanketa.exe` z přenosné verze. Když tlačítko „Přesto spustit“ chybí, blokuje neznámé aplikace správce počítače nebo funkce Inteligentní řízení aplikací (Smart App Control) a Blanketu na takovém počítači nejde spustit, dokud ji správce nepovolí nebo dokud tu funkci nevypnete.

## Co umí

**ZFO**
- Zobrazení formuláře ve vzhledu Form Filleru:
  - opakované sekce;
  - přepínané části (multiswitch);
  - výpočty a kontroly v XPath a JavaScriptu 602;
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
- Panel podpisů: neporušenost každé vrstvy, důvěryhodnost certifikátu podle systému a důvěryhodných seznamů EU a to, zda vydavatel certifikát nezrušil.

**PDF**
- Vyplňování přes PDF.js:
  - texty, zaškrtávátka, přepínače, seznamy;
  - ukládá se jako přírůstková aktualizace, takže dřívější podpisy zůstanou platné.
- Podpis PAdES (pyHanko):
  - do připraveného podpisového pole s razítkem;
  - nebo na místo, které uživatel vyznačí tažením myši;
  - nebo neviditelně;
  - volitelně s kvalifikovaným časovým razítkem (viz [Časová razítka](#časová-razítka)).
- Panel podpisů: totéž co u ZFO a navíc časová razítka (viz [Časová razítka](#časová-razítka)).
- Tisk vyplněného souboru v plné kvalitě.

**Datové zprávy a doručenky (ZFO z datových schránek)**
- Odesílatel, příjemce, věc, čísla jednací, data dodání a doručení, stav; u doručenky seznam událostí.
- Přílohy jde uložit jednotlivě nebo všechny do složky; Blanketa je sama neotevírá.
- Pečeť ISDS: neporušenost, důvěryhodnost certifikátu, zda nebyl zrušen, časové razítko. Zpráva zapečetěná někým jiným
  než provozovatelem ISDS se zobrazí s výrazným upozorněním.
- Zprávu nejde upravit ani znovu podepsat. Blanketa pozná zprávu podle obsahu, ne podle přípony.
- Formát podle Provozního řádu ISDS, příloha „WS manipulace s datovými zprávami“
  ([datovka.gov.cz](https://datovka.gov.cz/info/cs/80.html)).

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
| `zfoengine/` | port backendu zfo-editoru (Python 3, bez GTK): model, výpočty, opakované sekce, kontrola; `js602.py` je izolovaný interpret JavaScriptu 602 |
| `ui/shell.html`, `ui/shell.js` | rozhraní ZFO (lišta, panely, dialogy) vkládané do vykreslené stránky formuláře |
| `ui/pdf.html`, `ui/pdf.mjs` | rozhraní PDF nad PDF.js |
| `ui/message.html`, `ui/container.html` a jejich `.js` | datové zprávy a podepsané kontejnery; server předává data stránce jedním blokem JSON |
| `ui/fo2html.xsl` | doplněk XSLT zfo-editoru (obrázky, rozměry) |
| `zfo_sign.py`, `zfo_verify.py` | podpis a ověření vrstev XMLDSig |
| `pdf_sign.py` | vyplnění a podpis PDF, ověření podpisů |
| `certstore.py` | podpisové identity na všech systémech: klíčenka (`keychain.py` + `macsign`), úložiště Windows (`winsign.ps1`), soubory `.pfx`; důvěryhodnost certifikátů podle systému |
| `trustlist.py`, `revocation.py` | důvěryhodné seznamy EU a seznamy zrušených certifikátů |
| `time_stamps.py` | služba časových razítek při podepisování PDF a její nastavení |
| `support_reminder.py` | kdy po podpisu připomenout možnost přispět na vývoj |
| `error_report.py` | e-mail s hlášením chyby a popis systému pro něj |
| `crash_report.py` | záznam o chybě serveru okna a nabídka hlášení při dalším otevření |
| `update_check.py` | denní dotaz na novější vydání, uložený výsledek a odložení nabídky o týden |
| `desktop.py` | okna aplikace pro Windows a Linux |
| `linux_print.py` | náhled tisku PDF na Linuxu (PDFium) |
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

Server ve vývojovém režimu nabídne vzorky ze `samples/` a `samples-pdf/` na http://localhost:8602 a ukládá do `out/`.
S `--file CESTA [PORT]` otevře jeden formulář a podepsanou kopii uloží vedle něj, port 0 vybere volný port a vypíše ho
jako `PORT <n>`.

Pozor: náhled v Chromu se liší od aplikace (WebKit). Rozvržení proto ověřujte nástrojem `tools/webkit-probe`:

```bash
swiftc -O tools/webkit-probe.swift -o tools/webkit-probe
```

Testy (jednou předtím `tools/make-test-pki.sh`, viz níže):

```bash
.venv/bin/python -I -m unittest discover -s tests
.venv/bin/python -I -m unittest discover -s tests -p test_pdf.py   # jen jeden soubor
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

- Výpočty a kontroly psané v JavaScriptu 602 běží v izolovaném interpretu bez přístupu k souborům, síti a podpisu. Stejně běží skripty tlačítek a změn polí: zapisují jen do dat formuláře, přepínají kroky průvodce a přidávají a odebírají sekce. Uložit data a vytisknout formulář můžou jen přes dialog aplikace, kde rozhoduje uživatel; odeslat ani podepsat nemůžou. Kontroly, které formuláře spouštějí šablonami XSLT (`XSLCheck` ve starších formulářích MPSV), běží také, i s jejich pomocnými funkcemi v JScriptu. Co interpret neumí, nechá beze změny a upozorní na to.
- Formulář nedostane síť. Jedinou výjimkou je dotaz do registru ARES podle IČO, kterým formuláře MPSV a Jihomoravského kraje doplňují údaje firmy: Blanketa se nejdřív zeptá, zda smí IČO poslat Ministerstvu financí, a dotaz pak provede sama, formulář jen dostane odpověď. Nic jiného z formuláře neodejde. Tlačítko „Nápověda“ formulářů MPSV po souhlasu uživatele otevře v prohlížeči PDF z webu ministerstva (mpsv.gov.cz); formulář z něj nic nedostane. Číselníky a našeptávač obcí formulářů MPSV si Blanketa stáhne sama ze služby MPSV a uloží v nastavení, bez internetu použije uloženou verzi; formulář určuje jen název číselníku a nic z vyplněných údajů se neposílá. V kroku „Odeslání“ formuláře MPSV nabídnou úřad i s jeho e-mailem. Přes službu MPSV Blanketa podání neodešle, podepsaný formulář se posílá e-mailem nebo datovou schránkou. Číselníky z jiných adres se nenačtou a pole se vyplňují ručně. Adresy, které formulář MPSV při kontrole ověřuje v registru RÚIAN, Blanketa po souhlasu uživatele pošle Českému úřadu zeměměřickému a katastrálnímu a řekne, které registr nezná; adresu sama neopraví a kódy RÚIAN do dat nezapíše. Bez souhlasu nebo bez internetu adresy ověřené nejsou a úřad může takové podání odmítnout. Nová sekce přidaná skriptem se vždy zařadí na konec.
- Výrazy XPath ve formuláři se k počítači uživatele nedostanou: proměnné prostředí, jméno uživatele a cesta k souboru jsou pro ně prázdné a šablony XSLT nesmějí číst ani zapisovat soubory. Události aplikace (`SendAppEvent`) spouští jen tlačítko, na které uživatel klikne.
- Odesílání na portály (submit) není podporované.
- PDF formuláře XFA nejsou podporované; Blanketa na ně při otevření upozorní.
- Aplikace není notarizovaná (macOS) ani podepsaná certifikátem vydavatele (Windows), takže oba systémy při prvním spuštění varují; jak ji přesto otevřít, popisuje [Stažení a instalace](#stažení-a-instalace).
- Tokeny přes PKCS#11 zatím nejsou podporované (jen soubory `.pfx`).

## Důvěryhodnost podpisů

Systémy české kvalifikované autority (PostSignum, I.CA, eIdentity) většinou neznají. Když systém certifikátu nevěří, Blanketa ho dohledá v důvěryhodných seznamech EU: stáhne seznam EU z `ec.europa.eu` a národní seznam země vydavatele, ověří jejich podpisy a uloží je v nastavení. O otevřeném formuláři ani certifikátu se tím nikam nic neposílá. Bez internetu a bez uloženého seznamu panel podpisů řekne, že důvěryhodnost nešlo ověřit. Zda vydavatel certifikát nezrušil, zjistí Blanketa až po otevření formuláře ze seznamu zrušených certifikátů vydavatele: stáhne celý seznam, takže vydavatel nepozná, čí podpis si prohlížíte. Platí to jen pro vydavatele z důvěryhodných seznamů EU. Čas podpisu uvádí podepisující; potvrdit ho může jen časové razítko.

### Časová razítka

U PDF Blanketa čte časové razítko v podpisu i razítko celého dokumentu. Čas potvrdí jen neporušené razítko služby, která byla v tu chvíli kvalifikovaná podle důvěryhodného seznamu EU, a to certifikátem určeným jen pro časová razítka. Když vydavatel certifikát zrušil, rozhoduje o platnosti podpisu právě tento čas. Razítko dokumentu pokrývá jen podpisy, které už v souboru byly, a pozdější razítko dokumentu podpis po zrušení certifikátu nezneplatní, jen čas nepotvrdí. Zda nebyl zrušen certifikát samotného razítka, se nekontroluje.

Razítko jde připojit i při podepisování PDF: v dialogu podpisu se zaškrtne „Připojit časové razítko“ a vybere služba. Předvolená je veřejná služba Sectigo (`http://timestamp.sectigo.com/qualified`), která vydává kvalifikovaná razítka bez přihlášení, ale bez záruky dostupnosti a za podmínek jejího provozovatele. Kdo má smlouvu s jiným poskytovatelem (například PostSignum nebo I.CA), zadá adresu jeho služby a případně jméno; heslo se zadává při každém podpisu a neukládá se, a služba s přihlášením musí mít adresu `https://`. Službě odchází jen otisk podpisu (desítky bajtů), ne dokument; poskytovatel se dozví IP adresu a čas. Volba je vypnutá, dokud ji uživatel nezapne. U vlastní služby Blanketa předem neví, zda je kvalifikovaná; ukáže to po podpisu panel podpisů. Když razítko získat nejde nebo služba orazítkuje něco jiného než tento podpis, Blanketa nic neuloží, řekne proč a nabídne podpis bez razítka. Vznikne podpis s razítkem (PAdES-B-T); stav certifikátu pro dlouhodobé ověření (PAdES-LT) Blanketa k podpisu nepřikládá. Přihlášení ke službě certifikátem není podporované. Formuláře ZFO se podepisují bez razítka.

### Stav certifikátu uložený v PDF

Po skončení platnosti certifikátu ho vydavatel v aktuálním seznamu zrušených certifikátů uvádět nemusí, takže stav už nejde zjistit. PDF si ho ale může nést z doby podpisu (PAdES-LT): jako seznam zrušených certifikátů nebo jako odpověď vydavatele (OCSP), v úložišti dokumentu (`/DSS`) nebo přímo v podpisu. Blanketa takový stav použije, když ho podepsal vydavatel certifikátu z důvěryhodného seznamu EU (u odpovědi také jím pověřený certifikát), když čas podpisu potvrzuje kvalifikované časové razítko a když stav o tomto čase vypovídá. Stav vydaný v tomto čase nebo později, dokud certifikát ještě platil, je potvrzení: panel napíše, že v době podpisu certifikát zrušený nebyl. Stav vydaný před podpisem, který v době podpisu ještě platil (programy ho obvykle získávají těsně před podepsáním), potvrzení není: kdo podepisuje ukradeným klíčem po zrušení certifikátu, mohl by přiložit poslední seznam, který o zrušení ještě nevěděl. Panel ho proto uvede slabší větou, podle které je vidět, že vznikl před podpisem. Stav, který v době podpisu už neplatil, a stav u podpisu bez potvrzeného času se nepoužije; panel řekne proč. Co vydavatel říká dnes, má přednost; stav ze souboru se uplatní, až když dnešní stav zjistit nejde. Nic se kvůli tomu nestahuje. Prodlužování platnosti řetězem razítek dokumentu (PAdES-LTA) Blanketa nevyhodnocuje a zda nebyl zrušen certifikát pověřený k odpovědím, nekontroluje. Pozdější seznam také neukáže pozastavení certifikátu, které v době podpisu trvalo a mezitím skončilo. Když ale soubor nese i seznam platný v době podpisu, který pozastavení uvádí, bere se podpis jako vytvořený během pozastavení, i kdyby pozastavení skončilo ještě před ním: z uložených seznamů to rozlišit nejde.

U ZFO se čas podpisu dál bere z formuláře, tedy od podepisujícího. Vzorek ZFO s razítkem se nepodařilo najít a formát není veřejně popsaný, proto si ho Blanketa nedomýšlí.

Vzorové soubory v `samples-pdf/razitka/` vytváří `tools/make-timestamp-samples.py` s testovací autoritou.

## Bezpečnost

Bezpečnostní chyby prosím hlaste soukromě, ne veřejným issue – postup je v [SECURITY.md](SECURITY.md).

## Licence a cizí kód

Blanketa je svobodný software pod licencí [GNU GPL 3.0](LICENSE): kdokoli ji smí zdarma používat, šířit a upravovat,
upravené verze ale musí šířit se zdrojovým kódem pod stejnou licencí. Zdrojový kód každé vydané verze je v tomto repozitáři.

- `vendor/zfo-editor-src/` – zfo-editor, © CZ.NIC, GNU LGPL 2.0 nebo novější. `zfoengine/` je jeho upravený port pod stejnou licencí.
- `vendor/pdfjs/` – PDF.js 4.10.38, © Mozilla, Apache 2.0.
- Python knihovny podle `requirements.txt` mají vlastní licence (pyHanko MIT, pypdf BSD, lxml BSD…).

Přehled a podmínky jsou v [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Sestavená aplikace nese licence a zdroj `zfoengine/` ve složce `Licence` (na Macu `Contents/Resources/Licence/`).
