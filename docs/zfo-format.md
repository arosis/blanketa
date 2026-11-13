# Formát ZFO (Software602 602XML Form)

Technický popis formátu tak, jak jsme ho poznali při stavbě aplikace „Blanketa“.

Software602 formát veřejně nepopisuje. Vše níže vychází z rozboru skutečných formulářů a z vyplnění a podpisu ve Form Filleru na Windows. Ověřeno bylo i opačným směrem: soubory vytvořené touto aplikací Form Filler otevřel a podpis uznal. Kde jde o odhad, je to výslovně uvedeno.

Veřejné zdroje:
- registrace typu u IANA: [`application/vnd.software602.filler.form-xml-zip`](https://www.iana.org/assignments/media-types/application/vnd.software602.filler.form-xml-zip) (ZFO) a [`application/vnd.software602.filler.form+xml`](https://www.iana.org/assignments/media-types/application/vnd.software602.filler.form+xml) (FO);
- příručky Software602 (uživatelská příručka Form Filleru, příručka návrháře, „XPath výrazy“), kopie jsou jen lokálně v `docs-src/`;
- projekt zfo-editor (CZ.NIC, 2011, LGPL 2.0+), jehož XSLT a logika formulářů jsou základem `zfoengine/`. Zdroj je v `vendor/zfo-editor-src/`;
- statický rozbor instalace Form Filleru 4.81 kvůli kompatibilitě (oddíl 9). Form Filler se při něm nespouštěl a do repozitáře z něj nic nepřešlo.

## 1. Kontejner

`.zfo` je ZIP s těmito položkami:

| Položka | Obsah |
|---|---|
| `form.fo` | celý formulář: vzhled, datový model, logika, podpisy (XML) |
| `summary/summary.xml` | popis pro správce souborů: `summary/@title`, `@author` aj. |
| `thumbnails/…` | náhledy (nepovinné) |

Samostatný soubor `.fo` je totéž XML bez ZIPu a podpisy jsou v něm stejné. Blanketa pozná obal podle obsahu souboru a ukládá ve stejné podobě, v jaké formulář otevřela. Soubor `.fo` nemá `summary.xml` ani čas uložení v archivu, proto panel ukazuje čas změny souboru. Form Filler ve Windows otevře `.fo` vyplněný a podepsaný v Blanketě a podpisy v něm rozpozná.

V Linuxu má `*.fo` v souboru MIME vyšší váhu (60) než výchozích 50, protože `shared-mime-info` stejnou příponu přiřazuje typu XSL-FO `text/x-xslfo`.

## 2. Vrstvy podpisů v `form.fo`

`form.fo` je řada vnořených **obalujících** podpisů XMLDSig (enveloping):

```
dsig:Signature                       ← nejvnější vrstva (např. uživatel)
  dsig:SignedInfo
    dsig:Reference URI="#<objId>"    ← jediná reference, na Object níže
  dsig:SignatureValue Id="_<objId>"
  dsig:KeyInfo/X509Data              ← jeden certifikát
  dsig:Object Id="<objId>" MimeType="text/xml"
    dsig:Signature                   ← další vrstva (Software602)
      …
        dsig:Object Id=…             ← nejvnitřnější: vlastní formulář (fo:root…)
    dsig:SignatureProperties         ← čas podpisu této vrstvy
      dsig:SignatureProperty Target="#<Id podpisu>"
        fm:systeminfo datetime="…"
```

- **Kanonizace:** reference používá inkluzivní c14n (`REC-xml-c14n-20010315`), `SignedInfo` exkluzivní c14n.
- **Algoritmus:** Form Filler sám podepisuje RSA-SHA1. Podpis RSA-SHA256 přijme stejně (ověřeno).
- **Vrstvy Software602:** typicky dvě nebo tři vrstvy s certifikáty „License Key“ a „Publication Key“. Poznají se podle `602XML` v subjektu certifikátu. Jsou to podpisy vydavatele formuláře, ne uživatele.
- **Přidání vrstvy:** novou vnější vrstvou se vnitřní podpisy nemění, zůstávají platné.
- **Opakovaný podpis:** při novém podpisu se uživatelské vrstvy sloupnou až k nejvrchnější vrstvě Software602 a podepisuje se znovu (`app.py: publisher_layer`).

## 3. Kam patří vyplněná data

Toto je hlavní zjištění z referenčního souboru vyplněného ve Form Filleru:

- Vyplněná data jsou kopie `form:model/form:instance/*` s hodnotami.
- Vkládají se jako **`dsig:Object` bez atributu `Id`** na konec **nejvrchnější vrstvy Software602**. Ta ho nereferencuje, takže její podpis zůstane platný.
- Uživatelský podpis pak obalí celek včetně dat, takže data jsou podepsaná uživatelem.
- Při uložení bez podpisu se starý datový `Object` nahradí novým (`app.py: filled_form`).

## Časová razítka

Software602 uvádí, že formulář lze opatřit časovým razítkem, ale způsob uložení nepopisuje a žádný dostupný vzorek razítko neobsahuje. `dsig:SignatureProperties` nese jen čas uvedený podepisujícím, proto ho panel podpisů nepovažuje za potvrzený.

Podpisová knihovna Form Filleru umí XAdES ve verzích 1.1.1 (`http://uri.etsi.org/01903/v1.1.1#`) a 1.3.2 (`http://uri.etsi.org/01903/v1.3.2#`). Razítko ukládá jako `xades:SignatureTimeStamp/xades:EncapsulatedTimeStamp` v `xades:UnsignedSignatureProperties` (XAdES-T). Výchozí autorita je `https://www.secustamp.com/tsa/req`. Jádro obsahuje šablonu podpisu s `xades:QualifyingProperties` (verze 1.3.2), exkluzivní c14n pro `SignedInfo`, RSA-SHA256, SHA-256 pro reference a referencí typu `http://uri.etsi.org/01903#SignedProperties`. Ve kterém případě Form Filler XAdES místo popsaných vrstev použije, rozbor neurčil, proto chybí vzorek.

## 4. Datový model a vazby

- **Instance:** `form:model/form:instance` je výchozí XML dat. Prvky mají vlastní jmenný prostor formuláře, např. `d:`.
- **Vazby:** `form:bind` s atributy `nodeset`, `calculate`, `required` a `constraint` (výrazy XPath 1.0 s rozšířeními 602).
- **Id vazby není název prvku.** Například pole s vazbou `o1_check_adresa_pre_dorucovani` zapisuje do `d:check_adresa_pre_dorucovani`. Spojení se hledá přes `nodeset`.
- **Opakované sekce:** odpovídají opakujícím se prvkům instance. Ve vzhledu je obalují tabulky s třídou `602-repetition-table`.
- **Kontrola (Zkontrolovat):** povinná pole (`required`) a `constraint` se vyhodnocují pro každý uzel zvlášť. Některé zveřejněné formuláře MPSV (např. 208, 375 a 376) mají v podmínce `required` neuzavřenou závorku, kterou XPath nepřečte. Blanketa takovou podmínku vynechá, zkontroluje zbytek formuláře a ohlásí, že kontrola není úplná. Jak se v tom případě chová Form Filler, ověřené není.
- **Hodnoty polí:** pole omezuje hodnotu počtem znaků (`form602maxchars`), povolenými znaky (`form602chars`, výčet jako `[0123456789]` nebo skupiny jako `decimal_digits` a `uppercase|lowercase` spojené svislou čarou), svým typem (`form602ct`: `num` číslo, `dat` datum, `tim` čas) a typem vazby (`type`: typ XML Schema, nebo jednoduchý typ z vlastního `xs:schema` ve `fm:model`, případně se vzorem `xs:pattern`). Software602 tyto podmínky popisuje ve své šabloně pro převod formuláře do HTML („Improvement 602XML forms“, funkce `Checker.Check`), kterou nese `Filler602.exe`. Blanketa je při kontrole formuláře uplatní stejně a v tomtéž pořadí na uloženou hodnotu, prázdnou hodnotu nechá kontrole povinných polí. Vzor hledá kdekoli v hodnotě, jako šablona, ne v celé hodnotě jako XML Schema. Skupiny písmen jsou převzaté včetně toho, že mezi českými písmeny chybí ů, ň, ť a ď. Číslo uložené s desetinnou tečkou projde i výčtem, který povoluje jen čárku, protože uživatel ji tak píše. Hlášky jsou vlastní, překlady Form Filleru v instalaci nejsou. Zda vlastní kontrola Form Filleru postupuje stejně jako jeho šablona, ověřené není. V 159 stažených veřejných formulářích má `form602maxchars` 156 formulářů, `form602chars` 64, vlastní schéma 34 formulářů MPSV (všechny typy jsou texty, vzor mají čtyři).
- **Formát hodnot:** data drží datum jako `1990-02-01`, čas jako `08:30:00` a číslo s desetinnou tečkou. `form602format` určuje, jak je pole ukáže, obrázkem Windows: `d.M.yyyy`, `d. M. yyyy`, `H:mm`, `# ##0,00`, `##0,00 Kč` nebo `#0,00 '%'` (text v apostrofech se ukáže bez nich). Má ho 158 ze 159 stažených formulářů. Blanketa hodnotu podle něj ukáže a napsaný text převede zpět do podoby dat: datum `1. 2. 1990` i `1.2.1990`, čas `8.30` i `8:30`, číslo `1 234,5`. Text, který datem, časem ani číslem není, uloží, jak je, a kontrola ho nahlásí. Datum jde napsat i vybrat z kalendáře. Pole pro výstup (`out`) číslo zaokrouhlí na desetinná místa obrázku, pole pro zápis ukáže všechny číslice. Slovní měsíce a dny (`MMM`, `ddd`), dvanáctihodinový čas (`h`, `t`) a zvláštní obrázky jako `attch-size-kB` Blanketa nezná a ukáže hodnotu tak, jak je v datech. Znak `%` bere jako text, hodnotu nenásobí stem. Číselné pole s hodnotou `00.0` ukáže prázdné: Form Filler ji podle své šablony pro předčítání polí (`spfillcz.xsl`) bere jako nevyplněnou. Kontrola povinných polí ji dál počítá jako vyplněnou, jak ji posuzuje Form Filler, ověřené není (ve stažených formulářích ji mají jen dvě nepovinná číselná pole). Výpočet `CurrentDate( dd.MM.yyyy )` vrátí datum podle obrázku v argumentu.
- **Atributy, které Blanketa neřeší:** `form602minlength` (20 formulářů MPSV, vždy 2) statický rozbor Form Filleru 4.81 v jeho souborech nenašel a Blanketa ho nekontroluje. `form602datestyle="datepicker"` (8 formulářů Jihomoravského kraje) v šabloně Software602 zapíná výběr data z kalendáře, Blanketa ho nabízí u každého data. `form602minval`, `form602maxval` a `form602default` Form Filler zná, ale žádný stažený formulář je nepoužívá. Atributy `lk602*` jsou klíče překladů pro jazykové verze formuláře, hodnotu neomezují.
- **Výpočty:** `calculate` a `constraint` jsou XPath, nebo blok JavaScriptu 602 ve složených závorkách, který předá výsledek funkcí `Result`. Bloky provádí `zfoengine/js602.py`, funkce sdílené formulářem jsou ve `form:script`. Oficiální popis tohoto jazyka se nepodařilo najít. Form Filler ho provádí enginem V8, jde tedy o běžný JavaScript s funkcemi aplikace (oddíl 9). `Result` přijme právě jeden argument typu řetězec, jinak skript skončí výjimkou. Když ho skript zavolá víckrát, platí poslední hodnota, a skript po něm běží dál. Před každým výpočtem se výsledek vynuluje. Blanketa se chová stejně: jiný počet nebo typ argumentů vyhodí `TypeError`, který skript může zachytit. Když ho nezachytí, výpočet nic nezapíše a Blanketa ohlásí chybu skriptu. Veřejné formuláře proto čísla převádějí samy (`hodnota + ""`): v 159 stažených formulářích dostalo všech 18 836 volání `Result` při otevření a přepočtu právě jeden text. Funkce z `form:script` běží ve stejném globálním kontextu jako výpočty, takže vlastní funkce formuláře `RegExp` ve Form Filleru vestavěný konstruktor nejspíš přepíše. Blanketa v takovém případě pro `new RegExp` dál používá vestavěný konstruktor, protože ho formuláře jedné rodiny používají vedle vlastní funkce. Jak to dopadne ve Form Filleru, ověřené není. Prázdné číselné pole (`form602ct="num"`) se ve výpočtech XPath počítá jako nula, ať ho nikdo nevyplnil, nebo ho uživatel smazal, při změně pole i při celém přepočtu (tlačítko Přepočítat, tlačítko formuláře, které změnilo data). Pole samo zůstává prázdné a do souboru se za ně nula nezapisuje. Nula platí jen tam, kde by výpočet nad daty souboru nedal nic (součet s prázdným číslem): pole, které prázdné číslo jen přebírá, zůstane prázdné, skript ho najde prázdné a omezení (`constraint`) pole posuzuje hodnotu, jak ji uživatel zapsal. Form Filler 4.81 se chová stejně: ve vzorovém formuláři `samples/olk-dotace` po smazání jednoho ze tří sčítanců součet zůstal, po F9 také, a po změně jiného sčítance ze 100 na 200 ukázal 200.
- **Zaškrtávátka:** `fo:character` s `form602ct="chb"` zapíše do pole `form602on`, když je zaškrtnuté, a `form602off`, když není. Bez `form602off` zapíše prázdnou hodnotu: skripty formulářů MPSV se na odškrtnutí ptají podmínkou `DataXpath(".") != ""`. Na jedno pole bývá navázáno víc zaškrtávátek, každé s jiným `form602on` a bez `form602off` (např. „Žádost podává“ ve formuláři MPSV 12). Taková skupina je výběr jedné možnosti: zaškrtnuté je jen zaškrtávátko, jehož hodnota je v poli, a odškrtnutí pole vyprázdní. Má je 115 ze 159 stažených veřejných formulářů. Jak Form Filler zapisuje odškrtnutí bez `form602off`, je odvozené ze skriptů formulářů, spuštěním ověřené není.
- **Skripty tlačítek a změn polí:** `form602btnclick` a `form602onchange` jsou bloky JavaScriptu 602, které běží v `zfoengine/form_scripts.py` jako celek: když se skript zastaví, data se vrátí. Kroky průvodce jsou `multi-switch` řízený hodnotou v datech. Opakovanou sekci některé formuláře značí jen `form602ct="rep"` a `form602bind` bez `form602repe`. Form Filler při otevření doplní jazyk do `/d:root/@xml:lang` a formuláře podle něj vybírají texty dialogů.
- **Funkce skriptů tlačítek:** ze 159 stažených veřejných formulářů jich 155 má tlačítka se skriptem, dohromady 6 865, a 5 749 z nich Blanketa provede celé. Ostatní se zastaví na funkcích, které Blanketa nezná nebo neprovede (stav k listopadu 2026): PDF formuláře jako příloha k odeslání a k potvrzení e-mailem (382, `VariantToBase64`), odeslaná žádost a data z portálu (192, `Base64ToVariant`), `SendData` na adresy úřadů (186, tiskopisy a potvrzení stahované ze serveru), tisk prázdného formuláře (90), přílohy (`GetUploadedFileData` 56 včetně nahrání dat, které zkouška neumí, `SetData` 39, `SetBinData` 24), podformuláře (`SetSubformResult`, `OpenSubform`) a podpis (`ChooseCertEx`). Úplný seznam nabízených funkcí a přehled zastavení je v oddílu 10. Blanketa zná:
  - `NavigationDlg` ukáže seznam částí formuláře, které jsou právě vidět, a přejde na vybranou. Části jsou bloky s atributem `form602anchor` (ve formuláři MPSV 12 „A. Žadatel“ až „Elektronické přílohy“). Že dialog Form Filleru nabízí právě je, je odvozené z formulářů, spuštěním ověřené není.
  - `SaveData(data, název)` uloží text z `GetFormData` přes dialog pro uložení, s názvem, který dal formulář (formuláře MPSV `typ-formuláře.xml`), ve složce formuláře. Přijme jen prostý název souboru s příponou `.xml` a jeden soubor na jedno stisknutí, jinak skript zastaví. Soubor se uloží, až skript doběhne.
  - Tisk vyplněného formuláře („Vytisknout / Uložit (PDF)“, „Stáhnout vyplněnou žádost (PDF)“ ve formulářích MPSV): skript si vezme formulář sám (`GetFormData` s `requiredFormat = VarFFo`; formuláře přidávají příznak `GFD_ORIGINAL`, Blanketa ho nevyžaduje), otevře ho jako pomocný dokument (`OpenAux`), nahraje do něj data (`LoadFormData`), nechá si z něj vyrobit PDF (`GetFormData` s `VarFPdf`) a to ukáže (`ShowData`) nebo uloží (`SaveData` s názvem `.pdf`). Blanketa pomocný dokument nevytváří ani PDF sama nevyrábí: tuto posloupnost pozná a spustí vlastní tisk otevřeného formuláře, kde uživatel zvolí tiskárnu nebo uložení do PDF. Výstup je tedy formulář, jak ho Blanketa ukazuje, ne tisková sestava Form Filleru. Formuláře s více tiskovými stránkami ukazují ve Form Filleru PDF po jedné a před každou další se ptají; Blanketa tiskne jednou, a na otázky položené po vyžádání tisku proto sama odpoví Ne. Tisk si může vyžádat jen skript tlačítka, skript změny pole se na něm zastaví. Cokoli jiného skript zastaví: prázdný formulář (`CleanForm`), pomocný dokument z jiných dat (například stažených přes `SendData`), PDF jako příloha (`VariantToBase64`).
  - Nápověda formulářů MPSV: skript stáhne PDF (`SendData` metodou `SM_GET` na `url_base` + `documents/20142/36042/napoveda_formulare.pdf`), novější formuláře mu nastaví `format = VarFPdf`, a zobrazí ho (`ShowData`). Základní adresu dodává formuláři portál MPSV, ve staženém souboru je prázdná. Blanketa cestu `documents/<číslo>/<číslo>/<název>.pdf` doplní adresou `https://mpsv.gov.cz/`, zeptá se uživatele a dokument otevře v systémovém prohlížeči. Sama nic nestahuje a z formuláře nic neodejde. Skript z dokumentu nic nepřečte a může ho jen zobrazit, jednou na jedno stisknutí a jen z tlačítka. Jiné adresy skript zastaví (tiskopisy a potvrzení z `o/rest/formulare/…` jsou funkce portálu).
  - Nahrání uložených dat („Nahrát uložená data (XML)“ v novějších formulářích MPSV): tlačítko je ovládací prvek `form602ct="bib"` s `form602webaccept=".xml"`, po stisku nechá uživatele vybrat soubor a jeho obsah předá skriptu (`GetUploadedFileData`). Skript se do něj podívá (`EvalXPath(data, výraz)`, u MPSV porovná atribut `FormularId` s číslem formuláře), převede ho šablonou (`TransformData`) a nahraje do části dat (`LoadFormDataPart(data, cesta)`). Blanketa dialog nabídne jen tam, kde formulář žádá `.xml`, a soubor čte jako text bez entit a bez DTD. Tlačítka `bib` pro přílohy (PDF, obrázky) Blanketa nenabízí a jejich skript se zastaví na `GetUploadedFileData`. Zkouška tlačítek soubor vybrat neumí, proto tato tlačítka v počtech výše zůstávají mezi zastavenými.
  - Přílohy ve starších formulářích MPSV (99 stažených formulářů): pole `form602ct="bin"` s `form602menu="add"` dostane od uživatele soubor. Form Filler 4.81 zapíše jeho obsah v base64 na jednom řádku do dat pole (`form602bind`), název souboru do vazby `filenamebind` a příponu bez tečky, tak jak je v názvu („Sken Velky.PDF“ dá `PDF`), do vazby `mediatypebind` (ověřeno spuštěním na formuláři „Evidence docházky vykonavatele veřejné služby“). Potom běží skript pole (`form602onchange`): přečte soubor (`GetBinData`, `GetBinDataSize`), nechá ho zkontrolovat (`CheckBinData`), převede zpět na base64 (`VariantToBase64`), zapíše do opakované sekce příloh a pomocné pole vyprázdní. Blanketa dělá totéž jako jeden celek: skript, který se zastaví, po sobě soubor v datech nenechá. Dvě pole téhož formuláře („Načíst údaje“ a „Vložit soubor z počítače jako přílohu“) sdílejí stejnou vazbu, pole se proto rozlišují pořadím ve formuláři. Pole s `form602ffiltertype="strict"` přijme jen typy z `form602ffilter` (filtr dialogu Windows, „popis|*.pdf|…“). Soubor větší než 17 MB Blanketa nevezme; tolik berou formuláře MPSV na všechny přílohy jednoho podání.
  - Na stránce je pole pro soubor tlačítko s textem, který mu dal formulář: s `form602menu="add"` otevře výběr souboru (typy podle `form602ffilter`) a soubor pošle jádru, s `form602menu="save"` uloží soubor, který pole drží, zpět na disk pod názvem z dat. Aplikace ukládají jen to, co jim server formuláře vydá na adrese `/attachment` s číslem, proto si stránka soubor pole nejdřív nechá serverem připravit (`/file-of-field`) a aplikaci předá jeho číslo. Název z dat, který není prostým názvem souboru, se nahradí názvem `priloha`.
  - Přílohy v novějších formulářích MPSV (psaných pro FormApps Server, 32 stažených formulářů) mají dvě podoby. Seznam příloh: tlačítko `form602ct="btm"` přidá každý vybraný soubor jako položku opakované sekce, kterou jmenuje `form602attchsec`; hlídá typy (`form602webaccept`), velikost jednoho souboru a všech dohromady (`form602maxonefilesize`, `form602maxallfilessize`) a počet (`form602maxoccurs`), před vložením pustí skript `form602attchuploadstartaction` a po vložení skript odkazu přílohy (`form602ct="bil"`, `form602onchange`), který soubor nechá zkontrolovat a špatný zase odebere. Jedna příloha na pole (6 formulářů): tlačítko `form602ct="bib"` předá soubor svému skriptu (`GetUploadedFileData`), ten ho vloží do odkazu přílohy (`SetBinData(cesta, data)`) a prázdnou hodnotou (`new Variant()`) ho zase odebere. Odkaz `bil` říká, kam se co zapisuje: obsah v base64 do `form602bindref`, název do `filenamebind`, velikost v bajtech do `filesizebind` a příponu bez tečky, tak jak je v názvu, do `mediatypebind`; časové razítko položky seznamu zůstává `0`. Ověřeno na datech, která v listopadu 2026 uložil webový formulář MPSV č. 362 se dvěma zkušebními soubory: Blanketa zapíše položky seznamu i hodnoty, které si formulář dopočítá (`attachcount`, `attachsizeRaw`, `attachsize`, `empty`), stejně. Skript `form602attchuploadstartaction` jádro při přidání souboru spouští (rozhodnutí z listopadu 2026): běží ve stejném omezeném prostředí jako skripty tlačítek, ve všech 32 stažených formulářích jen zapíše, že seznam není prázdný, a bez něj formulář přílohy nezapočítá. Tlačítko nebo odkaz s `form602bindref` stojí pro svůj skript u dat, na která odkazuje: skripty zapisují vedle nich do `../d:error` a `../d:filename`.
  - Na stránce je tlačítko `bib` i `btm` tlačítko s výběrem souboru (u seznamu i několika najednou, každý se posílá jádru zvlášť) a odkaz `bil` tlačítko s názvem přílohy, které ji uloží zpět na disk stejnou cestou jako pole staršího typu.
  - `CheckBinData`, `MIMETYPE_CHECK`, `ANTIVIRUS_CHECK` a `ERROR_BIN_DATA_*` ve skriptové knihovně Form Filleru 4.81 nejsou: patří FormApps Serveru, pro který jsou formuláře MPSV psané (`formusage="web"` Form Filler vůbec neotevře, „winweb“ otevře, ale skript přílohy v něm na `CheckBinData` skončí). Hodnoty konstant jsou proto Blanketiny; formuláře výsledek jen porovnávají s názvy a 0 znamená bez závady. Blanketa kontroluje, že soubor má příponu a obsah, že pole typ přijímá a že obsah začíná tak, jak má u typů PDF, PNG, JPG, GIF, RTF, DOC/XLS, DOCX/XLSX/ODT a XML. Antivir nemá: na `ANTIVIRUS_CHECK` odpoví bez závady a uživateli ve stavovém řádku řekne, že soubor antivirem nekontrolovala.
  - `GetFormData` se šablonou i s částí dat (`xslName` a `dataPartXPath`) převede šablonou jen tu část. Tak „Uložit rozpracovaný formulář“ uloží `<Data FormularId="…">`, stejně jako Form Filler 4.81 (porovnáno na témže formuláři), a pole „Načíst údaje“ takový soubor pozná. `LoadFormDataPart` vrací pravdu, formuláře podle ní hlásí úspěch.
  - `LoadFormDataPart(data, cesta)` nahradí část dat tím, co skript dodá: prázdným prvkem ji formuláře vyprazdňují („Smazat údaje“), skupinou s několika prázdnými položkami nastavují počet položek (přílohy před odesláním, seznam měsíců k výběru) a souborem od uživatele ji plní. Blanketa obsah nenahrává tak, jak přišel: vezme část, jak ji formulář vydal, a hodnoty do ní doplní. Prvek ani atribut, který vydaná část nemá, se do dat nedostane; položka navíc je kopie první vydané položky téhož jména. Kořen obsahu musí mít stejné jméno jako část (jmenný prostor stejný, nebo žádný), obsah nesmí mít DTD a cesta musí vést k právě jednomu prvku, který není kořenem dat. Kopie položek se počítají mezi zápisy skriptu.
  - `GetFormData` bez šablony dá data formuláře, celá, nebo část, kterou určí `dataPartXPath`. `TransformData(data, šablona)` je převede šablonou formuláře; XML čte bez entit a bez DTD.
  - `CloseApp` zavře okno formuláře. Formuláře se předtím samy ptají, a Blanketa se na neuložené změny neptá ani jinak, takže příznak `CLOSE_IGNORECHANGES` nic nemění. V prohlížeči kartu zavřít nejde a Blanketa to řekne ve stavovém řádku.
  - Konstanty (`MB_*`, `ID*`, `PI_*`, `Var*`, `GFD_*`, `SM_*`, `SR_*`, `SHTTP_*`, `CLOSE_*`) mají hodnoty, které definuje skriptová knihovna Form Filleru v `JSInterpret.dll`. `MB_HTMLCONTENT`, ikony `MB_ICON2*` a `SR_ERROR` v ní nejsou, jejich hodnoty jsou Blanketiny.
- **Kontroly šablonami (`XSLCheck`):** starší formuláře MPSV kontrolují data šablonami XSLT uloženými v `fm:template`. Skript zavolá `doc.XSLCheck("Název")` nebo `doc.XSLCheck("Název", "noclean")`. Šablona běží nad daty a vrátí `<control><item><text>…</text><focus>/d:root[1]/…</focus></item>…</control>`, jednu položku za každou chybu: `text` je hláška pro uživatele, `focus` cesta k chybnému uzlu v datech. Vedle položek může šablona vrátit `<warning><text>…</text></warning>`, varování, které Form Filler ukáže v okně „Varování“ jen tehdy, když kontrola žádnou chybu nenašla, a za chybu ho nepočítá. Blanketa ho ukáže stejně. Bez položek kontrola vrátí `true`. Form Filler 4.81 druhý argument přijme (výchozí je `/`), ale nepoužije, a každá kontrola u něj nejdřív smaže všechna označení chyb (`form602error`). Po kontrolách MPSV, které jdou za sebou s `noclean`, by tak zůstaly označené jen chyby té poslední. Blanketa záměrně ukáže chyby všech kontrol jednoho spuštění: bez `noclean` začíná výsledek znovu, s ním se přidá k předchozí kontrole. Šablona `<control/>` (u MPSV `XsltClean`) chyby smaže i na stránce. Kurzor na první chybné pole Blanketa nepřesouvá, chyby ukáže v panelu kontroly. Chování Form Filleru vychází ze statického rozboru jeho jádra, spuštěním ověřené není. Podrobnosti rozboru jsou v oddílu 9. Pomocné funkce šablon jsou v JScriptu v `msxsl:script` s `implements-prefix`, jako v MSXML. Blanketa je pouští v interpretu skriptů (`zfoengine/form_stylesheets.py`). `GetFormData` s `xslName` vrátí výstup takové šablony jako text.
- **Dotazy na webové služby:** skript sestaví `new SendParam()` s `method` (`SM_GET`, `SM_POST`), `url` a `flags` a zavolá `doc.SendData(data, sendparam)`. Odpověď má `code` (`SR_OK` při úspěchu) a text v `data.data`. Formuláře MPSV a Jihomoravského kraje se tak ptají registru ARES (`https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/<IČO>`, u JMK `…/ekonomicke-subjekty-res/<IČO>`) a odpověď JSON čtou přes `JSON.parse`. Starší formuláře JMK volají zrušenou službu `wwwinfo.mfcr.cz`. Blanketa pustí jen GET na ARES, po potvrzení uživatele, a dotaz provede server (`lookups.py`).
- **Číselníky stažené při otevření:** formuláře MPSV mají `fm:enum` bez položek a s `updateurl` `$(Eval("StrCat(GetCustomConfigValue(VratCiselnik),Stat.xml)"))` nebo `…(url_base),fas-adapter-codelists/,Stat.xml)…`. Form Filler adresu doplní z nastavení portálu MPSV, číselník je na `https://mpsv.gov.cz/fas-adapter-codelists/<Název>.xml` jako `fm:enum` se seznamem `fm:list/fm:listitem`. Našeptávač (`form602ct="acm"`) volá ve `updateurl` skript `Naseptavac('ČÍSELNÍK - OBEC')`, který stáhne `Obec.xml` a vybranou položku předá skriptu v `ACIItemId` (kód) a `ACIText` (text). Blanketa tyto číselníky stáhne a uloží sama (`codelists.py`) a doplní je jen do otevřeného formuláře, ne do souboru. Číselník s `updatexsl` formulář po stažení přetvoří vlastní šablonou z `fm:template` (např. rozdělí `1;;Svobodný(á)` na kód a text nebo přidá prázdnou první položku). Blanketa ji spustí stejně jako šablony skriptů, a když selže, nabídne číselník tak, jak přišel. Úřady, kterým se podání posílá (krok „Odeslání“), mají v `updateurl` `…fas-adapter-codelists/,DataXpath(//d:Ciselnik_PrijemcePodani),.xml…`: název číselníku (`PrijemcePodani`, `PrijemcePodaniKrajskyUrad`, `PrijemcePodaniMpsv`, …) spočítá pole `Ciselnik_PrijemcePodani` z nastavení formuláře. Položka nese `e-mail;;datová schránka;;název`, šablona `Ciselnik_PrijemcePodani_Parse` z ní udělá atributy `itemtext`, `email` a `idds` a formulář je výpočtem `GetListItemData` přenese do polí `EmailPrijemce` a `ExtIS2Idds`. Blanketa pošle jen název, který začíná `PrijemcePodani`; odeslání přes službu MPSV nedělá.
- **Ověření adres v RÚIAN:** formuláře MPSV při kontrole nebo odeslání zjistí adresy ke kontrole šablonou `Validace_AdresaWS_XpathAdres` (nová generace, adresa má podřízený prvek `Ruian`) nebo `XsltGetAdresy` (stará generace, `__PomValidovanaAdresa`). Každou pak pošlou SOAP požadavkem na interní službu MPSV `…fas-adapter/ZkontrolujAdresuService-v1`, která adresu opraví a do dat zapíše kódy RÚIAN. Blanketa formuláři vrátí prázdný seznam a adresy (části `Ulice`, `CisloPopisne`, `CisloEvidencni`, `CisloOrientacni`, `Psc`, `Obec`, `CastObce`; jen české, neověřené a vyplněné) ověří sama ve vyhledávání ČÚZK (`ags.cuzk.gov.cz/…/RUIAN/MapServer/exts/GeocodeSOE/findAddressCandidates`, `ruian.py`), až s tím uživatel souhlasí. Vyhledávání najde adresu i podle části, takže Blanketa nalezené adresy porovná s vyplněnou po částech. Data formuláře nemění, kódy RÚIAN zůstanou prázdné.
- **Pole závislá na `system`:** `fm:bind` s `depends="system"` (ID a název formuláře, odkaz na nápovědu, seznam úřadů) Form Filler spočítá při otevření formuláře, `system` není žádné pole. Blanketa je spočítá při otevření a při přepočtu. Pole, která na nich závisí, při otevření vyplněného formuláře nepřepočítá: mají hodnotu uloženou spolu s nimi. Formulář, který nikdo nevyplnil, Blanketa po doplnění stažených číselníků přepočítá celý, stejně jako Form Filler při otevření: Form Filler 4.81 má v datech čerstvě otevřeného formuláře MPSV počitadla opakovaných sekcí, součty a vlastní číslo formuláře dřív, než uživatel cokoli udělá, a skripty na ně spoléhají (vložení přílohy sčítá velikosti dosavadních příloh). Po celém přepočtu se data téhož formuláře liší od Form Filleru už jen v atributu `Velikost` prázdné přílohy (`00.0` z předlohy, Form Filler ho vyprázdní). Novější formuláře MPSV tak znají vlastní číslo (`Att_FormularId` z `Pom/Formular/Id`), které ukládají do souboru s daty a při jeho nahrání porovnávají. Formulář může nést i číselník, který žádné pole nezobrazuje (`form602listname`) a žádný výpočet ani skript nejmenuje (např. `GetListItemData("ZdravotniPojistovna", …)`), třeba `Rok` u MPSV: ten se nestahuje a jeho chybění nevadí.

## 5. Vzhled (XSL-FO s rozšířeními 602)

Vzhled je XSL-FO (`fo:root`, `fo:simple-page-master`, tabulky, bloky). Pole jsou prvky `fm:…` navázané na vazby. Form Filler se drží těchto pravidel; HTML převod je musí napodobit:

| Pravidlo | Chování Form Filleru |
|---|---|
| Tabulky | všechny jsou `table-layout="fixed"` bez šířky, tedy 100 % dostupné šířky po odečtení okrajů |
| Okraje | okraj bez barvy je černý (ne `currentColor`) |
| Buňky | výchozí padding je 0 |
| Písmo | výchozí Tahoma; pole s `font-family` v buňce dědí písmo buňky |
| Barvy polí | z `fm:formparams` (`proprequiredformfieldbkcolor`, `proproformfieldbkcolor`) |
| Rozměr stránky | první `fo:simple-page-master` |
| Multiswitch | hodnota `all` znamená zvláštní režim přepínače a Form Filler ji zkontroluje dřív než názvy `case`; jinak hodnota vybere `case` se shodným `case-name`, a když žádný neodpovídá, nevybere žádný; prázdná hodnota vybere `case` se `starting-state="show"`, a když žádný není, první `case`. Blanketa při `all` ukáže všechny `case`. Že je režim `all` zobrazí všechny, je odvozené ze statického rozboru, spuštěním ověřené není. `case` bez `case-name` se chová jako `case` s prázdným názvem: hodnota ho nikdy nevybere, ukáže se jen jako první `case` při prázdné hodnotě (např. formuláře MPSV 12, 170 a 309). Žádný ze 159 stažených veřejných formulářů `starting-state` ani `all` nepoužívá |
| Opakované sekce | tlačítka jsou aplikační události `addsect`/`delsect`; prázdný koncový sloupec tabulky opakování se nezobrazuje |
| Nápověda k poli | atribut `form602tooltip` |
| Obrázky | `fm:internal-graphic` v base64 (také **BMP**), rozměr z obalujícího `fo:instream-foreign-object` |
| Vložená písma | `fm:fonts/fm:font` (TTF v base64) |

## 6. Nastavení formuláře a uživatelská tlačítka

`fm:formsettings` obsahuje mimo jiné tlačítka formuláře `fm:userbutton<N>`:

| Atribut | Význam |
|---|---|
| `caption`, `tooltip` | popisek a nápověda |
| `iconid`, `showtext` | ikona (`print`, `saveform`, `savedata`, `loaddata`…) a zda ukázat text |
| `express` | výraz, typicky `LastParam(SendAppEvent(checkdata), SendAppEvent(print, 2), …)` |

`SendAppEvent` volá funkce aplikace:
- `checkdata` – kontrola;
- `print` – tisk; `print, 2` tiskne jen vyplněné hodnoty;
- `saveform` – uložení;
- `submit` s `save_data` – export dat;
- `loaddata` – import dat;
- `recalc` – přepočet.

`fm:formparams` nese `form-id`, `creationdate`, `xmlcore-version` a barvy polí.

## 7. Výměna dat

**Uložit data do souboru:** obsah datové instance jako samostatné XML (kořen = kořen `form:instance`).

**Import dat:** přijme XML se stejným kořenovým prvkem a hodnoty vloží jako data formuláře. Formulář se tím nemění.

## 8. Ověření podpisu

Pro každou vrstvu se kontroluje:
1. digest referencovaného `Object` (c14n podle transformace);
2. podpis nad `SignedInfo`;
3. certifikát z `KeyInfo` a jeho důvěryhodnost.

Důvěryhodnost se hodnotí v úložišti certifikátů systému: na Macu přes `SecTrustEvaluateWithError`, ve Form Filleru přes úložiště Windows. Form Filler hlásí vydavatele Software602 jako „nebyl nalezen certifikát vystavitele“, pokud v systému chybí jejich CA. Nejde o chybu podpisu.

Implementace: `zfo_verify.py` (ověření), `zfo_sign.py` (podpis), `macsign/` (klíč v klíčence nebo tokenu).

## 9. Poznatky ze statického rozboru Form Filleru 4.81

Rozbor proběhl nad instalací pro Windows bez jejího spuštění, kvůli kompatibilitě. Popisuje chování, ne kód; nic z programu se do repozitáře nepřebírá. Nic z toho není ověřené spuštěním Form Filleru; co z kódu nevyplývá přímo, je označeno jako odhad.

**Složení programu**

| Část | Úloha |
|---|---|
| `Filler602.exe` | okno aplikace a tisk |
| `xmlcore.dll` | jádro formulářů: datový model, vazby, vzhled, kontroly, šablony |
| `JSInterpret.dll` + `v8.dll` | skripty formulářů (engine V8) včetně ladění |
| `W5_SIGN.dll`, `w7_sign.dll` | podpis a ověření: libxmlsec s OpenSSL 3, Windows CNG a NSS; XAdES, časová razítka, šifrování XML |
| `fffo2xhtml.xsl`, `ffxhtml2fo.xsl` | převod XSL-FO na XHTML a zpět, podle všeho pro pole s formátovaným textem (editor TinyMCE); vzhled stránky nekreslí |

Ukázkové formuláře v instalaci (úvodní stránka, datové zprávy, podpis PDF, konverze, e-faktura) jsou vlastní rozhraní aplikace napsané jako ZFO. Funkce, které používají, nevypovídají o úředních formulářích.

**Skriptové API.** `JSInterpret.dll` registruje 331 názvů:
- metody objektu dokumentu (`XSLCheck`, `GetFormData`, `SignData`, `SendData`, `InsertSection`, `GetBinData` …);
- parametrické objekty `DataParam`, `SignParam`, `SendParam`, `MailParam`, `PrintParam`;
- objekty `Signature`, `Certificate`, `Timestamp`, `AuxDocument`, `AuxElement`, `PdfClass`;
- globální funkce (`MessageBox`, `CurrentDate`, `RCCheck`, `ChooseCert`, `B64EncodeData` …).

Funkce kontrolují počet a typ argumentů a při chybě vyhodí `SyntaxError` nebo `TypeError`. Blanketa z nich zná přibližně čtvrtinu. Sémantika `Result` a sdílený kontext skriptů jsou popsané v oddílu 4.

**`XSLCheck`.** Přijme jeden nebo dva řetězce, druhý má výchozí hodnotu `/`, ale dál ho program nepoužije. Slovo `noclean` se v programu nevyskytuje, takže režim `noclean` z oddílu 4 Form Filler nezná. Kontrola nejdřív smaže v celém dokumentu atributy `form602error` z předchozích kontrol a pak zpracuje výstup šablony stejnou funkcí jako kontrolu dat formuláře:
- každý `item` s `text` a `focus` označí pole jako chybné a na první z nich přejde kurzor;
- texty všech `warning` ukáže v dialogu „Varování“ s tlačítky Ano a Ne, jen když kontrola nenašla žádný `item`; odpověď výsledek kontroly nemění;
- bez položek `item` vrátí `true`.

**Vzhled.** Jádro zná pevný seznam vlastností XSL-FO (včetně `border-*-radius`, `keep-*`, `break-*`, `page-break-*` a vlastností vodítek `leader-*`) a 75 atributů `form602*`/`lk602*`, mimo jiné `form602minval`, `form602maxval`, `form602maxchars`, `form602datestyle`, `form602default`, `form602tooltip` a `form602btnlook`. Výchozí písmo Tahoma používá okno aplikace; jádro samo zná Arial, Times New Roman a Lucida Sans Unicode. Další pravidla z oddílu 5 (šířka tabulek, barva okrajů, padding) jsou v kreslicím kódu a statickým rozborem ověřená nejsou.

## 10. Funkce skriptů tlačítek a jejich stav

Skripty tlačítek a změn polí dostanou vedle běžného JavaScriptu jen funkce z této tabulky. Volání čehokoli jiného skript zastaví, data se vrátí do stavu před stisknutím a uživatel se dozví, co se nestalo. Co sahá mimo data formuláře (tisk, zobrazení dokumentu z webu, uložení dat do souboru, zavření okna a dialog „Přejít na“), smí jen skript tlačítka; skript změny pole se na tom zastaví, aby formulář nic z toho nevyvolal pouhým psaním. Je to rozhodnutí Blankety, ve stažených veřejných formulářích to žádný skript změny pole nedělá. Stav k listopadu 2026; podrobnosti k jednotlivým funkcím jsou v oddílu 4.

| Objekt | Funkce | Co dělá v Blanketě |
|---|---|---|
| globální | `GetDocument()` | objekt dokumentu |
| globální | `MessageBox(text, titulek, příznaky)` | zpráva, nebo otázka Ano/Ne (`MB_YESNO`) |
| globální | `EvalXPath(data, výraz)` | hodnota z XML v proměnné |
| globální | `RCCheck(text)` | kontrola rodného čísla |
| globální | `GetNamedParam(název)`, `SetNamedParam(název, hodnota)` | pojmenované parametry formuláře |
| globální | `GetCustomItem(název)` | vždy prázdný text; hodnoty dodává portál úřadu |
| globální | `DbgLog(…)` | nic |
| globální | `new Variant()`, `new DataParam()`, `new SendParam()` | objekty pro předávání dat a parametrů |
| globální | konstanty `MB_*`, `ID*`, `PI_*`, `Var*`, `GFD_*`, `SM_*`, `SR_*`, `SHTTP_*`, `CLOSE_*` | hodnoty podle Form Filleru |
| dokument | `GetElement()` | prvek, ze kterého skript běží |
| dokument | `CheckData()` | kontrola formuláře, výsledek v panelu kontroly |
| dokument | `XSLCheck(šablona, režim)` | kontrola šablonou formuláře |
| dokument | `GetFormData(parametry)` | data formuláře, jejich část, výstup šablony, nebo formulář sám pro tisk |
| dokument | `TransformData(data, šablona)` | převod XML šablonou formuláře |
| dokument | `LoadFormDataPart(data, cesta)` | nahradí část dat, sloučeně s tím, jak ji formulář vydal |
| dokument | `SaveData(data, název)` | XML uloží přes dialog; PDF formuláře spustí tisk |
| dokument | `SendData(data, parametry)` | jen dotaz do ARES podle IČO a nápověda MPSV, obojí po souhlasu uživatele |
| dokument | `OpenAux(data)` | jen kopie formuláře pro tisk |
| dokument | `ShowData(data)` | tisk otevřeného formuláře, nebo nabídka dokumentu z webu |
| dokument | `NavigationDlg()` | dialog „Přejít na“ s částmi formuláře |
| dokument | `CloseApp()` | zavře okno formuláře |
| dokument | `SetProgressInfo(…)` | nic |
| prvek | `DataXpath(výraz)` | hodnota z dat formuláře; prázdný výraz dá prázdný text |
| prvek | `GetCurrentDataXPath()` | cesta k datům prvku |
| prvek | `GetListItemData(…)` | položka číselníku formuláře |
| prvek | `GetAttribute(název, cesta)` | atribut prvku formuláře |
| prvek | `SetData(cesta, vazba, hodnota)`, `SetDataTo(vazba, hodnota)` | zápis do pole |
| prvek | `InsertSection(cesta)`, `RemoveSection(cesta)` | přidá a odebere kopii opakované sekce |
| prvek | `ActivateField(cesta)`, `ActivateFieldEx(cesta)` | přesune kurzor na pole |
| prvek | `GetUploadedFileData()` | soubor, který uživatel k tomuto stisknutí vybral; vlastnost `data` je jeho text |
| prvek | `SetBinData(cesta, data)` | vloží soubor z `GetUploadedFileData` nebo `GetBinData` do odkazu přílohy; prázdný `Variant` přílohu odebere |
| prvek | `GetBinData(cesta)` | soubor vložený do pole pro přílohu (`form602ct="bin"`); vlastnost `data` je jeho text |
| prvek | `GetBinDataSize(cesta)` | velikost vloženého souboru v bajtech |
| prvek | `CheckBinData(cesta, kontroly)` | 0, nebo `ERROR_BIN_DATA_*`, když obsah souboru neodpovídá příponě nebo pole takový typ nepřijímá; antivir Blanketa nemá |
| globální | `VariantToBase64(data)` | obsah souboru z `GetBinData` v base64; cokoli jiného skript zastaví |
| globální | konstanty `MIMETYPE_CHECK`, `ANTIVIRUS_CHECK`, `ERROR_BIN_DATA_*` | hodnoty Blankety, viz oddíl 4 |
| pomocný dokument | `LoadFormData(data)`, `GetFormData(parametry)` | jen jako kroky tisku vyplněného formuláře |
| pomocný dokument | `CleanForm()` | zastaví skript: prázdný formulář Blanketa z tlačítka netiskne |

Na čem se zastavují tlačítka stažených veřejných formulářů (první zastavení každého tlačítka, 6 865 tlačítek ve 155 formulářích):

| Kde se skript zastaví | Tlačítek | Co formulář chtěl | Co se dozví uživatel |
|---|---|---|---|
| `VariantToBase64` | 382 | přiložit své PDF k odeslání nebo k potvrzení e-mailem | že to Blanketa neumí a formulář má uložit a podat e-mailem nebo datovou schránkou |
| `Base64ToVariant` | 192 | uložit odeslanou žádost a data z portálu | že to Blanketa neumí |
| `SendData` na jinou adresu | 236 | stáhnout tiskopis nebo potvrzení ze serveru úřadu, nebo mu poslat přílohy formuláře (50) | že spojení se serverem úřadu Blanketa formulářům nedovolí a jak formulář podat |
| `CleanForm` | 90 | vytisknout prázdný formulář | že z tlačítka se tiskne jen vyplněný formulář |
| `GetUploadedFileData` u „Nahrát soubor“ | 24 | nahrát přílohu | nic: v aplikaci tlačítko funguje, jen zkouška neumí vybrat soubor |
| `GetUploadedFileData` u „Nahrát uložená data (XML)“ | 32 | nahrát soubor s daty | nic: v aplikaci tlačítko funguje, jen zkouška neumí vybrat soubor |
| `SetData` | 7 | smazat údaje zápisem, který Blanketa neprovede | název funkce |
| `SetSubformResult`, `OpenSubform` | 35 | otevřít nebo zavřít podformulář | že podformuláře Blanketa neumí |
| `ChooseCertEx` | 26 | podepsat data z formuláře | že formulář se v Blanketě podepisuje tlačítkem Podepsat a uložit |
| ostatní | 36 | různé; většinou chyba nebo chybějící funkce ve skriptu formuláře | název funkce nebo důvod |

Skriptová knihovna Form Filleru (`JSInterpret.dll`) registruje 331 názvů funkcí, objektů a jejich vlastností. Ty, které v první tabulce nejsou, Blanketa skriptům nenabízí. Funkce použitelné ve výrazech XPath (výpočty a podmínky polí) jsou jiná sada a popisuje je oddíl 4.

## Pomocné programy

```text
zfo_sign.py IN.zfo OUT.zfo KEY.pem CERT.pem [--sha1] [pole=hodnota ...]
zfo_verify.py FORM.fo|FORM.zfo
macsign list [--keychain CESTA]
macsign sign --cert SHA1HEX [--alg sha256|sha1] [--keychain CESTA] < data > podpis.b64
macsign trust < certifikáty DER v base64, po jednom na řádku
winsign.ps1 list
winsign.ps1 sign SHA1 sha1|sha256 SOUBOR
winsign.ps1 trust < certifikáty DER v base64, po jednom na řádku
```

`macsign` podepisuje klíčem z klíčenky macOS nebo tokenu přes CryptoTokenKit, `winsign.ps1` je jeho protějšek pro úložiště
certifikátů Windows (`Cert:\CurrentUser\My`, včetně čipových karet) a funguje ve Windows PowerShellu 5.1 i PowerShellu 7.
Klíč systém neopustí a token si o PIN řekne systémovým dialogem. `list` vrací JSON s poli `sha1`, `subject`, `token` a
`certificate`, `trust` vypíše na každý řádek jeden JSON s poli `trusted` a `reason`.
