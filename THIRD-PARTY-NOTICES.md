# Cizí kód a licence

Blanketa sama je pod licencí GNU GPL 3.0 (`LICENSE`). Obsahuje tyto cizí součásti:

| Součást | Autor | Licence | Text licence |
|---|---|---|---|
| `zfoengine/` – upravený port backendu zfo-editoru 0.3.2 | © 2009–2010 CZ.NIC, z.s.p.o.; úpravy 2026 Richard Winkler Bejr | GNU LGPL 2.0 nebo novější | `zfoengine/LICENSE` |
| `vendor/zfo-editor-src/` – zfo-editor 0.3.2 beze změn (XSLT, skripty, styly) | © 2009–2010 CZ.NIC, z.s.p.o. | GNU LGPL 2.0 nebo novější | `vendor/zfo-editor-src/zfo-editor-0.3.2/LICENSE` |
| `vendor/pdfjs/` – PDF.js 4.10.38 | © Mozilla Foundation | Apache 2.0 | `vendor/pdfjs/LICENSE` |
| Python knihovny v `requirements.txt` (pyHanko, pypdf, lxml, cryptography, …) | jejich autoři | vlastní (MIT, BSD, Apache 2.0) | v balíčcích |

Změny v `zfoengine/` proti zfo-editoru 0.3.2 jsou popsané v hlavičkách souborů a podrobně v historii gitu. Původní zdroj je v `vendor/zfo-editor-src/` (`zfo-editor-0.3.2.tar.gz`, `zfo-editor-0.3.2-3.src.rpm`).

Sestavená aplikace obsahuje tyto licence a zdrojový kód `zfoengine/` ve složce `Licence` (na Macu `Contents/Resources/Licence/`). Kdokoli tak může upravenou knihovnu nahradit vlastní verzí, jak LGPL vyžaduje.
