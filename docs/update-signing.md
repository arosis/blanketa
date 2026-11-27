# Podpis vydání

Každé vydání Blankety nese **manifest** s otisky všech souborů vydání a jeho podpis. Aplikace podle nich pozná,
že soubor, který si sama stáhla k aktualizaci, vydal projekt a nikdo ho cestou nezměnil. Klíče nejsou certifikát
vydavatele pro systém; slouží jen k ověření uvnitř Blankety.

## Soubory u vydání

- `blanketa-<verze>-update.json` je manifest.
- `blanketa-<verze>-update.json.sig` je podpis: Base64 ze surového 64bajtového podpisu Ed25519 přes přesné bajty
  manifestu.

Manifest je JSON bez zbytečných mezer a s abecedně seřazenými klíči:

```json
{"files":{"Blanketa-0.6.0-macos.zip":{"platform":"mac","sha256":"<64 hexadecimálních znaků>"}, …},"version":"0.6.0"}
```

Každý soubor má platformu a otisk SHA-256. Platformy a soubory:

| Platforma | Soubor |
|---|---|
| `mac` | `Blanketa-<verze>-macos.zip` |
| `windows` | `Blanketa-<verze>-setup.exe` |
| `windows-portable` | `Blanketa-<verze>-windows-x64.zip` |
| `linux` | `blanketa-<verze>-linux-amd64.tar.gz` |
| `linux-deb` | `blanketa_<verze>_amd64.deb` |

Do manifestu se dostanou jen tyto soubory; ostatní soubory ve složce vydání (například `.dmg`) se přeskočí.
Chybí-li kterýkoli z pěti, podepisování selže.

## Jak se podepisuje

Úloha `release` v `.github/workflows/build.yml` po stažení sestavených souborů spustí
`tools/sign_release.py release <verze>`. Nástroj přečte soukromý klíč z proměnné prostředí `UPDATE_SIGNING_KEY`,
vytvoří manifest a podpis a hned je ověří vestavěnými veřejnými klíči z `RELEASE_PUBLIC_KEYS` v
`update_signature.py`. Chybí-li klíč nebo podpis neověří, nástroj skončí chybou a **vydání se nepublikuje**.
Klíč nikdy nevypisuje ani nezapisuje.

## Vygenerování klíčů

Klíče se vytvářejí před prvním vydáním s aktualizátorem; verze bez skutečného klíče se sama nikdy neaktualizuje.

1. Dvakrát spusťte `python tools/release_keys.py`. Pokaždé vypíše nový soukromý a jemu odpovídající veřejný klíč
   a nic nezapíše. První dvojice je hlavní klíč, druhá záložní.
2. Soukromý hlavní klíč uložte jako tajnou proměnnou `UPDATE_SIGNING_KEY` v prostředí `release` na GitHubu
   (Settings, Environments, release). Vydání tam schvaluje vlastník.
3. Soukromý záložní klíč uložte offline mimo GitHub (správce hesel a šifrovaná záloha). Na GitHubu nesmí být.
4. Oba veřejné klíče vložte do `RELEASE_PUBLIC_KEYS` v `update_signature.py` a změnu vydejte běžným postupem
   přes pull request. Aplikace přijme podpis kterýmkoli z nich.
5. Soukromé klíče nikdy nepatří do repozitáře, issue, logu ani artefaktu.

## Ztráta a únik klíče

- **Ztráta hlavního klíče:** vydání se podepíše záložním klíčem a v té verzi se přidá nový hlavní veřejný klíč.
- **Ztráta obou klíčů:** verze s těmito klíči se už samy neaktualizují. Uživatelé si musí novou verzi stáhnout
  ručně; aplikace jim odkaz na stažení dál nabídne, protože kontrola verze na klíči nezávisí.
- **Únik klíče:** vydejte novou verzi s novou dvojicí klíčů a starý veřejný klíč z aplikace odstraňte. Dokud
  ho aplikace obsahuje, platí podpis i tímto klíčem, proto je potřeba vydat rychle.
