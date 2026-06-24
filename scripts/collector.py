#!/usr/bin/env python3
# ════════════════════════════════════════════════════════════
# MIJNVERBRUIK — collector.py
# Leest de HomeWizard P1-meter uit, slaat de meting op in een
# SQLite-database en schrijft JSON-bestanden voor de webpagina.
# Bedoeld om elke minuut te draaien via cron.
# Geen externe pakketten nodig: alleen de Python-standaardbibliotheek.
# ════════════════════════════════════════════════════════════

import json
import smtplib
import sqlite3
import sys
import urllib.request
from datetime import datetime, date
from email.message import EmailMessage
from pathlib import Path

BASIS = Path(__file__).resolve().parent
CONFIG = json.loads((BASIS / "config.json").read_text())
DB_PAD = BASIS / "metingen.db"
LOG_PAD = BASIS / "collector.log"
STATUS_PAD = BASIS / "status.json"


def log(melding):
    regel = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {melding}\n"
    with open(LOG_PAD, "a") as f:
        f.write(regel)


def lees_meter():
    """Haalt de actuele data op uit de lokale API van de HomeWizard P1."""
    url = f"http://{CONFIG['meter_ip']}/api/v1/data"
    with urllib.request.urlopen(url, timeout=10) as antwoord:
        return json.loads(antwoord.read())


def open_db():
    db = sqlite3.connect(DB_PAD)
    db.execute("""
        CREATE TABLE IF NOT EXISTS metingen (
            ts         INTEGER PRIMARY KEY,   -- unix-tijd van de meting
            vermogen_w REAL,                  -- actueel vermogen (W)
            import_t1  REAL,                  -- meterstand kWh tarief 1 (dal)
            import_t2  REAL,                  -- meterstand kWh tarief 2 (normaal)
            gas_m3     REAL                   -- meterstand gas (m3)
        )""")
    return db


def sla_op(db, d):
    db.execute(
        "INSERT OR REPLACE INTO metingen VALUES (?,?,?,?,?)",
        (
            int(datetime.now().timestamp()),
            d.get("active_power_w"),
            d.get("total_power_import_t1_kwh"),
            d.get("total_power_import_t2_kwh"),
            d.get("total_gas_m3"),
        ),
    )
    db.commit()


def huidig_stroomtarief():
    """Geeft het nu geldende stroomtarief (euro per kWh).

    Het daltarief geldt op werkdagen tussen 23:00 en 07:00 en het hele
    weekend; in de overige uren geldt het normaaltarief.
    """
    t = CONFIG["tarieven"]
    nu = datetime.now()
    weekend = nu.weekday() >= 5
    daluur = nu.hour >= 23 or nu.hour < 7
    return t["stroom_t1_per_kwh"] if (weekend or daluur) else t["stroom_t2_per_kwh"]


def kosten(t1_kwh, t2_kwh, gas_m3):
    """Rekent verbruik om naar euro's volgens de tarieven in config.json."""
    t = CONFIG["tarieven"]
    bedrag = (
        t1_kwh * t["stroom_t1_per_kwh"]
        + t2_kwh * t["stroom_t2_per_kwh"]
        + gas_m3 * t["gas_per_m3"]
    )
    return round(bedrag, 2)


def periodes(db, formaat, aantal):
    """Verbruik per dag ('%Y-%m-%d') of per maand ('%Y-%m').

    Het verbruik per periode is het verschil tussen de hoogste en
    laagste meterstand binnen die periode.
    """
    rijen = db.execute(f"""
        SELECT strftime('{formaat}', ts, 'unixepoch', 'localtime') AS periode,
               MAX(import_t1) - MIN(import_t1),
               MAX(import_t2) - MIN(import_t2),
               MAX(gas_m3)    - MIN(gas_m3)
        FROM metingen
        GROUP BY periode
        ORDER BY periode DESC
        LIMIT {int(aantal)}
    """).fetchall()

    uit = []
    for periode, t1, t2, gas in reversed(rijen):
        t1, t2, gas = t1 or 0, t2 or 0, gas or 0
        uit.append({
            "periode": periode,
            "kwh": round(t1 + t2, 3),
            "gas_m3": round(gas, 3),
            "kosten": kosten(t1, t2, gas),
        })
    return uit


def uren_24(db):
    """Verbruik per uur voor de laatste 24 uur."""
    rijen = db.execute("""
        SELECT strftime('%Y-%m-%d %H', ts, 'unixepoch', 'localtime') AS uur,
               MAX(import_t1) - MIN(import_t1),
               MAX(import_t2) - MIN(import_t2),
               MAX(gas_m3)    - MIN(gas_m3)
        FROM metingen
        WHERE ts >= strftime('%s', 'now', '-24 hours')
        GROUP BY uur
        ORDER BY uur
    """).fetchall()

    uit = []
    for uur, t1, t2, gas in rijen:
        t1, t2, gas = t1 or 0, t2 or 0, gas or 0
        uit.append({
            "periode": uur,
            "kwh": round(t1 + t2, 3),
            "gas_m3": round(gas, 3),
            "kosten": kosten(t1, t2, gas),
        })
    return uit


