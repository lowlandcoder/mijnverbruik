# MijnVerbruik

Eenvoudig dashboard voor het eigen energieverbruik (stroom, gas en kosten).
De pagina toont de actuele cijfers en grafieken per uur, per dag en per maand.

## Onderdelen

- `index.html` — de webpagina (laadt `huisstijl.css` en toont de grafieken).
- `huisstijl.css` — gedeelde huisstijl: lettertype, kleuren en de kop "Mijn + thema".
- `scripts/collector.py` — verzamelscript dat de meter uitleest en de data klaarzet.
- `scripts/config.example.json` — voorbeeld van de instellingen.

## Hoe het werkt

`collector.py` leest elke minuut de HomeWizard P1-meter uit via het lokale
netwerk, slaat de meting op in een SQLite-database (`metingen.db`) en schrijft
de JSON-bestanden die de pagina toont. De pagina haalt die JSON elke minuut op.

De pagina toont de uren van vandaag, vanaf 00:00, met de uren van gisteren als
lijn erachter. Daaronder staan de laatste 31 dagen en de laatste 24 maanden.

## Hoe het verbruik wordt berekend

Het verbruik in een periode is de meterstand aan het einde min de meterstand
aan het begin. De stand op een grens is de laatst bekende meting op of voor dat
moment. Periodes sluiten daardoor op elkaar aan en er gaat niets verloren
tussen twee periodes.

Tot augustus 2026 werd het verbruik berekend als het verschil tussen de hoogste
en de laagste stand binnen een periode. Dat telde alleen het verbruik tussen de
gemeten momenten. Bij een meting per minuut viel dat nauwelijks op, maar bij een
uitval van dagen verdween het verbruik van die dagen volledig, ook uit de
maandtotalen.

Valt de meting langer uit dan `gat_drempel_minuten` (standaard 15 minuten), dan
hoort al het verbruik van dat gat volgens die regel bij de eerste meting daarna.
Dat zou een valse piek geven. Zo'n gat wordt daarom naar rato van de tijd
verdeeld over de periodes die het raakt. De som blijft precies gelijk aan het
gemeten verschil in meterstand, dus dag-, maand- en jaartotalen kloppen ook na
een storing van dagen.

Op de pagina is te zien wat gemeten is en wat geschat:

- een periode zonder eigen metingen krijgt een grijze staaf;
- onder de grafiek staat hoeveel kWh is geschat;
- de tooltip vermeldt het geschatte deel van een periode.

Twee aandachtspunten:

- Op de dagen dat de klok verspringt heeft een dag 23 of 25 uur. Het aantal
  staven in de uurgrafiek volgt dat vanzelf, want de grenzen komen uit de
  lokale klok.
- Wordt de meter vervangen, dan valt de stand terug naar nul. Een negatief
  verschil telt als nul verbruik. De dag van de vervanging is daardoor te laag;
  de dagen erna kloppen weer.

## Planning (systemd-timer)

Het script draait elke minuut via een systemd-timer. De bestanden staan in
`systemd/` en horen op de server in `/etc/systemd/system/`.

```bash
sudo cp systemd/mijnverbruik-collector.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mijnverbruik-collector.timer
```

Stand en uitvoer bekijken:

```bash
systemctl list-timers mijnverbruik-collector.timer
journalctl -u mijnverbruik-collector.service --since "-1 h"
```

Tot 29 juli 2026 liep dit via cron (`* * * * * /usr/bin/python3
/opt/mijnverbruik/collector.py`). Die regel staat op de server uitgeschakeld in
de crontab van peter en kan weg zodra de timer zich bewezen heeft.

## Instellen

1. Kopieer `scripts/config.example.json` naar `scripts/config.json`.
2. Vul de eigen waarden in: het IP-adres van de meter, de tarieven, het pad
   naar de datamap van de website, de gegevensmap en eventueel de
   e-mailinstellingen voor storingsmeldingen. Blijft `gat_drempel_minuten`
   leeg, dan geldt 15 minuten.
3. `config.json` bevat geheimen en staat daarom in `.gitignore`; dit bestand
   hoort nooit op GitHub.

## Publiceren (bijwerken op de server)

Wijzigingen staan eerst op GitHub. De server haalt ze op uit een aparte kopie
van de repository en kopieert de juiste bestanden naar hun plek. Dit is nodig
omdat de bestanden op de server op twee plekken staan, met een andere indeling
dan de repository: de website in `/var/www/mijnverbruik/` en het verzamelscript
als `/opt/mijnverbruik/collector.py`.

Eenmalige opzet (al gedaan):

- repository als bron op de server: `~/mijnverbruik-repo`
  (`git clone https://github.com/lowlandcoder/mijnverbruik.git ~/mijnverbruik-repo`);
- publicatiescript: `~/publiceer-mijnverbruik.sh`.

Een wijziging publiceren:

1. de wijziging lokaal vastleggen en naar GitHub pushen (`git push origin main`);
2. op de server `~/publiceer-mijnverbruik.sh` uitvoeren.

Het script doet een `git pull`, kopieert `index.html` en `huisstijl.css` naar
`/var/www/mijnverbruik/` en `scripts/collector.py` naar
`/opt/mijnverbruik/collector.py`. De instellingen (`config.json`) en de map
`data/` blijven ongemoeid. De inhoud van het script:

```bash
#!/bin/bash
set -e
cd ~/mijnverbruik-repo
git pull origin main
cp index.html huisstijl.css /var/www/mijnverbruik/
sudo cp scripts/collector.py /opt/mijnverbruik/collector.py
echo "Gepubliceerd: website en verzamelscript bijgewerkt."
```

## Wat niet in GitHub staat

- `scripts/config.json` (bevat het wachtwoord en het meter-IP).
- `data/` en de runtime-bestanden (`metingen.db`, `collector.log`,
  `status.json`). Die staan in de map die `gegevensmap` in `config.json`
  aanwijst, standaard `/srv/ssddata/mijnverbruik/`. Daardoor gaan ze mee in
  de reservekopie van de SSD. Blijft `gegevensmap` leeg, dan komen ze naast
  `collector.py` te staan; dat is de oude plek en valt buiten de reservekopie.
