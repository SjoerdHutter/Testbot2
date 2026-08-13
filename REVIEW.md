# Review voorspellingsmethodiek Testbot2

Datum review: 2026-08-06.

## Conclusie

De gebruikte methodiek is inhoudelijk sterk voor een browser-only weerbot: de app combineert meerdere numerieke weersystemen, ijkt per stad en horizon, valideert walk-forward en houdt onzekerheidsbanden empirisch in de gaten. De grootste winst zit niet meer in een complexer ML-model, maar in betere live-data, langere/consistentere logging en expliciete kalibratie van minima en resolutiebronnen.

Op basis van `app_params.js` bevat de huidige export 49 steden en 139 stad-horizoncombinaties. De nieuwe kalibratie verlaagt de gemiddelde MAE van 1,011 naar 0,987 lokale graden; dat is ongeveer 2,4% verbetering tegenover de basis. Tegenover de oude correctie is de gemiddelde winst ongeveer 2,3%. De ridge-kern is gekozen bij 94 van 139 combinaties. De gemiddelde 80%-dekking is 79,9%, met een bandbreedte van 72,3% tot 87,2% per stad-horizon.

## Sterke punten

- De code gebruikt een echt ensemble van IFS, AIFS, GFS/GEFS, ICON en GEM en reduceert niet alles tot één modelrun.
- Kalibratie gebeurt walk-forward: voorspellingen worden steeds gemaakt met alleen informatie die vóór de doeldag beschikbaar was.
- De verwachting wordt per stad en horizon gecorrigeerd met modelgewichten, bias/slope, recente foutinformatie, spreiding tussen modellen en modelafwijkingen.
- De onzekerheidsbanden worden niet blind uit ensemble-spreiding overgenomen, maar empirisch gekalibreerd op restfouten en gecontroleerd op dekking.
- De browserkern en Python-kalibratie hebben pariteitstests, waardoor de kans klein is dat productie anders rekent dan de backtest.
- De maximumvoorspelling mengt NWS voor Amerikaanse steden in met een geleerd of terugvalgewicht, wat praktisch sterk is voor resolutiebronnen in de VS.

## Belangrijkste risico's en verbeterpunten

### 1. Minima zijn duidelijk zwakker gekalibreerd dan maxima

De weekkalibratie leert alleen maxima. Minima gebruiken wel de modelgewichten uit de maximumkalibratie en een browser-geleerde EWMA-bias met 10/90-restkwantielen, maar geen eigen offline walk-forward kalibratie. Dit is logisch als snelle toevoeging, maar methodisch zwakker: nachtminima hebben andere foutbronnen dan maxima, zoals stedelijk hitte-eiland, bewolking, windstilte en timing van frontpassages.

Aanbevolen verbetering: breid `bot/kalibratie.py` uit met `temperature_2m_min` voor previous-runs/historical-forecast en exporteer aparte min-parameters per stad/horizon. Verwachte winst: waarschijnlijk 3-10% MAE op minima, vooral bij luchthavens met structurele nachtelijke biases.

### 2. De winst van de nieuwe kern is positief maar beperkt

De huidige export laat gemiddeld ongeveer 2,4% MAE-verbetering zien tegenover de basis. Dat is reëel, maar niet enorm. Het commentaar in `kalibratie.py` vermeldt dat gradient boosting op dezelfde features slechter presteerde dan ridge, wat aansluit bij de testuitvoer. Complexere modellen gaan vermoedelijk alleen helpen met echt nieuwe, niet-lekkende predictors.

Aanbevolen verbetering: focus op datakwaliteit en extra predictoren die live beschikbaar zijn, zoals neerslagkans, bewolking, wind, dauwpunt, zeewind-indicatoren en uurcurve-features. Verwachte extra winst: circa 1-4% MAE voor maxima, lokaal meer voor kuststeden en convectieve regio's.

### 3. Live ensemble versus trainingsdata kan nog verschillen

De kalibratie gebruikt previous-runs/historical-forecast deterministische API's en valt later terug op de dagelijkse ensemblelog zodra genoeg logregels bestaan. Dat is goed, maar zolang de loghistorie kort of incompleet is, blijft er domeinverschil tussen training en de live ensemble-aanroep.

Aanbevolen verbetering: blijf `logs/ensemble_log.csv` consequent vullen en stap per horizon zo snel mogelijk over op ensemblelog-training zodra het minimumaantal dagen gehaald is. Verwachte winst: vooral betere bias en betere modelgewichten, orde 1-3% MAE.

### 4. Onzekerheidsdekking is gemiddeld goed, maar lokaal variabel

Gemiddeld is de 80%-dekking bijna perfect, maar het bereik van 72,3% tot 87,2% wijst op stad/horizon-specifieke onder- en overdekking. Voor markttoepassingen is dat belangrijker dan alleen MAE.

Aanbevolen verbetering: kalibreer bandfactoren hiërarchisch per klimaatgroep of per stad met shrinkage naar globaal. Een enkele globale keuze voor de spreidingsband is robuust, maar laat lokale miscalibratie liggen. Verwachte winst: minder misprijsde kansen; MAE verandert nauwelijks, CRPS kan enkele procenten verbeteren.

### 5. Station/bron-consistentie blijft de belangrijkste praktische foutbron

De app voorspelt specifiek het station waarop markt of verificatie afrekent. Dat is goed. Maar sommige steden gebruiken alternatieve bronnen of fallback naar ERA5 als METAR niet bruikbaar is. Dat kan een andere grootheid zijn dan de markt of het station, vooral in steden met sterke microklimaten.

Aanbevolen verbetering: maak per stad expliciet zichtbaar of kalibratie, live controle en marktresolutie exact dezelfde bron gebruiken. Blokkeer of label kansen als bronconsistentie ontbreekt.

## Code-observaties

- `index.html` bevat veel domeinlogica in één groot bestand. Dat werkt voor GitHub Pages, maar maakt review en testen moeilijk. Splits op termijn rekenkern, datafetch, verificatie, UI en marktlogica in losse modules.
- De NWS-bijmenging verschuift verwachting en interval samen. Dat is eenvoudig en meestal correct, maar de bandbreedte zou bij grote NWS/modeldiscrepantie juist groter moeten worden.
- De minimumcorrectie gebruikt `Math.min`/`Math.max` om de band nooit smaller te maken dan ensemble- of empirische band. Dat is conservatief en veilig, maar kan kansen te vlak maken zodra de minimumkalibratie volwassen is.
- De ML-koppeling staat terecht in schaduwmodus. Niet activeren zonder langdurige out-of-sample logvergelijking tegen de huidige ridge.

## Accuratesse-inschatting

