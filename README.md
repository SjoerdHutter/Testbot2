# Weerbot 2

Verbeterde versie van [TestBot](https://github.com/SjoerdHutter/TestBot). De app
voorspelt de dagtemperatuur in 51 steden uit een ensemble van vijf modelsystemen
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
veld, geen extra verzoek). De weekkalibratie in `app_params.js` rekent alleen aan
maxima, dus voor het minimum leert de app zelf:

* het gewogen modelgemiddelde gebruikt de skillgewichten per modelsysteem uit de
  maximumkalibratie — die zeggen welk model het op dít station goed doet;
* de dagelijkse controle rekent de restfout uit tegen het gemeten dagminimum en
  levert per horizon een EWMA-gewogen bias (halfwaardetijd 10 dagen) en de
  10/90 restfoutkwantielen als band;
* onder de 8 geverifieerde dagen blijft de correctie uit en staat er `ongeijkt`
  bij het cijfer, met de kale ledenspreiding als band.

Die controle kost geen extra netwerkverkeer: de metingen en de modelreeksen die
ervoor nodig zijn werden al opgehaald voor de maximumcontrole. De bias is niet
klein — op LaGuardia ligt het rastermodelminimum structureel zo'n 2,4 °F onder de
stationsmeting, en juist die meting rekent de markt af.

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

Er wordt niets verhandeld en er gaat niets naar buiten: het venster doet alleen
leesverzoeken naar de publieke Gamma-API van Polymarket. Zhengzhou en Jinan
hebben geen markt; daar staat de knop niet.

## Opslag

Weerbot 2 draait op dezelfde herkomst als de eerste versie (GitHub Pages deelt
`localStorage` over alle paden van één domein). Alle sleutels heten daarom
`weerbot2-…` in plaats van `weerbot-…`, zodat de twee apps elkaars cache, log,
kalibraties en voorkeuren niet overschrijven.

## Zelftests

```
python3 bot/test_kern.py                        # rekenkern index.html == kalibratie.py
python3 weerbot-modellen/controleer_upload.py   # bestandshashes tegen MANIFEST.txt
python3 weerbot-modellen/pak_features.py check  # featurebundel
```

Deze draaien ook in `.github/workflows/zelftest.yml` bij elke push.

## Structuur

| pad | wat |
| --- | --- |
| `index.html` | de hele app: opmaak, rekenkern, controle, kalibratie, weergave |
| `app_params.js` | wekelijks gekalibreerde parameters per stad en horizon |
| `weerbot-modellen/polymarkt.js` | Polymarket-koppeling en het marktvenster |
| `weerbot-modellen/weerbot-ml*.js` | ML-modellen, nog in schaduwfase |
| `bot/` | kalibratie en zelftests in Python |
| `.github/workflows/` | dagelijkse en wekelijkse herberekeningen |
