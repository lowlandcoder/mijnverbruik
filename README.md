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

## Planning (cron)

Het script draait elke minuut. De cron-regel op de server:

```
* * * * * /usr/bin/python3 /opt/mijnverbruik/collector.py
```

## Instellen

1. Kopieer `scripts/config.example.json` naar `scripts/config.json`.
2. Vul de eigen waarden in: het IP-adres van de meter, de tarieven, het pad
   naar de datamap van de website en eventueel de e-mailinstellingen voor
   storingsmeldingen.
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
- `data/` en de runtime-bestanden (`metingen.db`, `collector.log`, `status.json`).