- Huidige maximumkalibratie: sterk; gemiddelde verbetering ongeveer 2-3% tegenover de basis in de huidige parameterexport.
- Realistische verdere winst voor maxima zonder nieuwe databronnen: beperkt, circa 1-3%.
- Realistische verdere winst voor maxima met goede extra live-features en langere ensemblelogs: circa 3-6%.
- Realistische winst voor minima met een echte offline min-kalibratie: circa 3-10%, afhankelijk van stad en meetstation.
- Kanskalibratie/marktprobabilities kunnen relatief meer verbeteren dan puntvoorspellingen door lokale bandkalibratie en niet-normale verdelingen.

## Aanbevolen prioriteiten

1. Bouw een volledige offline min-kalibratie naast max-kalibratie.
2. Voeg per stad/horizon lokale of klimaatgegroepte band-shrinkage toe.
3. Gebruik NWS-discrepantie als onzekerheidssignaal, niet alleen als gemiddelde-shift.
4. Bewaak bronconsistentie tussen kalibratie, controle en markt explicieter.
5. Modulariseer `index.html` nadat de rekenpariteitstests zijn uitgebreid naar modules.

---

# Natrekking van de aanbevelingen

Toegevoegd op 2026-08-06, na het narekenen van bovenstaande review. De cijfers in
de analyse hierboven kloppen: 49 steden, 139 stad-horizoncombinaties, MAE 1,0112
naar 0,9873 (2,37%), ridge-kern bij 94 van 139, dekking gemiddeld 79,9% met
bereik 72,3 tot 87,2 procent. Alles nagerekend uit `app_params.js`.

Wat er van de voorgestelde wijzigingen is overgenomen, en waarom.

## Wel overgenomen

**Offline min-kalibratie** (`bot/kalibratie.py`, `bot/weer.py`). De aanbeveling
is terecht: minima leunden alleen op de ijking die de browser zelf leert. Eerst
gemeten, want een goede richting is nog geen verbetering. Beide methoden op
dezelfde records en dezelfde 181 evaluatiedagen, walk forward, 240 dagen
geschiedenis:

| stad | horizon | kaal | browser-ijking | weekkalibratie | verschil |
| --- | --- | --- | --- | --- | --- |
| New York (°F) | vandaag | 2,183 | 1,693 | 1,320 | −22,0% |
| | morgen | 2,449 | 1,963 | 1,560 | −20,5% |
| | overmorgen | 2,694 | 2,225 | 1,650 | −25,8% |
| Londen (°C) | vandaag | 0,601 | 0,511 | 0,440 | −13,9% |
| | morgen | 0,740 | 0,623 | 0,570 | −8,5% |
| | overmorgen | 0,970 | 0,711 | 0,640 | −10,0% |
| Tokio (°C) | vandaag | 0,867 | 0,613 | 0,550 | −10,3% |
| | morgen | 1,061 | 0,736 | 0,640 | −13,0% |
| | overmorgen | 1,105 | 0,771 | 0,680 | −11,8% |

Negen van de negen beter, 8 tot 26 procent. Dat is geen ruis: de browser-ijking
kent alleen een EWMA-bias over de laatste dagen, terwijl de weekkalibratie ook
eigen modelgewichten op het dagminimum leert, plus de regressie en de ridge-kern.

Overgenomen. `index.html` geeft nu voorrang aan de offline min-parameters, met de
browser-ijking als terugval zolang `app_params.js` ze nog niet heeft. De
min-parameters worden bewust uit het weekbestand zelf gelezen en niet uit
`paramsVan`: een herijking in de browser gaat alleen over het maximum en vervangt
het hele parameterblok, dus anders raakte de app zijn minimumijking kwijt zodra
hij zijn maximum bijstelde.

Twee kanttekeningen bij de gemeten reeks. De 1-minuutverrijking (`verrijk_1min`)
draait alleen op maxima, dus de gemeten dagminima komen uit de uurlijkse METAR's
en missen een dal tussen twee waarnemingen; dat geldt even hard voor de
browser-ijking, dus de vergelijking blijft eerlijk. En de min-kalibratie kost
23 procent extra looptijd in de weekworkflow (New York: 15,9 s maximum, 3,7 s
minimum).

**Lokale bandfactor** (`band_lokaal`). Getoetst op `features_alle.csv` met een
scheiding op datum, 70% trainen en 30% toetsen, 49 steden:

| | gemiddelde afwijking van 80% per stad | bereik | gemiddelde breedte |
| --- | --- | --- | --- |
| globale band | 5,78 procentpunt | 53 tot 94% | 3,273° |
| met `band_lokaal` | 4,35 procentpunt | 63 tot 93% | 3,273° |

Een kwart minder miskalibratie bij exact dezelfde bandbreedte. Overgenomen, en
in `index.html` toegepast op zowel maximum als minimum.

**Bronlabel in het marktpaneel.** Goedkoop en het maakt punt 5 zichtbaar, maar
omgedraaid ten opzichte van het voorstel. Daar kreeg elke stad een regel
`bronconsistentie: station`; dat is bij 48 van de 49 steden waar, dus alleen
ruis onder de tabel. Hongkong is de enige uitzondering — de controle loopt daar
via het observatorium en niet via het afrekenstation. Alleen daar staat nu een
waarschuwing.

## Niet overgenomen

**Weer-afhankelijke bandverbreding** (`weerOnzekerheid`). Getoetst op 44.236
stad-dagen uit `features_alle.csv`, met de absolute misser als doel:

| term | gefit coëfficiënt | richting in het voorstel |
| --- | --- | --- |
| modelspreiding | +0,3797 per ° | niet gebruikt |
| bewolking | −0,0002 per % | verbreedt boven 75% |
| neerslag | +0,0038 per mm | verbreedt tot +0,35° |
| wind | −0,0033 per km/u | verbreedt boven 25 km/u |
| dag-op-dagsprong | +0,0380 per ° | verbreedt boven 4° |

Bewolking doet vrijwel niets en wind heeft het omgekeerde teken: harde wind gaat
samen met een *kleinere* misser, wat fysisch klopt omdat een goed gemengde
atmosfeer beter voorspelbaar is. Samen verklaren de vier weertermen 0,92
procentpunt extra variantie bovenop de modelspreiding.

Alleen de dag-op-dagsprong heeft het juiste teken. Ook die haalt de toets niet:
met een scheiding op datum gaat de dekking van 81,4 naar 81,7 procent terwijl de
band 0,6 procent *breder* wordt. Je betaalt breedte voor dekking die er al was.

**NWS-verschil als verbredingssignaal.** De richting klopt: bij een groot
verschil tussen NWS en model is de misser gemiddeld 1,22°C tegen 0,88°C bij een
klein verschil, correlatie +0,26. Maar `logs/nws_log.csv` bevat 88 regels over
vijf dagen en elf steden. Dat is te weinig om een coëfficiënt op vast te leggen;
het logboek vult zichzelf, dus dit is over enkele maanden opnieuw te toetsen.

**De `verbreed`-vlag.** In het voorstel werd die getoetst ná `Math.min`/`Math.max`
in plaats van tegen de gekalibreerde band, waardoor hij per definitie altijd
`false` werd.

