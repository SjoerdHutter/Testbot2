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
