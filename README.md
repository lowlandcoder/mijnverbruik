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

## Wat niet in GitHub staat

- `scripts/config.json` (bevat het wachtwoord en het meter-IP).
- `data/` en de runtime-bestanden (`metingen.db`, `collector.log`, `status.json`).