**Losse weerparameters per stad.** Het voorstel haalde ze op met één aanroep per
stad, binnen `haalStad`, waar het eerste beeld op wacht. Gemeten op 49 steden:

| | eerste beeld | verzoeken naar api.open-meteo.com |
| --- | --- | --- |
| huidig | 2407 ms | 6 |
| voorstel | 12548 ms | 55 |

Omdat de weertermen de toets hierboven niet halen, is de aanroep helemaal
vervallen. Zou hij ooit nodig zijn, dan gebundeld zoals `bundelUren` en pas na
het eerste beeld.

---

# Open vraag: staat het koopvenster van strategie A de verkeerde kant op?

Toegevoegd op 2026-08-07, tegelijk met `logs/signalen.csv`. Niet beantwoord in
deze wijziging en de waarden zijn niet aangepast.

`STRAT_A` in `weerbot-modellen/polymarkt.js` koopt tussen `uurVroeg` 36 en
`uurLaat` 12 uur voor sluiting. Uit 174 met de hand afgewikkelde weerposities
onder @rainmoneymaker komt het omgekeerde beeld:

| instapmoment | ROI |
| --- | --- |
| binnen 36 uur voor sluiting | −11,7% |
| verder dan 36 uur voor sluiting | +9,7% |

En het aandeel gefade brackets dat alsnog uitkomt loopt op naarmate je later
instapt:

| moment | gefade bracket komt alsnog uit |
| --- | --- |
| meer dan 36 uur vooraf | 17% |
| minder dan 18 uur vooraf | 43% |

Dat past bij elkaar: dichter bij sluiting weet de markt meer dan het model, dus
juist daar is de staart minder goedkoop dan hij lijkt. Het huidige venster laat
precies dat deel wel toe en sluit het deel uit dat het beter deed.

Waarom de waarden nu toch blijven staan: die 174 posities zijn een eigen
selectie. Ze zeggen wat er gebeurde met de vakjes waarop ik instapte, niet wat
er gebeurde met de vakjes die de strategie aanwees en die ik liet lopen. Op zo'n
selectie een drempel verschuiven is precies de fout die het signalenlog moet
uitsluiten.

Te beantwoorden zodra `logs/signalen.csv` genoeg regels heeft. Het logboek
schrijft elk vakje weg met `model_kans`, `markt_prijs`, `edge_pp` en het
tijdstip, ongeacht of er gehandeld is, dus de vraag is dan te stellen als: hoe
verhoudt de gerealiseerde hitrate zich tot de betaalde prijs, uitgesplitst naar
uren tot sluiting en naar edge-bucket? Bij vier metingen per dag over drie
doeldagen staat elk vakje op meerdere afstanden tot sluiting in het logboek, dus
het venster is uit dezelfde reeks te schatten in plaats van eruit te veronder-
stellen. Richtlijn: pas beoordelen bij een paar honderd afgewikkelde doeldagen
per bucket, anders vervangt de ene te kleine steekproef de andere.

## Nagekomen bij die vraag: welke uren tellen mee

Toegevoegd op 2026-08-07. De eerste versie van het signalenlog rekende de uren
tot sluiting uit het veld `endDate` van de Gamma-API. Dat veld staat voor élke
stad op 12:00 UTC van de doeldag, en dat is alleen voor Wellington het einde van
de lokale dag. Gemeten op de markten van 8 augustus:

| stad | `endDate` | middernacht lokaal (UTC) | verschil |
| --- | --- | --- | --- |
| Wellington | 08-08 12:00Z | 08-08 12:00Z | 0 uur |
| Tokio | 08-08 12:00Z | 08-08 15:00Z | 3 uur |
| Amsterdam | 08-08 12:00Z | 08-08 22:00Z | 10 uur |
| Londen | 08-08 12:00Z | 08-08 23:00Z | 11 uur |
| New York | 08-08 12:00Z | 08-09 04:00Z | 16 uur |
| San Francisco | 08-08 12:00Z | 08-09 07:00Z | 19 uur |

Daarmee stond de tijdpoort van strategie A per stad op een ander werkelijk
moment. `uren_tot` in `bot/signalen.py` rekent nu tot middernacht na de doeldag
in de tijdzone van de stad, en het logboek heeft er twee kolommen bij:
`uren_tot_sluiting` en `einde_api`, dat laatste zodat de aanname toetsbaar
blijft.

Gevolg voor de venstervraag hierboven: die is pas te beantwoorden met regels die
`uren_tot_sluiting` gevuld hebben. De 2057 regels van vóór deze correctie
hebben die kolom leeg en zijn niet nageschat; hun `strat_a_signaal` is op de
`endDate`-klok gerekend en klopt alleen voor Wellington. Ze tellen dus niet mee
in de vergelijking met de 174 handmatig afgewikkelde posities, die op het einde
van de lokale dag zijn gemeten. Het aantal bruikbare regels begint bij nul op
7 augustus 2026.

---

# Natrekking van de externe review en het implementatieplan

Toegevoegd op 2026-08-13, na twee aangeleverde stukken: *Technical Review and
Optimization* en *Implementation Blueprint*. Beide zijn geschreven op een oudere
kopie van dit project (Weerbot V3.1, 51 steden) en op de GitHub-weergave, die de
inhoud van de grote bestanden niet toont. Dat is aan de conclusies te merken.
Hieronder eerst wat er niet klopte, dan wat er wél uit is gekomen — en dat is
meer dan de drie P0's van de review samen.

## De drie P0's van de review bestaan niet

**"De workflows stoppen na het installeren van de pakketten."** Ze doen dat
niet. `hertraining-wekelijks.yml` draait `deel9_wekelijks.py` en commit vier
bestanden, `klim-dagelijks.yml` draait `bereken_klim_vandaag.py` en commit
`klim_vandaag.json`, en `kalibratie-wekelijks.yml` weigert zelfs te committen
onder de 45 steden. Alle drie hebben `workflow_dispatch`. De review zegt er
zelf bij dat de bestanden niet volledig te lezen waren; dat had de bevinding
moeten tegenhouden in plaats van hem op P0 zetten.

**"De modelsleutels lopen niet gelijk."** Ze lopen wel gelijk. `ENS_MODELLEN`
in `index.html` en `ENSMAP` in `weerbot-ml-koppel.js` dekten elkaar precies. De
`gfs_seamless` en `gem_seamless` waar de review over viel zijn `UUR_MODELLEN`,
de uurkoers voor de piekgrafiek, en die komt nooit bij de ML-koppeling.

**"De servicewerker gooit de caches van andere apps weg."** Dat deed hij in de
oude kopie; in dit project staat het voorvoegsel `weerbot2-` er al, ruimt
`activate` alleen sleutels met dat voorvoegsel op, en dwingt
`controleer_schil.py` in de zelftest af dat het versienummer meegaat met de
schil.