def schrijf_json(db, d):
    map_uit = Path(CONFIG["webroot_data"])
    map_uit.mkdir(parents=True, exist_ok=True)

    uren  = uren_24(db)
    dagen = periodes(db, "%Y-%m-%d", 31)
    maanden = periodes(db, "%Y-%m", 24)

    vandaag = {"kwh": 0, "gas_m3": 0, "kosten": 0}
    if dagen and dagen[-1]["periode"] == date.today().isoformat():
        vandaag = dagen[-1]

    actueel = {
        "tijd": f"{datetime.now():%Y-%m-%d %H:%M}",
        "vermogen_w": d.get("active_power_w"),
        "stroom_per_kwh": huidig_stroomtarief(),
        "vandaag": vandaag,
        "standen": {
            "stroom_t1_kwh": d.get("total_power_import_t1_kwh"),
            "stroom_t2_kwh": d.get("total_power_import_t2_kwh"),
            "gas_m3": d.get("total_gas_m3"),
        },
    }

    (map_uit / "actueel.json").write_text(json.dumps(actueel))
    (map_uit / "uren.json").write_text(json.dumps(uren))
    (map_uit / "dagen.json").write_text(json.dumps(dagen))
    (map_uit / "maanden.json").write_text(json.dumps(maanden))


# ── Monitoring: e-mail bij storing ──────────────────────────


def laad_status():
    if STATUS_PAD.exists():
        return json.loads(STATUS_PAD.read_text())
    return {"storingen": 0, "gemeld": False}


def bewaar_status(status):
    STATUS_PAD.write_text(json.dumps(status))


def stuur_mail(onderwerp, tekst):
    m = CONFIG["monitoring"]
    bericht = EmailMessage()
    bericht["Subject"] = onderwerp
    bericht["From"] = m["smtp_gebruiker"]
    bericht["To"] = m["email_naar"]
    bericht.set_content(tekst)
    with smtplib.SMTP(m["smtp_host"], m["smtp_poort"], timeout=30) as smtp:
        smtp.starttls()
        smtp.login(m["smtp_gebruiker"], m["smtp_wachtwoord"])
        smtp.send_message(bericht)


def meld_storing(status, fout):
    """Stuurt eenmalig een e-mail zodra de drempel is bereikt."""
    m = CONFIG.get("monitoring", {})
    status["storingen"] += 1
    if (
        m.get("actief")
        and not status["gemeld"]
        and status["storingen"] >= m.get("drempel_minuten", 3)
    ):
        try:
            stuur_mail(
                "MijnVerbruik: P1-meter niet bereikbaar",
                f"De P1-meter ({CONFIG['meter_ip']}) levert al "
                f"{status['storingen']} minuten geen data.\n\n"
                f"Laatste foutmelding: {fout}\n\n"
                "Controleer of de meter nog verbonden is met wifi.\n"
                "Er volgt automatisch een herstelmelding zodra de meter "
                "weer bereikbaar is.",
            )
            status["gemeld"] = True
            log("Storingsmail verzonden")
        except Exception as mailfout:
            log(f"FOUT bij versturen storingsmail: {mailfout}")
    bewaar_status(status)


def meld_herstel(status):
    """Stuurt een herstelmelding als er eerder een storing is gemeld."""
    if status["gemeld"] and CONFIG.get("monitoring", {}).get("actief"):
        try:
            stuur_mail(
                "MijnVerbruik: P1-meter weer bereikbaar",
                f"De P1-meter ({CONFIG['meter_ip']}) levert weer data "
                f"na een storing van ongeveer {status['storingen']} minuten.",
            )
            log("Herstelmail verzonden")
        except Exception as mailfout:
            log(f"FOUT bij versturen herstelmail: {mailfout}")
    if status["storingen"] or status["gemeld"]:
        bewaar_status({"storingen": 0, "gemeld": False})


def main():
    status = laad_status()
    try:
        data = lees_meter()
    except Exception as fout:
        log(f"FOUT bij uitlezen meter: {fout}")
        meld_storing(status, fout)
        sys.exit(1)

    meld_herstel(status)

    try:
        db = open_db()
        sla_op(db, data)
        schrijf_json(db, data)
        db.close()
    except Exception as fout:
        log(f"FOUT bij verwerken: {fout}")
        sys.exit(1)


if __name__ == "__main__":
    main()
