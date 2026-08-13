# Model card — de ML-modellen van Testbot2

Bijgewerkt op 2026-08-13. Gaat over `weerbot-modellen/modellen/modellen.json` en
de bijbehorende `.pkl`-bestanden. Niet over de rekenkern in `index.html` en
`app_params.js`; dat is de gekalibreerde kern die de app werkelijk toont, en die
staat beschreven in README.md.

## Wat het is

Per stad één model dat het dagmaximum op het afrekenstation voorspelt. 49
steden, verdeeld over drie labels:

| label | aantal | wat het is |
| --- | --- | --- |
| `ML` | 34 | eigen ridge-model, variant `ridge` of `ridge_klim` |
| `LINEAIR` | 14 | geen eigen model; de bestaande kern deed het beter |
| `GEPOOLD` | 1 | één model over alle steden heen (`pooled_gbm.pkl`) |

De vier `stagea_gbm_*.pkl`-bestanden (Atlanta, Chengdu, Dallas, Milaan) zijn
microklimaatmodellen uit een eerdere ronde en worden door de app niet geladen.

## Waarop het is getraind

`weerbot-modellen/deel9_wekelijks.py`, elke maandag 03:17 UTC, op
`features_alle.csv`: 49 steden, ongeveer 1000 dagen per stad, doel is het
stationsmaximum (`station_max`) met ERA5 als terugval.

Vijftien features:

- `p1_ifs`, `p1_aifs`, `p1_gfs`, `p1_icon`, `p1_gem` — het dagmaximum per
  modelsysteem uit de run van de vorige dag, opgehaald bij
  `previous-runs-api.open-meteo.com` met de modelnamen `ecmwf_ifs025`,
  `ecmwf_aifs025_single`, `gfs_seamless`, `icon_seamless`, `gem_seamless`
- `mm_spreiding` — de standaardafwijking over die vijf, met ddof=1
- `run2run` — het p1-gemiddelde min het p2-gemiddelde, oftewel hoeveel de
  laatste modelronde van de vorige is opgeschoven
- `lag2_err` — de fout van het kale modelgemiddelde van twee dagen terug, of
  drie als die er niet is
- `doy_sin`, `doy_cos` — de dag van het jaar
- `rh_gem`, `bewolking_gem`, `wind_max`, `instraling_som`, `neerslag_som`
- `klim` — alleen bij de variant `ridge_klim`, vier steden

Ontbreekt een feature, dan volgt `weerbot-ml.js` de conventie waarmee
`deel9_wekelijks.py` hem heeft gefit:

- `run2run` en `lag2_err` → nul, want zo staan ze in de trainingsmatrix
- een ontbrekend `p1_*` → het modelgemiddelde `mm_gem`
- de aux-features en `doy` → de mediaan uit de training (`params.med`)
- `klim` → geen terugval; zonder klimwaarde valt `ridge_klim` terug op `ridge`

De sigma komt uit de NGR-parameters: `sqrt(c² + (d · spreiding)²)`.

## De trainingsafstand

De features komen uit de run van de vorige dag. Dat is de horizon "morgen". De
app voorspelt op drie horizonnen — vandaag, morgen, overmorgen — en op de eerste
en de derde staat het model buiten de afstand waarop het is gefit. Dat is de
reden dat `ml_activatie.json` per stad **en** per horizon gaat.

Op horizon 2 is dat een meetbare vraag en het antwoord is voor vijf steden
gunstig. Op horizon 0 is het geen vraag maar een verlies: daar heeft de
rekenkern een verse run die het model niet kent. Zie hieronder.

## Wat het nu doet

Vijf steden krijgen de ML-uitkomst getoond, de rest niet. Singapore en Shanghai
op horizon 1 en 2, Chongqing, Hongkong en New York alleen op horizon 2. Voor
alle overige steden en horizonnen toont de app zijn eigen rekenkern en wordt de
ML-voorspelling er alleen naast gelogd.

Horizon 0 staat uit, en dat is nagerekend. De rekenkern krijgt daar de run van
vandaag (historical-forecast) terwijl dit model op `previous_day1` is getraind,
de run van gisteren. De ML-voorspelling is op h0 en h1 hetzelfde getal, dus het
verschil zit volledig in wat de kern erbij krijgt: gemeten over `app_params.js`
is die verse run +0,223 °C waard, tegen een ML-winst van +0,018 °C. Geen enkele
van de 33 steden zou op h0 winnen; het beste geval verliest nog 0,046 °C. Een
model dat wél op lead 0 getraind zou zijn komt naar schatting op +0,014 °C uit,
ruim onder de drempel van +0,050. Zie REVIEW.md. Horizon 1 krijgt
`previous_day1` en is precies waarop getraind is, horizon 2 krijgt
`previous_day2`.