Verder: het zijn 49 steden en niet 51, en de minima worden sinds 6 augustus wel
degelijk offline gekalibreerd — alle 49 steden hebben een `min`-blok in
`app_params.js`. Aanbeveling P2 "bouw een min-kalibratie" is daarmee al gedaan,
met de meting in de vorige sectie.

## Wat de review en het plan allebei missen

Beide stukken bespreken hoe de ML-modellen aangezet moeten worden. Geen van
beide kijkt naar wat er in die modellen gáát. Daar zit het probleem.

De modellen worden getraind door `deel9_wekelijks.py` op de deterministische
previous-runs: `p1_ifs` tot en met `p1_gem`, opgehaald bij
`previous-runs-api.open-meteo.com` met de modellen `ecmwf_ifs025`,
`ecmwf_aifs025_single`, `gfs_seamless`, `icon_seamless` en `gem_seamless`. Live
kregen ze iets anders: `weerbot-ml-koppel.js` nam `d.mlx.m`, en dat zijn de
gemiddelden van de ensembleleden uit `ensemble-api.open-meteo.com`. Voor twee
van de vijf gaat het zelfs om een andere modelvariant: `gem_global` tegen
`gem_seamless`, `ncep_gefs025` tegen `gfs_seamless`.

Gemeten door `logs/ensemble_log.csv` naast `features_alle.csv` te leggen, 377
gematchte stad-dagen op lead 1, alles in °C:

| feature | bias | sd | gemiddeld verschil |
| --- | --- | --- | --- |
| `p1_ifs` | −0,142 | 1,063 | 0,647 |
| `p1_aifs` | +0,039 | 0,926 | 0,668 |
| `p1_gfs` | +0,115 | 1,923 | 1,450 |
| `p1_icon` | −0,234 | 0,999 | 0,740 |
| `p1_gem` | −0,776 | 2,061 | 1,479 |
| modelgemiddelde | −0,200 | 0,740 | 0,567 |

Wat dat met de voorspelling doet, over 262 stad-dagen waarvoor het model en
beide reeksen er zijn: de ML-uitkomst verschuift gemiddeld **0,565 °C**, met
Jeddah op 1,76, Qingdao op 1,63 en New York op 1,15. De winst die deze modellen
moeten opleveren is 0,89 → 0,83 °C MAE, oftewel 0,06 °C. De ruis op de invoer
was negen keer zo groot als het effect dat gemeten moest worden.

Daarmee vervalt de vraag waar het implementatieplan over gaat. Er viel niets te
activeren en er viel ook niets te beoordelen: het schaduwlogboek van de
afgelopen maanden is gevuld met voorspellingen op invoer die de modellen niet
kennen.

Twee kleinere versies van hetzelfde:

- `run2run` stond altijd op `null`. De feature zit in alle 38 ridge-modellen.
  Effect per graad run-to-run: gemiddeld 0,088 °C, in Houston 0,36 °C. Dat
  signaal was er domweg niet. De nul waarmee hij werd ingevuld is overigens de
  juiste terugval — `_matrix` in `deel9_wekelijks.py` doet hetzelfde — maar
  invullen is iets anders dan meten, en hier werd altijd ingevuld.
- `lagFout` kreeg de EWMA-restfout van de eigen rekenkern doorgegeven, terwijl
  de modellen op `lag2_err` zijn gefit: de kale fout van het modelgemiddelde van
  twee dagen terug. De eerste heeft een spreiding van 0,56 °C, de tweede van
  1,18 °C over dezelfde 49 steden. De lagterm telde dus structureel half mee.

## Wat er is veranderd

`weerbot-ml-koppel.js` haalt de p1- en p2-reeksen nu zelf op bij dezelfde API en
met dezelfde modelnamen als de training, gebundeld in drie groepen en ná het
eerste beeld, net als de aux-aanroep die er al stond. Daaruit komen p1,
`mm_spreiding` (met ddof=1, zoals `np.std(p1v, ddof=1)`) en `run2run`. Ontbreekt
die reeks, dan wordt er niet voorspeld: een schaduwcijfer op de verkeerde invoer
is misleidender dan geen cijfer.

`index.html` legt in de verificatierij de fout van het kale modelgemiddelde vast
(`mmf`), en `lag2Voor` leest daaruit de lagfeature in de vorm die de training
kent. Het oude `mlx`-blok met ensemblegemiddelden is vervallen.

`weerbot-ml.js` blijft een ontbrekende `run2run` of `lag2_err` met nul invullen
en niet met de mediaan. Dat zag er bij eerste lezing uit als een fout — elke
andere feature valt wél op `params.med` terug — maar het is de conventie van de
training: `_matrix` zet een ontbrekende `run2run` op 0.0 vóórdat het de medianen
uitrekent, en `_laad` begint `lag2_err` als nulvector. Als nul gefit is nul ook
wat het moet zijn. `bot/test_ml.py` legt beide kanten nu vast, inclusief dat de
aux-features juist wél naar de mediaan gaan, zodat het verschil niet nog eens
per ongeluk wordt rechtgetrokken.

Het schaduwlogboek is v2. Het oude logboek schreef per stad per doeldag één
regel, en die werd elke dag overschreven door de kortere horizon; wat overbleef
was altijd lead 0. Nu staat de horizon erbij, plus de sigma en de eenheid van de
stad. `schaduwRapport()` geeft daardoor n, MAE, bias, CRPS en 80%-dekking per
stad én per horizon, alles omgerekend naar °C zodat een graad Fahrenheit niet
even zwaar meetelt als een graad Celsius.

`bot/test_ml.py` legt de trainingsdefinities vast: welke modellen worden
opgevraagd, welk dagmaximum, welke spreiding, welke terugval. 32 toetsen,
draait mee in de zelftest. De sleutelvraag van de review staat er ook in, nu als
toets in plaats van als vermoeden.

## Wat er van het implementatieplan is overgenomen

**Activeren per stad en horizon** (`ml_activatie.json`). Overgenomen, en de
reden is sterker dan het plan geeft. Het plan wil per horizon kunnen activeren
omdat dat fijnmaziger is. De echte reden: de modellen zijn op p1 getraind, de
run van de vorige dag, en dat is de horizon "morgen". Op vandaag en overmorgen
worden ze buiten hun trainingsafstand gebruikt. Dat is geen fijnafstelling maar
een reden om die twee apart te beoordelen.

Het bestand staat in de schil van de servicewerker en in `controleer_schil.py`.
Wijzigt het, dan valt de zelftest om tot het versienummer meegaat, en halen
bezoekers het vers op. Terugdraaien is daarmee: alles op `false`, nummer
ophogen. Het plan liet dit bestand buiten de schil en gaf de rollback als
handmatige procedure; zo is het afdwingbaar.

