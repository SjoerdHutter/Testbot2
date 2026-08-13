# Weerbot 2

Verbeterde versie van [TestBot](https://github.com/SjoerdHutter/TestBot). De app
voorspelt de dagtemperatuur in 49 steden uit een ensemble van vijf modelsystemen
(ECMWF IFS en AIFS, NCEP GEFS, ICON, GEM via Open‑Meteo), corrigeert die met
gekalibreerde parameters, en controleert zichzelf elke dag tegen de
stationsmeting waar ook de weddenschappen op afrekenen.

Alles draait in de browser; er is geen server en er gaat niets naar buiten
behalve leesverzoeken naar Open‑Meteo, IEM (METAR), de NWS en Polymarket. De
python-kant leest daarnaast de luchthavenverwachting (TAF) en, voor Tokio en
Singapore, een fijnmaziger stationsreeks bij het JMA en NEA.

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

Die kolom leest niet altijd als winst. Binnen `MARKT_VENSTER_UREN` van de
**verwachte piek** en boven `MARKT_VERSCHIL_PP` verschil komt er
`market_disagrees` bij te staan, en het tabblad zet de edge dan oranje met een ⚠
en de reden als tooltip. Beide drempels zijn gemeten op 230 afgerekende
stad-dagen uit `signalen.csv` (Brier-score per vak, lager is beter):

| venster voor de piek | model | markt | |
| --- | --- | --- | --- |
| meer dan 24u | 0,0655 | 0,0599 | markt 9% beter |
| 12 tot 24u | 0,0673 | 0,0592 | markt 12% beter |
| 6 tot 12u | 0,0666 | 0,0505 | markt 24% beter |
| 0 tot 6u | 0,0640 | 0,0370 | markt 42% beter |
| piek voorbij | 0,0670 | 0,0098 | markt 85% beter |

Kijk vooral naar de modelkolom: die verbetert nauwelijks als de dag vordert,
terwijl de markt er een orde van grootte op vooruitgaat. Logisch, want de markt
ziet de al gemeten temperatuur van die dag en het model voorspelt nog steeds.
Bij twaalf uur voor de piek verdubbelt het voordeel van de markt, en daar ligt
de grens. Een groot verschil in dat venster is dus veel vaker het model dat
ernaast zit dan een edge die te pakken valt: bij Shanghai op 9 augustus stond er
+82pp edge terwijl de markt op 92% zat en gelijk kreeg.

Het piekuur staat niet in `signalen.csv`, dus voor de meting is 15:00 lokaal
aangenomen. De uitkomst hangt daar niet aan: over aangenomen piekuren van 13:00
tot 17:00 blijft het patroon 8-9% / 11-14% / 20-28% / 31-62% / 73-100%.

Ook de 20pp is gemeten en niet gekozen. Binnen twaalf uur voor de piek, naar de
grootte van het meningsverschil: bij alle vakken zit de markt 44% dichter bij de
uitkomst, boven 10pp 57%, boven 20pp 67%, boven 40pp 85%. Hoe groter het
verschil, hoe vaker de markt gelijk had. Lager dan 20pp zou verdedigbaar zijn,
maar dan markeert de vlag een op de drie vakken en zegt hij niets meer; bij 20pp
is het ongeveer een op de acht.

De vlag raakt het stoplicht niet aan. Dat blijft in graden staan, zoals bedoeld.

Kijk vooral naar die modelkolom, want die is inmiddels aangepakt: punt 7 hieronder
conditioneert de kansen op de temperatuur die vandaag al gemeten is, en dat is
precies wat de markt in dat venster wél had en het model niet. De tabel hierboven
is dus van vóór die wijziging. De vlag blijft voorlopig staan zoals hij is —
of het gat werkelijk kleiner is geworden hoort uit de reeks te blijken en niet
uit de verwachting dat het zou moeten.

Valt de ensemblefetch van een stad om, dan volgen er twee herkansingen met tien
en twintig seconden pauze. Zonder die herkansingen kost één hapering in de
verbinding het hele modelbeeld van een stad, en staat elke positie daar die run
zonder licht; in de eerste vijf runs gebeurde dat twee keer, op twee
verschillende steden, allebei met een TLS-handshake die niet rond kwam. Blijft
het misgaan, dan blijft de positie staan met `light: "unknown"` en de reden
erbij — een stad stilletjes laten verdwijnen is erger dan een gat dat zichzelf
meldt.

De klok in het tabblad telt af naar het **verwachte warmste moment**
(`hours_to_peak`), niet naar de sluiting. De markt sluit formeel om middernacht,
maar de uitslag ligt er zodra het dagmaximum gevallen is: bij Busan rekende
Polymarket af terwijl er lokaal nog bijna drie uur op de klok stonden, en die
uren telden mee alsof er nog iets kon veranderen. Is de piek voorbij, dan staat
er "piek geweest" in plaats van een aftellend getal.

Het piekuur komt uit de uurcurve van dezelfde vijf modelsystemen als de app:
`pieken_uit()` in `portfolio.py` is het spiegelbeeld van `piekenUit` in
`index.html`, inclusief de eis van minstens zes uurwaarden per dag. Dat kost één
extra verzoek per stad, dat meteen drie dagen dekt. Ontbreekt die curve, dan valt
het tabblad terug op de sluiting en zet er een sterretje bij.

`hours_to_close` blijft in de JSON staan en wordt nog steeds gerekend als
middernacht aan het einde van de doeldag in de lokale tijdzone van de stad, met
`zoneinfo` — niet met een vaste UTC-offset, want die klopt maar in een deel van
het jaar. De markt-oneens-vlag telt inmiddels ook naar de piek en niet meer naar
die sluiting; de drempel is opnieuw gemeten met het piekmoment als nulpunt.

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

### 7. Conditioneren op wat er vandaag al gemeten is

De Brier-tabel hierboven zegt het onomwonden: naarmate de dag vordert verbetert
het model nauwelijks terwijl de markt zijn fout meer dan halveert. De reden staat
er ook bij — binnen twaalf uur ziet de markt de al gemeten temperatuur van die
dag, en voorspelde het model nog steeds alsof de dag moest beginnen.

Dat is geen modelfout maar een ontbrekende voorwaarde. Noem `m` de hoogste meting
van vandaag tot nu toe op het station waarop de markt afrekent, en `R` het
maximum over de uren die nog komen. Het dagmaximum is dan `T = max(m, R)`, en
daaruit volgt de verdelingsfunctie meteen:

```
F(t) = 0                        voor t < m
F(t) = Phi((t − mu_R) / sig_R)  daarboven
```

Die ene knik doet al het werk. Een vak dat helemaal onder `m` ligt krijgt kans
nul — het is niet onwaarschijnlijk meer, het is onmogelijk. En het vak waar `m`
in valt krijgt er vanzelf de puntmassa `Phi((m − mu_R)/sig_R)` bij: precies de
kans dat de piek al geweest is. Er is geen aparte tak voor nodig. Bij de
laagstereeks staat alles op zijn kop: `T = min(m, R)`.

Twee getallen beschrijven `R`, allebei uit dezelfde restfactor `w`:

```
mu_R = mu · w + m · (1 − w)      sig_R = max(sig · w, 0,05)
```

Bij `w` = 1 is er nog een hele dag te gaan en doet `m` alleen dienst als
ondergrens; bij `w` → 0 is de dag gelopen en komt alle massa op het vak van `m`.

**Waar die restfactor vandaan komt.** Niet uit een aanname over het warmste uur,
maar uit `logs/signalen.csv`. Voor elke lead-0 reeks is per logmoment de
marktverdeling over de vakken bekend, en de entropie daarvan is terug te rekenen
naar een sigma in vakbreedtes. Uitgezet tegen het lokale uur:

| lokaal uur | 0–10 | 11 | 12 | 13 | 14 | 15 | 16 | 17+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sigma markt | 0,774 | 0,625 | 0,561 | 0,528 | 0,454 | 0,291 | 0,201 | 0,18 |
| verhouding | 1,00 | 0,81 | 0,72 | 0,68 | 0,59 | 0,38 | 0,26 | 0,24 |

`bot/kalibreer_restfactor.py` rekent die tabel opnieuw uit en markeert zelf welke
uren onbruikbaar zijn: een Polymarket-prijs loopt niet verder dan 0,9995, dus de
entropie van een afgerekende reeks komt niet onder een bodem die niets met het
weer te maken heeft. Die uren zijn gecensureerd, niet gemeten, en in
`polymarkt.js` ingevuld door de daling ervoor door te trekken.

Er gaat een demping overheen, `w = ruw · (1 + 0,7 · (1 − ruw))`. De curve is
namelijk de onzekerheid van de *markt*, en die is scherper dan de onze om meer
redenen dan de meting alleen; bovendien zit de ondergrens `m` al in de
marktprijzen, dus de volle curve op sigma leggen én daarna ook afkappen telt
dubbel. De demping is het grootst in de overgangsuren, waar de afkapping het
meeste doet, en valt 's avonds weg — dan is de restfactor geen gecensureerde
marktmeting meer maar natuurkunde. Omdat `ruw` tussen 0 en 1 ligt geldt
`w ≥ ruw` altijd: onze spreiding is per constructie nooit krapper dan die van de
markt. `bot/test_waarneming.py` dwingt dat af.

**De meting zelf** komt van hetzelfde station waarop de markt afrekent, uit
dezelfde uurlijkse METAR-reeks als de dagelijkse controle. Dat het uurlijks is
maakt `m` een *ondergrens*, en die fout staat de goede kant op: onderschatten
kapt te weinig af, overschatten zou een vak wegstrepen dat nog kon vallen. Er
wordt dus niets bij opgeteld. De restfactor rekent met het laatste meetmoment en
niet met de klok, zodat een station dat 's middags uitvalt de band niet ten
onrechte dichtknijpt — de ondergrens blijft dan gewoon staan, alleen ouder.

Zonder waarneming rekent alles exact zoals daarvoor. Dat is dezelfde code, niet
een gelijkwaardige, en het is de eerste toets in `bot/test_waarneming.py`; de
pariteitstest in `test_kern.py` dekt nu ook de geconditioneerde tak.

```
python3 bot/waarneming.py                 wat er nu gemeten is, alle steden
python3 bot/waarneming.py --toon-curve    de restfactortabel
python3 bot/kalibreer_restfactor.py       de curve opnieuw uit het logboek
```

### 8. Tokio en Singapore fijnmaziger meten

Dat zijn precies de twee steden die `high_uncertainty` dragen. Een deel daarvan
is bemonsteringsruis: METAR is één meting per uur, vaak in hele graden, en het
echte dagmaximum valt zelden op het hele uur. Die reeks onderschat hem dus
structureel, terwijl Polymarket er wel op afrekent.

Voor de Amerikaanse steden loste `kalibratie.py` dat al op met de 1-minuut
ASOS-reeks in `verrijk_1min`. Die reeks bestaat alleen voor de VS — ASOS is een
Amerikaans netwerk. `bot/fijnmeting.py` levert drie alternatieven:

| bron | wat | sleutel | staat aan voor |
| --- | --- | --- | --- |
| `hfmetar` | de MADIS-stroom: hetzelfde IEM-eindpunt met `report_type=1` erbij | nee | niemand, tot de dekking het uitwijst |
| `amedas` | JMA, tien-minutenwaarden | nee | TYO |
| `nea` | data.gov.sg, ongeveer per minuut | nee | SIN |
| `fmi` | FMI, tien-minutenwaarden | nee | niemand |
| `knmi` | KNMI, het officiële dagcijfer | nee | niemand |
| `kma` | KMA ASOS, uurwaarden | `KMA_SLEUTEL` | niemand |

De eerste is verreweg de goedkoopste: hetzelfde verzoek aan hetzelfde archief.
Zit een station in MADIS, dan komen er vijf- of twintigminutenwaarden terug in
plaats van één melding per uur; zit het er niet in, dan verandert er niets en valt
de verfijning vanzelf af op de eis van zestig metingen per dag. Er hoeft dus
nergens een lijst met deelnemers bijgehouden te worden.

**Fijner is niet vanzelf beter.** De reeks die je wilt is die waarop *afgerekend*
wordt, niet de fijnste die bestaat — daarom staat er bij `report_type=3` in
`weer.py` dat het de reeks is die Wunderground toont. Voor de Amerikaanse
stations lopen die samen (ASOS rekent het dagmaximum uit vijfsecondegemiddelden
en dat is wat de NWS publiceert), buiten de VS hangt het van de nationale dienst
af. En voor de ondergrens uit punt 7 telt het dubbel: `m` te laag kapt te weinig
af en is onschuldig, `m` te hoog streept een vak weg dat nog kon vallen.

Daarom staat `fijn` per stad uit tot iemand gemeten heeft dat het klopt.
`--hfmetar-dekking` doet die meting: twee aanroepen per tijdzone over dezelfde
afgeronde dagen, en per station het aantal meldingen per dag naast elkaar plus
hoeveel het dagmaximum omhoog gaat. Stations komen eruit als *kandidaat*, *wel
fijner maar zonder effect*, *alleen uurlijks* of *geen data*.

Waar het om gaat: op welk raster ligt de reeks nu? Gemeten in je eigen historie
staan LON en MUC al op 0,1 °C en acht Aziatische steden op 0,5 °C; de elf
Amerikaanse staan op 1 °F. De 23 steden op hele graden zijn de doelgroep, en die
hebben allemaal een actieve markt — WLG, SEL, MIL, PAR en SZX staan bovenaan op
volume.

**Waarom de lijst niet langer is.** Van die 23 publiceert het merendeel van de
nationale diensten niets bruikbaars: China, Nieuw-Zeeland, de Filipijnen,
Pakistan, India en Saoedi-Arabië hebben geen open waarnemingsAPI, en Frankrijk,
Israël en Korea vragen een sleutel. Canada publiceert wel open, maar als losse
XML-bestanden per minuut per station — honderden verzoeken per dag, geen
begaanbare weg. `kma` staat er als patroon voor de sleutelgevallen: de sleutel
komt uit een omgevingsvariabele, dus de repo blijft secret-vrij en zonder
sleutel doet de bron niets. Voor de rest is `hfmetar` de enige route, en of die
dekking geeft is een empirische vraag.

**Twee soorten bron.** De meeste zijn een fijnere *bemonstering* van hetzelfde
station. `knmi` is iets anders: het officiële dagcijfer, door het KNMI zelf
afgeleid uit de volledige reeks — voor Schiphol precies wat de 1-minuut
ASOS-reeks voor de Amerikaanse velden is. Maar het komt pas de volgende ochtend
beschikbaar, dus het helpt de wekelijkse kalibratie en niet de ondergrens van
vandaag.

Verfijnen, niet vervangen. Het uitgangspunt blijft METAR; de fijne reeks mag een
dagmaximum alleen omhoog bijstellen en een dagminimum alleen omlaag, bij minstens
zestig metingen en binnen vier graden — dezelfde bewaking als `verrijk_1min`.
Valt de bron om, dan staat er het oude cijfer en nooit een verzonnen cijfer. Het
station wordt op afstand tot de stad gezocht in de stationstabel die allebei de
bronnen zelf publiceren, in plaats van als vast nummer in de code: een overgetypt
stationsnummer meet anders jarenlang de verkeerde stad zonder dat iemand het
merkt.

De verfijning telt twee keer: in de wekelijkse kalibratie, en op de ondergrens
van vandaag. Daar telt hij het hardst, want die grens kapt de kansen af.

`high_uncertainty` blijft voorlopig op beide steden staan. De bemonsteringsruis
gaat hiermee weg, maar de andere helft van dat vlaggetje is een raster-tegen-
stationprobleem, en of dát kleiner wordt is pas te zien na een hertraining met de
verfijnde waarnemingen.

```
python3 bot/fijnmeting.py --bronnen              wat er is en wat aanstaat
python3 bot/fijnmeting.py --dekking              elke bron tegen het METAR,
                                                 ook de bronnen die uitstaan
python3 bot/fijnmeting.py --hfmetar-dekking      welke stations sub-uurlijks
                                                 melden, en wat het toevoegt
python3 bot/fijnmeting.py --stad TYO --dagen 7   beide reeksen naast elkaar
```

`--dekking` is de poort: hij toetst ook bronnen die nog uitstaan, en dat is
precies wat je wilt weten voordat je `fijn` in `weer.STEDEN` zet. Per stad en
bron komt eruit of hij reageert, hoeveel metingen per dag, hoeveel het
dagmaximum omhoog gaat, en een oordeel — *KANDIDAAT*, *voegt niets toe*, *te
dun*, *geen sleutel* of *geen data*.

### 9. Hoeveel je zou inzetten

Strategie A zei wélke vakjes, nooit hoevéél. `bot/inzet.py` rekent dat uit met
fractionele Kelly en zet er plafonds omheen: 2% van het bankroll per positie, 20%
totale blootstelling, twintig posities, en een dagverliesstop van 5%.

Kelly is alleen optimaal als de kans klopt, dus staat de vraag voorop of de
modelkans überhaupt beter is dan de marktprijs. Die is te beantwoorden met
`logs/signalen.csv`. Met de mengfactor

```
logit(p) = logit(prijs) + λ · (logit(modelkans) − logit(prijs))
```

komt de best passende λ op **nul** uit, in elk venster, met een 95%
bootstrap-interval dat nul ruim omvat:

| venster | n | reeksen | λ* | 95%-interval | brier λ* | brier markt | brier model |
| --- | --- | --- | --- | --- | --- | --- | --- |
| meer dan 24u | 6655 | 123 | +0,046 | [−0,15, +0,34] | 0,0595 | 0,0595 | 0,0656 |
| 12 tot 24u | 3113 | 143 | −0,099 | [−0,23, +0,12] | 0,0501 | 0,0503 | 0,0661 |
| minder dan 12u | 1518 | 110 | +0,034 | [−0,17, +0,26] | 0,0306 | 0,0306 | 0,0627 |

De Brier-score van de best passende meng is tot in vier decimalen gelijk aan die
van de kale marktprijs. Het verschil tussen onze kans en de prijs voorspelt in
deze steekproef dus niets.

Twee dingen aan die meting die er toe doen. De bootstrap trekt **hele reeksen** en
geen losse regels — elf vakken van dezelfde stad-dag zijn één waarneming, geen
elf, en zonder die clustering komt er een interval uit dat een factor drie te smal
is. En momenten waarop de markt al had afgerekend tellen niet mee: Polymarket
schiet naar 0,0005 of 0,9995 zodra het dagcijfer binnen is terwijl het logboek
doortikt, en zulke regels zijn geen voorspelling maar een uitslag. Dat haalde in
het laatste venster bijna twee derde van de regels weg.

`LAMBDA` staat daarom op 0,30 — de bovenkant van wat het interval toelaat, niet
op 1. En niet op nul, want dan valt er nooit meer iets te meten. De inzetten die
daaruit komen zijn klein, en dat is geen voorzichtigheid die erin gedraaid is
maar wat de meting zegt.

Wat de uitkomst nog kan kantelen: de steekproef is ruim honderd onafhankelijke
reeksen, en **de conditionering uit punt 7 zit er niet in** — alle gelogde kansen
zijn van vóór die wijziging, terwijl juist die het gat in het laatste venster
dichtte. `--meet` draait de som opnieuw.

```
python3 bot/inzet.py --meet             de mengfactor uit het logboek
python3 bot/inzet.py --bankroll 500     de inzetten bij de huidige stand
```

Er wordt niets geplaatst en niets verkocht.

### 10. TAF als bevestigingslaag

Een TAF is de luchthavenverwachting voor hetzelfde vliegveld waar de markt op
afrekent, met een horizon van 24 tot 30 uur — precies het koopvenster van
strategie A, en een oordeel dat niet uit ons eigen ensemble komt. De TX- en
TN-groepen (`TX24/1015Z`) zijn een rechtstreekse voorspelling van het dagcijfer.

Amerikaanse TAF's dragen die groepen meestal niet. Dat komt goed uit: de elf
Amerikaanse steden hebben de NWS-bijmenging al, de achtendertig daarbuiten hadden
niets. De twee lagen vullen elkaar dus aan.

`TAF_GEWICHT` staat op nul. De laag logt vanaf nu in `logs/taf_log.csv` en
verandert geen enkel cijfer. `kalibratie.leer_taf` leert het gewicht per horizon
zodra er veertig gematchte dagen zijn, gekrompen richting nul in plaats van
richting 0,25 zoals `leer_nws`: bij de NWS was er reden om aan te nemen dat de
verwachting iets toevoegt, bij de TAF is dat juist de vraag, en een prior van
0,25 legt het antwoord er half in. Dat is dezelfde les als bij de inzetregel.

```
python3 bot/taf.py             loggen
python3 bot/taf.py --dekking   welke stations TX/TN meesturen
```

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

In `logs/` staan vijf bestanden, bijgewerkt door twee acties met elk hun eigen
bestanden:

* `.github/workflows/signalen-log.yml` draait `bot/logger.py`,
  `bot/signalen.py` en `bot/taf.py` **vier keer per dag** en commit
  `ensemble_log.csv`, `nws_log.csv`, `signalen.csv` en `taf_log.csv`. Vier keer
  is genoeg omdat ECMWF en GFS zelf elke zes uur draaien.
* `.github/workflows/portefeuille.yml` draait `bot/signalen.py --portfolio`
  **elk uur** en commit `portfolio.json` en `logs/portfolio_history.csv`. Die
  redenering over modelrondes geldt daar niet: open posities veranderen wanneer
  er gehandeld wordt en de biedprijzen bewegen de hele dag. Op vier keer per dag
  bleef een verse transactie tot bijna acht uur onzichtbaar. Het kan ook
  goedkoop — die stap kost 70 seconden tegenover zeven en negen minuten voor de
  twee logboeken hierboven.

De twee bestandsverzamelingen overlappen niet. Dat is geen toeval maar de reden
dat ze los staan: zo kan een `git pull --rebase` tussen de twee acties nooit op
hetzelfde bestand botsen. Ze horen in de repo thuis: op deze reeksen wordt later
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

De regels van vóór de spreidingskolommen zijn met `bot/migratie_logkoppen.py`
aangevuld met lege velden, zodat het bestand rechthoekig is en `csv.DictReader`
in `kalibratie.py` geen ontbrekende sleutels tegenkomt. Datzelfde script vult
`logs/signalen.csv` aan als daar kolommen bij komen. Het mag opnieuw gedraaid
worden; staat de nieuwe kop er al, dan gebeurt er niets.

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
| `uren_tot_sluiting` | uren tot middernacht ná `doel_datum` in de tijdzone van de stad, twee decimalen; dit is de klok waarop het koopvenster van strategie A loopt |
| `einde_api` | het onbewerkte `endDate` uit de Gamma-API, zodat later te toetsen is of Polymarket de handel daar werkelijk stopt |

Over die laatste twee: `endDate` staat voor élke stad op 12:00 UTC van de
doeldag. Dat is alleen voor Wellington het einde van de lokale dag; voor
Amsterdam scheelt het 10 uur, voor New York 16 en voor San Francisco 19. Op die
klok zou de tijdpoort van strategie A per stad op een ander werkelijk moment
staan, en zou het logboek niet te vergelijken zijn met handmatig afgewikkelde
posities, die op het einde van de lokale dag zijn gemeten. `uren_tot_sluiting`
rekent daarom tot middernacht lokaal, met de tijdzone die al in `bot/weer.py`
`STEDEN` staat. `einde_api` gaat mee zodat de aanname zelf toetsbaar blijft.

Beide kolommen zijn achteraan bijgeplakt en staan leeg voor de regels van vóór
deze wijziging; die zijn niet nageschat. Van die oudere regels is `strat_a_signaal`
op de `endDate`-klok gerekend en dus alleen bruikbaar voor Wellington.

Het koopvenster liep van 36 tot 12 uur voor sluiting en loopt sinds 10 augustus
tot 24 uur. De strategie leunt erop dat het model de markt verslaat, en de
Brier-cijfers hierboven laten zien dat dat binnen een etmaal ophoudt: op meer
dan 24 uur zit het model er 7% naast, tussen 12 en 24 uur al 22%. De gemarkeerde
signalen wijzen dezelfde kant op — 87 signalen boven de 24 uur gaven +3,2%
rendement, de 28 daaronder −6,0% — maar op 28 waarnemingen is dat binnen de
ruis. De keuze rust op de Brier-cijfers, niet op die 28 trades. Regels van vóór
10 augustus in dit logboek zijn dus met het oude venster gemarkeerd.

Daarachter staan de vijf kolommen van de conditionering op de meting van
vandaag:

| kolom | wat |
| --- | --- |
| `waarneming` | de hoogste (of laagste) temperatuur die op het moment van loggen die dag al gemeten was, in de eenheid van de markt; leeg op lead 1 en 2 en bij een gemist station |
| `waarneming_uur` | het lokale tijdstip van de laatste meting waar de restfactor mee gerekend heeft — niet de klok, zie punt 7 |
| `waarneming_n` | het aantal metingen waarop die waarde rust |
| `restfactor` | de `w` die daaruit volgde |
| `model_kans_kaal` | de kans zonder conditionering. Blijft erin staan omdat er zonder die kolom achteraf niet te meten valt of de conditionering iets opleverde: dan is er maar één getal en geen vergelijking |

Ook deze vijf zijn achteraan bijgeplakt, ná `uren_tot_sluiting` en `einde_api`
omdat die twee al in het logboek op schijf stonden. Oudere regels zijn met
`bot/migratie_logkoppen.py` aangevuld met lege velden, zodat het bestand
rechthoekig blijft. `model_kans_kaal` is daar met opzet leeg gelaten en niet uit
`model_kans` overgeschreven: leeg betekent "van vóór de conditionering", en dat
onderscheid moet blijven staan.

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

### `logs/taf_log.csv`

De TX- en TN-groepen uit de luchthavenverwachting, één regel per stad en doeldag.
Kolommen: `gelogd_utc`, `key`, `station`, `doel_datum`, `lead`, `tx_c`,
`tx_uur_utc`, `tn_c`, `tn_uur_utc`, `geldig_van`, `geldig_tot`, `piekgroep`,
`ruw`. Vanaf 40 gematchte dagen leert `kalibratie.leer_taf` hier het
bijmenggewicht per horizon uit; tot die tijd is het 0 en verandert deze reeks
geen enkel cijfer. `piekgroep` is de FM/BECMG/TEMPO-groep die over het piekuur
valt, als ruwe tekst — die gaat nergens in mee en ligt hier om later te kunnen
kijken of bewolking op het piekuur iets zegt.

### `logs/portfolio_history.csv`

Eén regel per open positie per portefeuillerun, sinds 10 augustus elk uur. Dat
is het hele punt van de reeks: daarmee is later te zien of een verwachting
geleidelijk kantelde, en of rood daadwerkelijk verlies voorspelde. De eerste
dagen staan er met tussenpozen van zes uur in, daarna uurlijks.

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
| `light` | `red`, `amber`, `green`, `settled` of `unknown` |
| `peak_hour` | het verwachte uur van de dagpiek, lokale tijd; leeg als de uurcurve ontbrak |
| `observed_today` | wat er die dag al gemeten was, in de eenheid van de markt |
| `restfactor` | de `w` waarmee geconditioneerd is |

Die laatste twee staan er om dezelfde reden als `city_bias_used`: zonder die
kolommen lijkt een kans die verspringt doordat de meting binnenkwam later op een
weersverandering.

`city_bias_used` staat er expliciet in omdat `app_params.js` periodiek opnieuw
gekalibreerd wordt. Zonder die kolom lijkt zo'n bijstelling later in de grafiek
op een weersverandering.

`peak_hour` staat er sinds 11 augustus bij, achteraan zodat de bestaande
kolommen hun plek houden. De drempels van de markt-oneens-vlag zijn gemeten
tegen een aangenomen piekuur van 15:00 lokaal, omdat het echte piekuur nergens
bewaard werd; met deze kolom is die meting over een paar weken over te doen met
de werkelijke uren. De regels van vóór die datum zijn met
`bot/migratie_portfolio_history.py` aangevuld met een leeg veld, zodat het
bestand rechthoekig is. Die migratie mag opnieuw gedraaid worden; staat de
nieuwe kop er al, dan gebeurt er niets.

De stand van nu staat in `portfolio.json` in de hoofdmap; dat bestand leest het
tabblad. Alleen open posities: een positie waarvan de doeldag voorbij is valt
eruit, en restjes onder een half aandeel tellen niet mee.

## Zelftests

```
python3 bot/test_kern.py                        # rekenkern index.html == kalibratie.py
                                                # en kansfunctie == polymarkt.js,
                                                # ook geconditioneerd
python3 bot/test_portfolio.py                   # slug terug, afstanden, netteren
                                                # en elke tak van het stoplicht
python3 bot/test_waarneming.py                  # de conditionering: onveranderd
                                                # zonder meting, nooit krapper
                                                # dan de markt, puntmassa
python3 bot/test_inzet.py                       # Kelly, plafonds, dagstop, en
                                                # lambda op een bekend logboek
python3 bot/test_taf.py                         # TX/TN, maandrand, doeldag
python3 weerbot-modellen/controleer_upload.py   # bestandshashes tegen MANIFEST.txt
python3 weerbot-modellen/controleer_schil.py    # servicewerkerversie dekt de schil
                                                # (--zet werkt hem bij)
python3 weerbot-modellen/pak_features.py check  # featurebundel
python3 bot/test_ml.py                          # invoer van de ML-modellen
python3 bot/meet_meting.py --zelftest            # rekenkant van de metingstudie
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
| `weerbot-modellen/ml_activatie.json` | welke stad-horizonnen de ML-uitkomst getoond krijgen; de zelftest toetst de lijst tegen `monitoring/` |
| `weerbot-modellen/schaduw_backtest.py` | de ML-modellen walk forward tegen de rekenkern, op de historie in `features_alle.csv` |
| `weerbot-modellen/monitoring/` | de uitkomst daarvan per lead |
| `bot/` | kalibratie, logboeken, portefeuillebewaking en zelftests in Python |
| `bot/waarneming.py` | de meting van vandaag en de conditionering erop |
| `bot/meet_meting.py` | wat die meting waard zou zijn in de puntvoorspelling; met de hand te starten |
| `bot/fijnmeting.py` | AMeDAS en NEA, fijner dan het uurlijkse METAR |
| `bot/inzet.py` | positiegrootte, risicoplafonds en het meten van de edge |
| `bot/taf.py` | de luchthavenverwachting, voorlopig alleen loggend |
| `bot/jslezer.py` | tabellen uit polymarkt.js lezen; één parser voor allebei |
| `logs/` | ensemblelog, NWS-log, signalenlog en portefeuillereeks; zie hierboven |
| `.github/workflows/` | dagelijkse en wekelijkse herberekeningen, plus de uurlijkse portefeuille |
| `MODEL_CARD.md` | wat de ML-modellen zijn, waarop ze zijn getraind en wanneer ze aan mogen |
| `REVIEW.md` | externe codereview en het narekenen van de aanbevelingen |
