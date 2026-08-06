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