**Sigma, CRPS, dekking en bias in het schaduwrapport.** Overgenomen, maar in
javascript en niet in Python. Het plan zet `scoring.py` en `verify_ml_shadow.py`
in `weerbot-modellen/` en laat die een CSV met kolommen `baseline_mu`, `ml_mu`
en `ml_sigma` lezen. Dat bestand bestaat niet en kan ook niet bestaan: het
schaduwlogboek staat in localStorage van de browser en komt nooit bij een
GitHub-actie. Een Python-script zou daar naar een leeg pad kijken. De cijfers
zijn nu berekend waar de gegevens staan.

**De acceptatiedrempels** (n ≥ 45, MAE-winst ≥ 0,05 °C, |bias| ≤ 0,35 °C,
dekking tussen 72 en 88 procent, CRPS beter) staan in `ml_activatie.json`. Als
afspraak, niet als code: de code leest alleen `aan` en `nooit_labels`, want een
drempel die zichzelf afvinkt op een reeks van 45 dagen is geen drempel.

## Wat er niet is overgenomen

**De Diebold-Mariano-toets als activatiepoort.** De richting klopt — twee
voorspellingen vergelijken vraagt om meer dan het verschil in gemiddelde. Maar
de p-waarde van 0,10 die het plan noemt zou hier over 45 waarnemingen per stad
en horizon gaan, met een reeks die op zichzelf autocorreleert. Zo'n toets zegt
dan vooral iets over de aanname en niet over de modellen. De uitweg is niet een
strengere p-waarde maar meer materiaal: het schaduwlogboek loopt nu per horizon
door, en bij een paar honderd dagen per stad-horizon is de vraag zinnig te
stellen. Tot die tijd staan MAE, bias, CRPS en dekking naast elkaar in het
rapport, en dat is eerlijker dan één getal met een sterretje.

**`requirements.txt` en het herschrijven van `zelftest.yml`.** Het voorgestelde
`zelftest.yml` vervangt negen stappen door drie en laat de pariteitstoets van de
rekenkern, `controleer_schil.py`, `compileall` en vier testbestanden vallen. Dat
is een verslechtering. De pinning die het plan wil (`scikit-learn==1.8.*`) staat
al in de twee workflows die hem nodig hebben, en de andere workflows hebben
alleen de standaardbibliotheek nodig; een `requirements.txt` zou daar een
installatiestap toevoegen die niets doet.

**`klim_vandaag.json` in de schil.** Het plan wil hem meecachen. Dat bestand
wordt twee keer per dag herschreven door `klim-dagelijks.yml`. In de schil zou
elke bezoeker de klimwaarden van gisteren zien tot het versienummer toevallig
meegaat, en zou `controleer_schil.py` elke dag omvallen. `modellen.json` staat
er wel in, want die wijzigt wekelijks en staat om die reden buiten de
vingerafdruk.

**`horizonVanDag` uit het plan.** De voorgestelde functie leest `dag.horizon` of
`dag.lead` en valt terug op `arguments[1]` binnen een `forEach`-callback; die
velden bestaan niet en `arguments` is daar de callback-argumenten. De index van
de `forEach` is de horizon, en zo staat het er nu.

**Modularisatie van `index.html`.** Terecht, en het staat sinds 6 augustus al op
punt 5 van de eigen lijst hierboven. Maar niet in dezelfde wijziging als deze:
de pariteitstoets tussen `index.html` en `kalibratie.py` is het enige wat
garandeert dat de app rekent zoals de backtest, en die moet eerst mee verhuizen.
Een verplaatsing zonder die toets is precies het soort stille breuk waar deze
hele natrekking over gaat.

**Backend, monitoring-infrastructuur, modelregister, SLA's.** De review beoordeelt
het project tegen een commerciële norm en concludeert dat het daar niet aan
voldoet. Dat klopt, maar het is geen bevinding: dit is een browser-only app
zonder server, en dat is een keuze, geen tekortkoming. Wat er van die lijst wél
toe doet en goedkoop is — een licentie en een model card — staat er nu.

## Wat hierna moet gebeuren

De schaduwreeks begint opnieuw. Alles wat er nu in `weerbot-ml-schaduw-v1`
staat is gemaakt met de oude invoer en is als vergelijkingsmateriaal onbruikbaar;
de nieuwe sleutel is `weerbot-ml-schaduw-v2` en die begint bij nul op 13 augustus
2026. Zestig dagen is dus zestig dagen vanaf nu, en pas daarna is
`WeerbotKoppel.rapport()` een uitspraak over de modellen in plaats van over hun
invoer.

Eén ding is niet na te rekenen zolang de reeks niet loopt: of de
previous-runs-API dezelfde waarden voor de kómende dagen geeft als voor de
dagen achteraf, waar de training hem op bevraagt. De aanroep is dezelfde en de
code valt netjes terug als het antwoord leeg is, maar dat is een aanname tot het
schaduwlogboek hem bevestigt. Eerste controle daarop: staat er na een dag voor
alle 49 steden een regel in het logboek, dan komt de reeks binnen.

---

# Nagekomen: de zestig dagen waren er al

Toegevoegd op 2026-08-13, bij de vraag of de schaduwperiode van zestig dagen te
versnellen is. Ja — voor de vraag die er werkelijk toe doet, en het antwoord is
minder gunstig dan het schaduwlogboek ooit had laten zien.

## Waarom het kon

`features_alle.csv` bevat 44.668 stad-dagen met een bruikbare p1-reeks én een
waarneming, van november 2023 tot augustus 2026. Dat is per stad zo'n
negenhonderd dagen, tegenover de zestig waar op gewacht werd. De ML-invoer, de
uitkomst en de waarneming staan er allemaal in.

Wat je er niet mee mag doen is `modellen.json` erop nakijken. `refit()` in
`deel9_wekelijks.py` fit op `tr = np.where(isfinite(mm_gem) & isfinite(doel))`,
oftewel op de hele geschiedenis. De modellen zijn dus op precies deze rijen
getraind en erop scoren is in de eigen trainingsdata kijken.

`weerbot-modellen/schaduw_backtest.py` doet het daarom overnieuw: elke zeven
dagen hertrainen op uitsluitend de dagen ervoor, minimaal 180, en de week erna
voorspellen. Dat is de cadans van de echte maandagactie. De referentie is niet
verzonnen maar geleend — `kalibratie.walk_forward` is de walk-forward van de app
zelf en geeft per dag terug wat de rekenkern die dag zou hebben getoond. Beide
kanten worden op dezelfde dagen gescoord, allebei met alleen kennis van
daarvoor.

## Wat eruit komt

34 steden met een eigen ridge, 597 tot 740 evaluatiedagen per stad. Alles in °C.

| | MAE kern | MAE ML | winst |
| --- | --- | --- | --- |
| lead 1 | 0,846 | 0,828 | +0,018 |
| lead 2 | 0,945 | 0,920 | +0,025 |

Dat is een derde van de 0,06 °C die het interne materiaal noemde (0,89 → 0,83).
Bij 12 van de 34 steden is het ML-model op lead 1 gewoon slechter dan de kern.

