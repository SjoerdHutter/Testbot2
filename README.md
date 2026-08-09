# Weerbot 2

Verbeterde versie van [TestBot](https://github.com/SjoerdHutter/TestBot). De app
voorspelt de dagtemperatuur in 49 steden uit een ensemble van vijf modelsystemen
(ECMWF IFS en AIFS, NCEP GEFS, ICON, GEM via Open‑Meteo), corrigeert die met
gekalibreerde parameters, en controleert zichzelf elke dag tegen de
stationsmeting waar ook de weddenschappen op afrekenen.

Alles draait in de browser; er is geen server en er gaat niets naar buiten
behalve leesverzoeken naar Open‑Meteo, IEM (METAR), de NWS en Polymarket.

## Wat er nieuw is ten opzichte van TestBot

### 1. Minimumtemperatuur

Naast het dagmaximum voorspelt de app nu ook het dagminimum per stad, voor
vandaag, morgen en overmorgen, met een eigen 80%-band.

Het minimum komt uit dezelfde ensembleaanroep (`temperature_2m_min` is een extra
veld, geen extra verzoek). De correctie erop komt uit twee bronnen, in deze
volgorde:

**De weekkalibratie.** `bot/kalibratie.py` rekent het minimum met exact dezelfde
walk-forward door als het maximum: eigen modelgewichten op het dagminimum, de
regressie, de ridge-kern en de restfoutkwantielen als band. Die parameters staan
per stad in `app_params.js` onder `min`.

**De ijking die de app zelf leert**, als terugval zolang het weekbestand nog geen
min-blok voor die stad heeft. Die gebruikt de skillgewichten uit de
maximumkalibratie en zet er een EWMA-gewogen bias overheen (halfwaardetijd 10
dagen), met de 10/90 restfoutkwantielen als band. Onder de 8 geverifieerde dagen
blijft de correctie uit en staat er `ongeijkt` bij het cijfer.

De eerste is duidelijk beter. Beide gemeten op dezelfde 181 evaluatiedagen over
240 dagen geschiedenis, negen van de negen stad-horizonnen in het voordeel van de
weekkalibratie:

| stad | vandaag | morgen | overmorgen |
| --- | --- | --- | --- |
| New York | −22,0% | −20,5% | −25,8% |
| Londen | −13,9% | −8,5% | −10,0% |
| Tokio | −10,3% | −13,0% | −11,8% |

De correctie zelf is niet klein — op LaGuardia ligt het rastermodelminimum
structureel zo'n 2,4 °F onder de stationsmeting, en juist die meting rekent de
markt af.

### 2. Filter op wat je wilt zien

In de balk staat **toon: alles · alleen max · alleen min**. Bij *alles* staat het
minimum onder het maximum op de kaart en onder elkaar in de lijst; bij *alleen
min* wordt het minimum het grote cijfer. Sorteren op **warmst** en het nieuwe
**koudst** kijkt naar het getal dat je op dat moment toont.

### 3. Steden verbergen

Met ⊘ in de kop van een kaart of rij verdwijnt een stad uit het overzicht. De
knop **verborgen (n)** in de balk haalt ze tijdelijk terug, ⊕ zet er een
definitief weer bij. De keuze staat in `localStorage`, dus hij blijft staan.

### 4. Favorieten

☆ maakt een stad favoriet; favorieten staan altijd bovenaan, ongeacht de
sortering. De knop **★ favorieten** filtert het overzicht tot alleen die steden.

### 5. Polymarket-venster

Polymarket noteert per stad per dag elf elkaar uitsluitende markten op de
hoogste temperatuur, en voor een handvol steden ook op de laagste. Ze rekenen af
op precies het station dat de app al gebruikt (LGA voor New York, EGLC voor
Londen, en zo verder), dus markt en voorspelling gaan over hetzelfde getal.

De knop **markt** opent een venster met:

* **totaal verhandeld volume** over de hele reeks, plus het volume van de
  laatste 24 uur en de open interest;
* de **Ja-kans per temperatuur**, per vak, met het verhandelde volume per vak;
* daarnaast onze eigen kans per vak, en het verschil in procentpunten;
* de verwachte temperatuur volgens de markt naast die van de app.

Onze kans per vak volgt uit de verwachting en de gekalibreerde 80%-band: die
band beslaat 2 × 1,2816 standaardafwijking, en daaruit volgt de kans dat de
afrekening in een vak valt (de markt rekent op hele graden, dus vak `84-85°F`
loopt van 83,5 tot 85,5). Scheve verdelingen vangt die vertaling niet.

Die band krijgt per stad en horizon nog een eigen factor mee (`band_lokaal`). De
globale band klopt gemiddeld maar is per stad te ruim of te krap; buiten de
steekproef getoetst op 49 steden zakt de gemiddelde afwijking van 80% dekking
daarmee van 5,8 naar 4,3 procentpunt, bij precies dezelfde bandbreedte. Dat komt
rechtstreeks in de kansen per vak terecht.

Er wordt niets verhandeld en er gaat niets naar buiten: het venster doet alleen
leesverzoeken naar de publieke Gamma-API van Polymarket.

### 6. Portefeuille

Het tabblad **portefeuille** (`portefeuille.html`, bereikbaar via de knop rechts
boven in de app) laat zien welke open posities gevaar lopen doordat de
verwachting sinds de instap is verschoven. Het klassieke faalgeval: NO op het
vak 20 °C, de verwachting kruipt richting 20 °C, en de marktprijs reageert pas
de ochtend zelf. Het alarm staat daarom in graden, niet in prijs.

Er wordt niets geplaatst en niets verkocht. Dit is signalering.

`bot/portfolio.py` haalt de open posities bij de data-API van Polymarket, draait
de slug terug naar stad, doeldag en vak, en zet daar per positie vier getallen
naast:

| getal | wat |
| --- | --- |
| `d` | afstand in graden van de verwachting nu tot de dichtstbijzijnde vakrand, 0 als de verwachting in het vak ligt. Bij een open einde telt alleen de rand die er is. |
| `b` | de vakbreedte: `hi − lo + 1`, of de standaardbreedte van de markt bij een open einde |
| `model_win_prob` | de kans dat de positie wint: bij NO 1 min de vakkans, bij YES de vakkans zelf |
| `delta_prob` | de vakkans nu min de vakkans bij instap, in procentpunten |
| `delta_mean` | hoeveel de verwachting sinds de instap is opgeschoven; positief is naar het vak toe |

Het stoplicht schaalt mee met de vakbreedte `b` — `hi − lo + 1`, en bij een open
einde de standaardbreedte van de markt: 2,0 op een °F markt en 1,0 op een °C
markt. Zonder die schaal staat elke Aziatische markt permanent op rood, want
daar is een vak een hele graad breed en in New York twee.

| kleur | voorwaarde, de eerste die klopt wint |
| --- | --- |
| rood | de verwachting ligt in het vak, of `d` < 0,5 · `b`, of `model_win_prob` < 55% |
| oranje | `d` tussen 0,5 · `b` en 1,0 · `b`, of `delta_prob` boven +15pp |
| groen | de rest |
| afgerekend | de markt noteert het vak onder 0,02 of boven 0,98: de uitslag ligt er al |

Die laatste staat niet in de oorspronkelijke opzet en is er na de eerste echte
run bij gekomen. Polymarket rekent af zodra het dagmaximum binnen is, en de
prijs schiet dan naar 0,0005 of 0,9995 terwijl de dag lokaal nog loopt. Het
model kent die uitslag niet en blijft op de verwachting rekenen: een verloren
NO op Busan kreeg zo groen mee, met een `edge_now` van +98pp erbij. Een
stoplicht over "schuift de verwachting nog op" zegt daar niets meer — er valt
niets meer op te schuiven. Zulke posities staan onderaan, gedempt, met in de
reden of ze gewonnen of verloren zijn.

Elke positie draagt de regel die zijn kleur zette mee als `reason`; die staat in
het tabblad onder het stoplicht als tooltip. Zonder die reden is een kleur niet
na te rekenen.

Eén afwijking van die tabel, met opzet: de twee afstandsregels zijn geschreven
vanuit het faalgeval van een NO. Voor een YES is de verwachting in het vak juist
de winnende stand, en zou de tabel letterlijk toegepast een winnende positie
rood kleuren. Die twee regels slaan daarom over voor een YES waarvan de
verwachting in het vak ligt; de modelwinkans doet daar het werk.

TYO en SIN krijgen `high_uncertainty`, en het tabblad zet er `±2°` bij. Die
steden hebben geen betrouwbare biaskalibratie en het Open-Meteo raster zit er op
de post waarop Polymarket afwikkelt ongeveer 2 °C naast; zonder die vlag geeft
het stoplicht daar een zekerheid die het niet waarmaakt.

Naast het stoplicht staat `edge_now`: de eerlijke waarde min de huidige bied, in
procentpunten. Dat is bewust een aparte kolom. Rood betekent "mijn aanname
wankelt", de verkoopbeslissing is een andere som.

Valt de ensemblefetch van een stad om, dan volgen er twee herkansingen met tien
en twintig seconden pauze. Zonder die herkansingen kost één hapering in de
verbinding het hele modelbeeld van een stad, en staat elke positie daar die run
zonder licht; in de eerste vijf runs gebeurde dat twee keer, op twee
verschillende steden, allebei met een TLS-handshake die niet rond kwam. Blijft
het misgaan, dan blijft de positie staan met `light: "unknown"` en de reden
erbij — een stad stilletjes laten verdwijnen is erger dan een gat dat zichzelf
meldt.

Uren tot sluiting worden gerekend als middernacht aan het einde van de doeldag
in de lokale tijdzone van de stad, met `zoneinfo` — niet met een vaste
UTC-offset, want die klopt maar in een deel van het jaar.

Draaien, en de bestanden die eruit komen:

```
python3 bot/signalen.py --portfolio    de vlag op de bestaande bot
python3 bot/portfolio.py               los, hetzelfde resultaat
python3 bot/portfolio.py --dump-raw    de ruwe respons van de data-API
python3 bot/test_portfolio.py          de zelftest, offline
```

In het tabblad staat de stadsnaam; de stadssleutel hangt eronder als tooltip.
Die sleutel blijft in `portfolio.json` (`city`) en in `portfolio_history.csv`
(`key`) het koppelveld, want daarop sluiten de logboeken op elkaar aan. De naam
komt uit `weer.STEDEN` en staat er als `city_name` naast: om te lezen, niet om
op te koppelen.

Het adres staat als `WALLET` bovenaan `bot/portfolio.py` en is per run te
overschrijven met `--wallet`. Let op dat dat het adres moet zijn dat de posities
*aanhoudt*: op Polymarket is dat vaak een apart proxy-adres en niet het adres
waarmee je tekent. Vraag je het verkeerde op, dan geeft het eindpunt een lege
lijst terug en meldt de module nul open posities — wat leest als "alles gedekt".
Zowel de CLI als het tabblad zeggen er daarom bij dat nul ook het verkeerde
adres kan betekenen.

**Let op bij de eerste echte run.** De veldnamen van
`https://data-api.polymarket.com/positions` staan in `portfolio.py` als een
tabel met kandidaatnamen per logisch veld en zijn nog niet tegen een echte
respons gelegd; de omgeving waarin de module geschreven is kon dat eindpunt niet
bereiken. `--dump-raw` drukt de eerste regel ruw af plus welke kandidaat per
veld raak was, zodat de tabel `VELD_ALIAS` in één commando te controleren is.
Een positie die op geen enkele naam aansluit verdwijnt niet: hij belandt met
zijn ruwe velden in `unmapped`, en het tabblad zet die onder de tabel in een
uitklapper met per regel de titel en de waarden. Het aantal staat in de kop, ook
als het blok dicht staat: een ingeklapte uitklapper moet te onderscheiden zijn
van geen gaten. De ruwe velden blijven in `portfolio.json` staan. Stil laten vallen zou hier de ergste fout zijn, want dan lijkt een gat
gedekt.

## Opstarten

De app toont eerst wat hij al heeft en haalt daarna pas op:

* de vorige ophaalronde staat in `localStorage` en wordt meteen getekend, mits
  hij van vandaag is; de verse cijfers vervangen hem stilletjes;
* de servicewerker geeft de schil (`index.html`, `app_params.js`, de scripts)
  direct uit zijn eigen cache en werkt die op de achtergrond bij;
* de uurcurves (het tijdstip van de dagpiek), de NWS-bijmenging voor de
  Amerikaanse steden en de dagelijkse controle houden het eerste beeld niet
  meer op; ze werken de kaarten bij zodra ze binnen zijn.

Gemeten op 49 steden, koud profiel: eerste beeld van 4,8 naar 1,7 seconde, en
bij een tweede bezoek naar ongeveer een kwart seconde. De schil zelf gaat onder
mobiele vertraging van 1,9 seconde naar 0,1 seconde.

Prijs van dat laatste: na een nieuwe versie zie je die pas de volgende keer dat
je de app opent. De weergegevens komen niet uit die cache maar rechtstreeks van
de weer-API's, dus die zijn altijd actueel.

## Onafhankelijk van TestBot

Weerbot 2 is een volledige, zelfstandige app: alle bestandsverwijzingen zijn
relatief, geen enkel bestand of eindpunt wijst naar de TestBot-repo, en de
workflows checken hun eigen repo uit en pushen naar hun eigen `origin`. Er zijn
geen secrets nodig.

Twee dingen die de browser wél deelt tussen apps op hetzelfde domein — GitHub
Pages zet `sjoerdhutter.github.io/TestBot/` en `/Testbot2/` op één herkomst — en
hoe ze hier gescheiden zijn:

* **`localStorage`** is per herkomst, niet per pad. Alle sleutels heten daarom
  `weerbot2-…` in plaats van `weerbot-…`; zie `opslagSleutel()` in `index.html`
  en `SLEUTEL()` in de twee `weerbot-ml*.js`-bestanden. Cache, logboek,
  kalibraties, eigen steden en voorkeuren staan dus los van elkaar.
* **`CacheStorage`** is óók per herkomst. De servicewerker gebruikt daarom het
  voorvoegsel `weerbot2-` en ruimt bij het activeren alleen caches met dát
  voorvoegsel op; hij leest offline ook alleen uit zijn eigen cache. Zonder die
  filter gooit elke app bij een nieuwe servicewerkerversie de offlineschil van
  de ander weg.

Let op: de eerste TestBot heeft die filter niet. Zolang die zijn `sw.js` niet
wijzigt gebeurt er niets, maar brengt TestBot ooit een nieuwe
servicewerkerversie uit, dan wist die eenmalig de offlineschil van Weerbot 2.
Weerbot 2 vult die bij het eerstvolgende online bezoek vanzelf weer aan. Wie dat
helemaal wil dichtzetten, past in TestBot `sw.js` dezelfde filter toe.

## Logboeken

In `logs/` staan vier bestanden. Ze worden vier keer per dag bijgewerkt door
`.github/workflows/signalen-log.yml`, die `bot/logger.py`, `bot/signalen.py` en
`bot/signalen.py --portfolio` draait en de map commit. Ze horen in de repo thuis: op deze reeksen wordt later
gemeten of de gerealiseerde hitrate boven de betaalde prijs ligt.

### `logs/ensemble_log.csv`

Wat de app per stad en doeldag aan modelwaarden zag. Vanaf 75 gelogde dagen
kalibreert `bot/kalibratie.py` hier rechtstreeks op, waarmee het verschil tussen
trainen en tonen verdwijnt. Eén regel per stad, doeldag en ensemblesysteem.

| kolom | wat |
| --- | --- |
| `gelogd_utc` | moment van loggen, UTC tot op de minuut |
| `key` | stadssleutel, bijvoorbeeld `NYC` |
| `doel_datum` | de dag waarover de voorspelling gaat, lokale datum van die stad |
| `lead` | 0 vandaag, 1 morgen, 2 overmorgen |
| `model` | naam van het ensemblesysteem bij de API, bijvoorbeeld `ecmwf_ifs025` |
| `gemiddelde` | het ledengemiddelde van het dagmaximum |
| `n_leden` | aantal leden waarop dat gemiddelde rust |
| `sd` | steekproefstandaarddeviatie van de leden (deler n-1); leeg bij één lid |
| `min`, `max` | laagste en hoogste lid |
| `p10`, `p25`, `p50`, `p75`, `p90` | ledenkwantielen, lineair geïnterpoleerd tussen de ordestatistieken (dezelfde definitie als numpy) |

Alle temperaturen staan in de eenheid van de stad zelf: °F voor de elf
Amerikaanse steden, °C voor de rest. Het logboek gaat over het dagmaximum;
`temperature_2m_min` wordt door `logger.py` niet opgevraagd en staat er dus ook
niet in. Dat is bewust: de kop heeft geen kolom `soort` en `kalibratie.py` leest
elke regel als een maximum. Het dagminimum staat wel in `signalen.csv`, dat
`signalen.py` in dezelfde aanroep meevraagt.

De regels van vóór de spreidingskolommen zijn met `bot/migratie_ensemble_log.py`
aangevuld met lege velden, zodat het bestand rechthoekig is en `csv.DictReader`
in `kalibratie.py` geen ontbrekende sleutels tegenkomt. Die migratie mag opnieuw
gedraaid worden; staat de nieuwe kop er al, dan gebeurt er niets.

### `logs/nws_log.csv`

De dagverwachting van de National Weather Service voor de elf Amerikaanse
steden, één regel per stad en doeldag. Kolommen: `gelogd_utc`, `key`,
`doel_datum`, `lead`, `temp_f`. Vanaf 40 gematchte dagen leert `kalibratie.py`
hier het bijmenggewicht per horizon uit; tot die tijd geldt 0,25.

### `logs/signalen.csv`

Eén regel per doeldag, stad, reeks en temperatuurvak van Polymarket, ongeacht of
er gehandeld is. Juist de niet-genomen vakjes horen erbij: zonder die regels
meet je alleen de eigen selectie en niets over het model.

| kolom | wat |
| --- | --- |
| `gelogd_utc` | moment van loggen, UTC tot op de minuut |
| `key` | stadssleutel |
| `doel_datum` | de dag waarover de markt afrekent, lokale datum van die stad |
| `lead` | 0 vandaag, 1 morgen, 2 overmorgen |
| `soort` | `max` of `min`: de hoogste- of de laagstetemperatuurreeks |
| `eenheid` | de eenheid van de markt, `°F` of `°C`; alle temperaturen in de regel staan daarin |
| `bracket_label` | de vaknaam zoals Polymarket hem schrijft, bijvoorbeeld `74-75°F` |
| `bracket_lo`, `bracket_hi` | de grenzen in hele graden; leeg aan de open kant van het onderste en bovenste vak |
| `verwachting` | de verwachting van de app, twee decimalen |
| `p10`, `p90` | de gekalibreerde 80%-band van de app |
| `model_kans` | de kans van de app op dit vak, vier decimalen: normale verdeling met sigma = (p90 − p10) / (2 × 1,2815515655446004), ondergrens 0,05, en de halve-graad randcorrectie op `lo` en `hi` |
| `leden_fractie` | de kale fractie ensembleleden die in het vak valt, vier decimalen |
| `markt_prijs` | de Ja-prijs uit de Gamma-API; leeg als de markt geen prijs noteert |
| `edge_pp` | (`model_kans` − `markt_prijs`) × 100, in procentpunten; leeg zonder prijs |
| `volume_24u` | het 24-uursvolume van de hele reeks, voor het toetsen van de liquiditeitspoort |
| `event_slug`, `markt_slug` | de slug van de reeks en van dit ene vak op Polymarket |
| `strat_a_signaal` | 1 als strategie A dit vakje op het moment van loggen aanmerkt: alle regels van A gehaald, beide poorten open én binnen het koopvenster; anders 0 |

`model_kans` en `leden_fractie` zijn twee onafhankelijke schattingen van
dezelfde kans. Door ze allebei te loggen is achteraf te zien welke van de twee
beter kalibreert.

Twee dingen om bij het narekenen te weten:

* `model_kans` wordt gerekend op de onafgeronde verwachting en band; de kolommen
  `verwachting`, `p10` en `p90` zijn op twee decimalen afgerond. Naspelen vanuit
  de regel geeft dus de kans op ongeveer een duizendste na.
* De correctiekern draait buiten de browser zonder lagterm. De app vult die met
  haar eigen verificatiereeks uit `localStorage`; die bestaat op een
  actierunner niet. Alle andere stappen (modelgewichten, kern, band, de
  NWS-bijmenging voor de Amerikaanse steden op dag 0 en 1) zijn gelijk aan de
  app. De kansfunctie zelf ligt via `bot/test_kern.py` cijfer voor cijfer vast
  op `onzeKansen` in `polymarkt.js`.

`bot/signalen.py` leest de stadssleutels, de maandnamen en de drempels van
strategie A rechtstreeks uit `weerbot-modellen/polymarkt.js`, zodat er maar één
lijst bestaat. Losse aanroep, bijvoorbeeld voor één stad en alleen vandaag:

```
python3 bot/signalen.py --steden NYC,LON --dagen 1
```

### `logs/portfolio_history.csv`

Eén regel per open positie per portefeuillerun. Dat is het hele punt van de
reeks: daarmee is later te zien of een verwachting geleidelijk kantelde, en of
rood daadwerkelijk verlies voorspelde.

| kolom | wat |
| --- | --- |
| `gelogd_utc` | moment van loggen, UTC |
| `key` | stadssleutel |
| `doel_datum` | de dag waarover de markt afrekent |
| `bracket_label` | het vak zoals Polymarket het schrijft |
| `adj_mean_now` | de gecorrigeerde verwachting op dat moment, in de eenheid van de markt |
| `model_prob_now` | de modelkans op dat vak |
| `current_bid` | de bied uit de data-API |
| `city_bias_used` | de correctie die de kalibratie op het kale ledengemiddelde legde |
| `light` | `red`, `amber`, `green` of `unknown` |

`city_bias_used` staat er expliciet in omdat `app_params.js` periodiek opnieuw
gekalibreerd wordt. Zonder die kolom lijkt zo'n bijstelling later in de grafiek
op een weersverandering.

De stand van nu staat in `portfolio.json` in de hoofdmap; dat bestand leest het
tabblad. Alleen open posities: een positie waarvan de doeldag voorbij is valt
eruit, en restjes onder een half aandeel tellen niet mee.

## Zelftests

```
python3 bot/test_kern.py                        # rekenkern index.html == kalibratie.py
                                                # en kansfunctie == polymarkt.js
python3 bot/test_portfolio.py                   # slug terug, afstanden, netteren
                                                # en elke tak van het stoplicht
python3 weerbot-modellen/controleer_upload.py   # bestandshashes tegen MANIFEST.txt
python3 weerbot-modellen/pak_features.py check  # featurebundel
```

Deze draaien ook in `.github/workflows/zelftest.yml` bij elke push.

## Structuur

| pad | wat |
| --- | --- |
| `index.html` | de hele app: opmaak, rekenkern, controle, kalibratie, weergave |
| `portefeuille.html` | het tabblad portefeuille; leest alleen `portfolio.json`, doet zelf geen API-verzoek |
| `portfolio.json` | de stand van de open posities, geschreven door `bot/portfolio.py` |
| `app_params.js` | wekelijks gekalibreerde parameters per stad en horizon |
| `weerbot-modellen/polymarkt.js` | Polymarket-koppeling en het marktvenster |
| `weerbot-modellen/weerbot-ml*.js` | ML-modellen, nog in schaduwfase |
| `bot/` | kalibratie, logboeken, portefeuillebewaking en zelftests in Python |
| `logs/` | ensemblelog, NWS-log, signalenlog en portefeuillereeks; zie hierboven |
| `.github/workflows/` | dagelijkse en wekelijkse herberekeningen |
| `REVIEW.md` | externe codereview en het narekenen van de aanbevelingen |