De lijst staat in `ml_activatie.json` en `bot/test_ml.py` toetst elke ingang
tegen `monitoring/backtest_lead<horizon>.json`, zodat er niets aan kan staan wat
de cijfers niet dragen.

## Wanneer het aan mag

Per stad en horizon, en pas als het schaduwlogboek het draagt. De afspraak staat
in `ml_activatie.json`:

- ten minste 60 dagen schaduw en 45 gematchte waarnemingen
- MAE ten minste 0,05 °C beter dan de huidige kern
- absolute bias ten hoogste 0,35 °C
- 80%-dekking tussen 72 en 88 procent
- CRPS beter dan de huidige kern
- nooit voor een stad met label `LINEAIR`
- Atlanta en Chengdu apart nakijken

Toetsen met `WeerbotKoppel.rapport()` in de console. Dat geeft n, MAE, bias,
CRPS en dekking per stad en per horizon, omgerekend naar °C.

Wat het model waard is, is los daarvan al bekend. `schaduw_backtest.py` traint
de modellen walk forward op `features_alle.csv` — wekelijks hertrainen op alleen
de dagen ervoor — en scoort ze tegen de rekenkern van de app op dezelfde dagen,
597 tot 740 per stad. Uitkomst: gemiddeld +0,018 °C op lead 1 en +0,025 °C op
lead 2, bij 12 van de 34 steden is ML slechter, en alleen **Singapore** en
**Shanghai** halen op beide horizonnen de drempels. Atlanta en Chengdu scoorden
hoger maar tellen niet mee: hun klim-term is niet walk forward. Zie REVIEW.md.

De backtest vervangt de wachttijd niet helemaal. Hij bewijst dat een model op
historische invoer beter is; hij bewijst niet dat de app die invoer live in
dezelfde vorm binnenkrijgt. Dat blijft het schaduwlogboek, maar dat is een
kwestie van dagen.

## Wat er bekend mis is

**De schaduwreeks begint op 13 augustus 2026.** Alles daarvoor is gemaakt met
invoer uit de verkeerde API en telt niet mee; de meting staat in REVIEW.md.

**De normale verdeling.** De kansen per temperatuurvak komen uit een normale
verdeling rond mu met de NGR-sigma. Temperatuurfouten zijn per station en per
seizoen scheef; die scheefheid zit er niet in.

**Eén doel.** Alleen het dagmaximum. Het dagminimum heeft geen ML-model; dat
loopt via de weekkalibratie in `app_params.js`.

**De meting van vandaag zit er niet in.** Geen enkele feature zegt wat er vandaag
al op de meter staat; de app gebruikt dat alleen om de kansen af te kappen.
Gemeten over 18.959 stad-dagen is alleen al de kale ondergrens `max(m, mu)` op
horizon 0 vanaf elf uur 's ochtends +0,058 °C waard, oplopend tot +0,402 vanaf
zes uur 's avonds — de MAE gaat daar van 0,815 naar 0,414. Tegen +0,018 °C voor
dit model. Maar het is een natuurkundige ondergrens en geen geleerde correctie,
dus het hoort in de rekenkern en niet hier. Zie REVIEW.md.

**Vier steden zonder eigen klimwaarde.** `ridge_klim` bestaat voor vier steden;
de rest gebruikt `ridge` en mist die term.

**De p1-reeks voor de komende dagen is een aanname.** Voor een doeldag D is
`previous_day1` de verwachting van één dag vóór D. Voor vandaag en morgen
bestaat die run al, voor overmorgen nog niet — horizon 2 krijgt dan geen invoer
en wordt overgeslagen. Of dat zo uitpakt is pas te zien aan het schaduwlogboek:
staat er na een dag voor alle 49 steden een regel, en op welke horizonnen.

## Terugdraaien

Zet elke ingang in `ml_activatie.json` op `false` en draai
`python3 weerbot-modellen/controleer_schil.py --zet`. Het bestand zit in de
schil van de servicewerker en in de vingerafdruk, dus de zelftest dwingt het
nieuwe versienummer af en bezoekers halen het bij de volgende keer openen vers
op. Zonder die stap blijft de oude schil staan en verandert er niets op het
scherm.

## Herkomst en licentie

Weergegevens van Open-Meteo, waarnemingen van IEM/METAR, NWS en HKO. De modellen
zijn getraind op afgeleiden daarvan. De code staat onder de MIT-licentie
(LICENSE); voor de gegevens gelden de voorwaarden van de bronnen zelf.