Tegen de drempels uit `ml_activatie.json` — winst ≥ 0,05 °C, |bias| ≤ 0,35 °C,
dekking tussen 72 en 88 procent:

| stad | winst lead 1 | winst lead 2 |
| --- | --- | --- |
| Singapore | +0,058 | +0,082 |
| Shanghai | +0,050 | +0,071 |
| Chongqing | +0,050 | +0,074 |
| New York | +0,040 | +0,065 |
| Hongkong | +0,036 | +0,057 |

Op beide horizonnen halen alleen **Singapore en Shanghai** het. Op lead 2 komen
Chongqing, New York en Hongkong erbij; op lead 1 blijven die net onder de
drempel.

## Twee steden die er wél uitsprongen, en waarom dat niet telt

Atlanta (+0,111) en Chengdu (+0,124) waren de grootste winnaars — en dat zijn
precies de twee die het niet mogen zijn. Allebei hebben ze variant `ridge_klim`,
en die klim-term komt uit `klim_features.csv`, dat `refit()` uitrekent met
`stagea_gbm_*.pkl`-modellen die op de hele geschiedenis zijn gefit. De
hertraining in de backtest is walk forward, die ene feature niet. Die twee
steden kijken via de klim-term in hun eigen toekomst.

Het is dezelfde val als bij de invoer: het opvallendste resultaat kwam uit de
methode en niet uit het model. `schaduw_backtest.py` merkt `ridge_klim`-steden
daarom af met "haalt ze, maar klim lekt" en telt ze niet mee als geslaagd. Dat
Atlanta en Chengdu in het interne integratieplan al "extra voorzichtig" heetten
is een aardige samenloop, maar de reden is een andere dan daar staat.

## Wat hiermee vervalt en wat er overblijft

Vervalt: zestig dagen wachten om te weten óf deze modellen beter zijn. Dat is nu
bekend, op negenhonderd dagen per stad in plaats van zestig, en het antwoord is
"bij twee van de 34, en bescheiden".

Blijft staan, en dat is een plumbing-vraag van dagen en niet van maanden:

1. Levert `previous-runs-api` de p1-waarden ook voor de kómende dagen? Voor een
   doeldag D is `previous_day1` de verwachting van één dag vóór D. Voor vandaag
   en morgen bestaat die run al, voor overmorgen nog niet. Het is dus goed
   mogelijk dat horizon 2 in de app helemaal geen invoer krijgt — de code slaat
   die dag dan over, wat de juiste uitkomst is, maar het betekent wel dat de
   lead 2-kolom hierboven een backtest is waar geen live tegenhanger bij hoort.
2. Zijn die live waarden gelijk aan wat je achteraf voor dezelfde dag
   terugkrijgt?

Allebei te zien aan het schaduwlogboek: staat er na één dag voor alle 49 steden
een regel, en op welke horizonnen, dan is vraag 1 beantwoord. Vraag 2 vraagt om
een handvol dagen, niet om zestig.

## Wat dit betekent voor activeren

Niet Singapore en Shanghai aanzetten omdat ze de drempel halen. De drempels in
`ml_activatie.json` zijn geschreven voor een live schaduwmeting en deze backtest
is iets anders: hij bewijst dat het model op historische invoer beter is, niet
dat de app die invoer live in dezelfde vorm binnenkrijgt. De volgorde blijft
dus: eerst de plumbing-vraag beantwoorden met het schaduwlogboek, dan de twee
steden aanzetten waarvan nu al vaststaat dat het model deugt.

Wat er wél mee vervalt is het omgekeerde: voor de 12 steden waar ML op
negenhonderd dagen slechter is dan de kern hoeft geen enkele schaduwdag meer te
worden afgewacht. Die kunnen uit de kandidatenlijst.

---

# Activering, en een fout in de horizonafbeelding

Toegevoegd op 2026-08-13, bij het verzoek om de steden met bewezen winst aan te
zetten en de rest twee keer per week na te lopen.

## Wat er eerst nog mis was

Het uitzoeken welke horizon aan mocht, bracht een fout aan het licht in de
koppeling die een paar commits eerder was herzien. `kalibratie.py` voedt de
rekenkern per horizon uit een andere bron — zie `run()`, waar `h == 0` uit `hf`
komt en `h` 1 en 2 uit `fc[(h, dag)]`:

| horizon | rekenkern krijgt | ML-model is getraind op |
| --- | --- | --- |
| 0, vandaag | historical-forecast, de run van vandaag | — |
| 1, morgen | `previous_day1` | `previous_day1` |
| 2, overmorgen | `previous_day2` | — |

`invoerVoor` gaf op alle drie de horizonnen `p1` door. Op horizon 2 hoort dat
`p2` te zijn: dezelfde grootheid een dag verder weg, wat `schaduw_backtest.py`
met `--lead 2` ook meet. En op horizon 0 hoort er niets te gebeuren. Daar heeft
de rekenkern de run van vandaag terwijl het model het met die van gisteren zou
moeten doen; dat is geen afweging tussen twee methodes maar het vervangen van
een verse voorspelling door een oudere.

Hersteld. `reeksVoor` kiest nu per horizon, horizon 0 krijgt geen invoer, en
`"0"` staat in `ml_activatie.json` onder `nooit_horizons` zodat het ook niet per
ongeluk aangezet kan worden. `run2run` bestaat alleen op horizon 1: op horizon 2
zou je `p3` nodig hebben, en die ontbreekt — precies zoals de lead 2-backtest
hem behandelt.

Daarmee vallen backtest en app op elkaar: lead 1 in de backtest is horizon 1 in
de app, lead 2 is horizon 2. Dat was het ontbrekende stuk om de cijfers uit de
vorige sectie te mogen gebruiken voor een activeringsbesluit.

## Wat er aanstaat

| stad | horizon 1 | horizon 2 |
| --- | --- | --- |
| Singapore | +0,058 | +0,082 |
| Shanghai | +0,050 | +0,071 |
| Chongqing | — | +0,074 |
| Hongkong | — | +0,057 |
| New York | — | +0,065 |

Winst in °C tegenover de rekenkern, walk forward over 597 tot 740 dagen. Alle
vijf halen ze op de aangezette horizon ook de bias- en dekkingsdrempels.
Chongqing, Hongkong en New York blijven op horizon 1 net onder de 0,05 en staan
daar dus uit.

Atlanta en Chengdu staan niet in de lijst, hoewel ze de hoogste winst lieten
zien. Hun klim-term lekt; de vorige sectie legt uit waarom.

## De lijst controleert zichzelf

`bot/test_ml.py` leest `ml_activatie.json` en toetst elke ingang tegen
`monitoring/backtest_lead<horizon>.json`: haalt deze stad op deze horizon de
drempels, heeft hij een eigen model, staat de horizon niet op de nooit-lijst,
en lekt de klim-term niet. De zelftest valt dus om zodra er een stad aanstaat
die de cijfers niet draagt — of hij nu met de hand is toegevoegd of stilletjes
is verslechterd.

Dat laatste is de reden dat die toets ook in de tweewekelijkse actie draait, ná
de verse backtest.

## De tweewekelijkse controle

`.github/workflows/ml-controle.yml`, dinsdag en vrijdag 11:07 UTC. Draait de
backtest voor lead 1 en 2 met `--controleer`, wat niet de tabel maar de
verandering meldt:

- een stad die de drempels nu haalt en nog uitstaat, met de cijfers erbij
- een stad die aanstaat en ze niet meer haalt, met de reden erbij

Daarna draait `bot/test_ml.py`, en die valt om bij het tweede geval. De actie
commit `monitoring/` en zet zelf niets aan: aanzetten verandert wat bezoekers
zien en vraagt ook een nieuw schilversienummer, en dat hoort een besluit te
zijn.

Dinsdag is de zinnige run — de hertraining draait maandag 03:17 en schrijft
`features_alle.csv` opnieuw. Vrijdag rekent meestal op dezelfde reeks en zal
hetzelfde zeggen. Dat is geen verspilling maar de prijs van vier minuten voor
het opvangen van een maandag die omviel of pas later landde.

## Wat activeren nog niet is

De backtest bewijst dat deze modellen op historische invoer beter zijn dan de
rekenkern. Hij bewijst niet dat de app die invoer live in dezelfde vorm
binnenkrijgt, en dat is de ene vraag die alleen de live-reeks beantwoordt:
levert `previous-runs-api` `previous_day1` en `previous_day2` ook voor een
doeldag die nog moet komen, en met dezelfde waarden als je achteraf voor die
dag terugkrijgt?

De app faalt hier veilig: zonder bruikbare reeks rekent `invoerVoor` niet en
blijft de rekenkern staan, ook voor een stad die aanstaat. Het risico is dus
niet een verkeerd getal maar een activering die stilzwijgend niets doet. Eerste
controle daarop is het schaduwlogboek: staat er na een dag voor de vijf steden
een regel, en op welke horizonnen.

---

# Uitgezocht: kan horizon 0 wel?

Toegevoegd op 2026-08-13. In de vorige sectie stond dat horizon 0 permanent
uitstaat omdat aanzetten "een verse voorspelling door een oudere zou
vervangen". Dat was een redenering en geen meting. Hier de meting.

## De vraag scherp

Op horizon 0 voedt `kalibratie.py` de rekenkern uit de historical-forecast: de
run van de dag zelf. De ML-modellen zijn op `previous_day1` getraind, de run van
de dag ervoor. De ML-voorspelling is op h0 en h1 daarom precies hetzelfde getal
— zelfde invoer, zelfde model — en het verschil zit volledig in wat de rekenkern
er extra bij krijgt.

ML wint op h0 dus alleen als zijn winst op h1 groter is dan wat die verse run de
rekenkern oplevert. Noem dat de versheidspremie. Die is te meten zonder nieuwe
gegevens: `app_params.js` heeft per stad de walk-forward MAE van de rekenkern op
alle drie de horizonnen, uit dezelfde kalibratieronde, dus het verschil h1 − h0
is een zuiver binnen-run-verschil zonder venstereffect.

## De uitkomst

| | °C |
| --- | --- |
| MAE rekenkern h0 (vandaag) | 0,634 |
| MAE rekenkern h1 (morgen) | 0,856 |
| versheidspremie h1 − h0 | **+0,223** |
| ML-winst op h1 (backtest) | +0,018 |

De premie is positief in alle 47 steden, van +0,040 (Wellington) tot +0,450
(Toronto). De verse run is dus overal winst, en gemiddeld ruim twaalf keer zo
veel waard als wat het ML-model erbovenop legt.

Per stad afgezet tegen de ML-winst: **0 van de 33 steden** zou op horizon 0 van
de rekenkern winnen. Het beste geval is Wellington, en dat verliest nog steeds
met 0,046 °C; gemiddeld is het verlies 0,216 °C. Singapore en Shanghai, die op
h1 en h2 wel aanstaan, verliezen op h0 met 0,082 en 0,120.

IJking van de twee bronnen: de MAE van de rekenkern in mijn backtest wijkt
gemiddeld −0,037 °C (sd 0,103) af van `app_params.js` op h1, over 34 steden en
ondanks vensters van 737 tegen 181 dagen. Dat is klein genoeg om de premie van
de ene bron naar de andere over te dragen.

De intraday-conditionering telt hier niet mee, en dat is met opzet. Die zit in
`polymarkt.js`, werkt op de kansen per vak en niet op `mu`, en wordt op beide
kanten gelijk toegepast. Ze maakt h0 als geheel beter maar verandert niets aan
de vergelijking tussen kern en ML.

## Zou een model dat wél op lead 0 getraind is het winnen?

Dat is de constructieve versie van de vraag, en de eerlijke schatting is: nee,
niet genoeg om de drempel te halen.

De ML-winst schaalt mee met de foutgrootte — er valt meer te corrigeren waar de
fout groter is. Over de twee gemeten leads:

| | MAE rekenkern | ML-winst | aandeel |
| --- | --- | --- | --- |
| lead 1 | 0,846 | +0,018 | 2,1% |
| lead 2 | 0,945 | +0,025 | 2,6% |

Het aandeel loopt op met de fout. Op h0 is de fout 0,634, een kwart kleiner dan
op h1. Twee manieren om door te trekken: een regressie van de winst op de
kern-MAE over alle 68 stad-leads geeft −0,007 °C (R² is met 0,18 zwak, dus dat
cijfer is grof), en een constant aandeel van 2,2 procent geeft +0,014 °C. De
drempel om aan te mogen is +0,050. Beide schattingen komen daar ver onder, en
omdat het aandeel juist dáált bij een kleinere fout is +0,014 nog aan de ruime
kant.

Daar staat een reële prijs tegenover. Een h0-model vraagt `p0_*`-kolommen uit de
historical-forecast-API, en dat betekent een backfill over ongeveer duizend
dagen maal 49 steden voordat er ook maar één model op te trainen valt.
`kalibratie.py` heeft de aanroep al (`haal_hist_forecast`), maar
`features_alle.csv` bewaart hem niet. Op deze schatting is dat niet de moeite.

Eén ding zou het antwoord wél kunnen kantelen, en dat is een andere feature dan
een verse run: de temperatuur die vandaag al gemeten is. `bot/waarneming.py`
gebruikt die nu alleen om de kansen af te kappen, niet in de puntvoorspelling.
Een h0-model met die meting erin heeft informatie die de rekenkern in zijn
puntvoorspelling niet heeft. Dat is een ander voorstel dan "train hetzelfde
model op lead 0" en het is niet met de gegevens in deze repository te schatten.

## Wat er in code is vastgelegd

`schaduw_backtest.py` heeft er `versheidspremie()` bij, en de lead 1-run drukt
het horizon 0-oordeel voortaan af. `bot/test_ml.py` toetst twee dingen: dat de
premie in elke stad positief is, en dat geen enkele stad op h0 van de rekenkern
zou winnen. Zou de wekelijkse kalibratie dat ooit omdraaien — een stad waar de
verse run niets meer oplevert — dan valt de zelftest om in plaats van dat
`nooit_horizons` stilzwijgend achterhaald raakt.

De conclusie blijft dus dezelfde als in de vorige sectie, maar staat nu op
cijfers en beweegt mee.

---

# Wat de meting van vandaag als feature zou opleveren

Toegevoegd op 2026-08-13. In de vorige sectie stond dat één ding het
horizon 0-antwoord zou kunnen kantelen: de temperatuur die vandaag al gemeten
is. Hier wat dat waard is.

## Eerst waar hij al zit, en waar niet

De app conditioneert al op `m`, de hoogste meting van vandaag tot nu toe, maar
uitsluitend in de kansen per temperatuurvak. `waarneming.cdf` kapt de verdeling
af op `m` en `waarneming.conditioneer` krimpt wat er overblijft. Dat is
paragraaf 7 van README.md en het werkt.

Wat er niet mee gebeurt is `d.verwachting`. Het getal op het scherm — en dus ook
de MAE waarop de rekenkern en de ML-modellen worden afgerekend — weet van niets.
Op horizon 0 voorspelt de app nog steeds alsof de dag moet beginnen, ook al
staat er om drie uur 's middags al een cijfer op de meter.

Dat maakt het meteen een andere vraag dan "helpt een ML-model op h0". De meting
is geen geleerde correctie maar een natuurkundige ondergrens: het dagmaximum kan
niet lager uitvallen dan wat er al staat. Wie die ondergrens gebruikt heeft geen
model nodig.

## De orde van grootte

Uit de restfactorcurve in `polymarkt.js` volgt hoeveel onzekerheid er per lokaal
uur nog over is. Bij een MAE van 0,634 °C op h0 geeft dat, als de MAE evenredig
met de resterende spreiding meeschaalt:

| lokaal uur | w ruw (markt) | winst | w app (gedempt) | winst |
| --- | --- | --- | --- | --- |
| tot 10 | 1,00 | +0,000 | 1,00 | +0,000 |
| 11 | 0,81 | +0,120 | 0,92 | +0,052 |
| 13 | 0,68 | +0,203 | 0,83 | +0,106 |
| 14 | 0,59 | +0,260 | 0,76 | +0,153 |
| 15 | 0,38 | +0,393 | 0,55 | +0,289 |
| 16 | 0,26 | +0,469 | 0,40 | +0,384 |
| 18 | 0,12 | +0,558 | 0,19 | +0,511 |
| 20 | 0,06 | +0,596 | 0,10 | +0,571 |

Ter vergelijking: het ML-model levert op h1 +0,018 °C en de drempel om aan te
mogen is +0,050. Zelfs de voorzichtige kolom is vanaf elf uur 's ochtends al
groter dan de drempel en loopt op tot dertig keer de ML-winst.

Dat is de goede orde van grootte om te onthouden, maar het is nadrukkelijk geen
meting. Drie redenen tot voorzichtigheid, en de eerste twee staan al in de kop
van `bot/waarneming.py`:

1. De curve is de onzekerheid van de **markt**, teruggerekend uit de entropie
   van de vakprijzen. De markt is scherper dan wij om meer redenen dan alleen
   de meting.
2. Daarom staat er een demping overheen. Die is gekozen voor het samenspel met
   de afkapping in de kansen, niet voor een puntvoorspelling.
3. "MAE schaalt met de spreiding" gaat uit van een symmetrische restverdeling,
   en de afkapping maakt hem juist scheef.

## Waarom dit niet alsnog voor ML pleit

Als de meting eenmaal in de puntvoorspelling zit, is hij er voor de rekenkern
net zo goed als voor een ML-model. De +0,223 °C versheidspremie waarmee de kern
op h0 wint blijft staan, en daar komt voor beide kanten hetzelfde bedrag
bovenop. Het rangordevraagstuk verandert dus niet: `nooit_horizons: ["0"]`
blijft goed.

Wat er wél verandert is de rangorde van de openstaande verbeteringen. De meting
in de puntvoorspelling stoppen is op h0 een orde van grootte meer waard dan alle
ML-winst op h1 en h2 bij elkaar, en het kost geen model — alleen `max(m, mu)`,
of netter `E[max(m, R)]`.

## Wat er nu is gebouwd

`bot/meet_meting.py`. Het IEM-archief heeft de uurlijkse METAR's van jaren
terug, dus de curve hoeft niet van de markt geleend te worden: hij is op de
eigen reeks te meten. Het script haalt per tijdzone de uurreeksen op,
reconstrueert per stad-dag wat er om elk lokaal uur op de meter stond, en legt
dat naast de walk-forward van de rekenkern zelf (`kalibratie.walk_forward`, net
als `schaduw_backtest.py`). Per uur komt eruit:

- **kaal** — MAE van de rekenkern zonder meting
- **ondergrens** — MAE van `max(m, mu)`: alleen de natuurkundige ondergrens,
  zonder restfactor en dus zonder geleende curve. Dit is de eerlijkste
  ondergrens van wat de meting waard is, en dit deel is gratis.
- **met krimp** — MAE van `E[max(m, R)]` met `R` uit `waarneming.conditioneer`

Het verschil tussen de tweede en de derde kolom is precies wat de krimp bovenop
de afkapping doet, en dus waar de dubbeltelling zit waar de demping voor bedoeld
is. Daarnaast komt de restfactor zelf eruit, gemeten op de eigen reeks, om naast
`W_REST_MAX` te leggen — wat de kop van `waarneming.py` al als openstaand punt
noemt.

Draaien kan hier niet: Open-Meteo én IEM zijn vanuit deze omgeving een
beleidsblokkade. `.github/workflows/meting-studie.yml` start hem met de hand;
daar is het netwerk er wel. De rekenkant draait wel offline en zit in de
zelftest: `E[max(m,R)]` tegen zijn grensgevallen, de loopmax, en de
uurontleding op hetzelfde IEM-voorbeeld als `bot/test_waarneming.py`, met de
eis dat de dagmax eruit gelijk is aan die van `waarneming.ontleed_iem`.

## Wat er daarna te besluiten valt

Niet meteen de getoonde verwachting aanpassen. Dat is het hoofdgetal van de app
en de meting kan hem alleen omhoog duwen; wie dat op de verkeerde curve doet
maakt het beeld structureel te warm. Eerst de studie draaien, dan kijken of de
gratis kolom — `max(m, mu)`, zonder enige kalibratie — het al doet. Als die het
doet is dat de wijziging: goedkoop, uitlegbaar, en zonder een curve die van de
markt geleend is.
