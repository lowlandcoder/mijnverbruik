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
from datetime import datetime, date, time, timedelta
from email.message import EmailMessage
from pathlib import Path

BASIS = Path(__file__).resolve().parent
CONFIG = json.loads((BASIS / "config.json").read_text())
GEGEVENS = Path(CONFIG.get("gegevensmap", BASIS))
DB_PAD = GEGEVENS / "metingen.db"
LOG_PAD = GEGEVENS / "collector.log"
STATUS_PAD = GEGEVENS / "status.json"


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


# ── Verbruik per periode ────────────────────────────
#
# Verbruik in een periode is de meterstand aan het einde min de
# meterstand aan het begin. De stand op een grens is de laatst
# bekende meting op of voor dat moment. Periodes sluiten zo op
# elkaar aan; er gaat niets verloren tussen twee periodes.
#
# Valt de meting langer uit, dan hoort al het verbruik van dat gat
# volgens die regel bij de eerste meting daarna. Dat zou een valse
# piek geven. Een gat langer dan de drempel wordt daarom naar rato
# van de tijd verdeeld over de periodes die het raakt, en apart
# geteld als schatting. De som blijft gelijk aan het gemeten
# verschil in meterstand, dus dag-, maand- en jaartotalen kloppen.


def meting_op_of_voor(db, ts):
    return db.execute(
        "SELECT ts, import_t1, import_t2, gas_m3 FROM metingen "
        "WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (int(ts),)
    ).fetchone()


def meting_op_of_na(db, ts):
    return db.execute(
        "SELECT ts, import_t1, import_t2, gas_m3 FROM metingen "
        "WHERE ts >= ? ORDER BY ts ASC LIMIT 1", (int(ts),)
    ).fetchone()


def eerste_meting_ts(db):
    rij = db.execute("SELECT MIN(ts) FROM metingen").fetchone()
    return rij[0]


def verschil(begin, eind):
    """Verschil tussen twee metingen per veld (t1, t2, gas).

    Een negatief verschil kan niet: dat wijst op een vervangen meter
    of op een ontbrekende waarde. Die telt als nul.
    """
    if begin is None or eind is None:
        return [0.0, 0.0, 0.0]
    uit = []
    for i in (1, 2, 3):
        a, b = begin[i], eind[i]
        uit.append(b - a if a is not None and b is not None and b > a else 0.0)
    return uit


def verbruik_reeks(db, grenzen, labels, drempel_s):
    """Verbruik per periode tussen opeenvolgende grenzen (unix-tijd)."""
    nu = int(datetime.now().timestamp())
    eerste = eerste_meting_ts(db)

    voor = [meting_op_of_voor(db, g) for g in grenzen]
    na = [meting_op_of_na(db, g) for g in grenzen]

    reeks = []
    for i, label in enumerate(labels):
        # Is er geen meting van voor de grens, dan is de eerste meting
        # daarna het beginpunt. Wat daarvoor is verbruikt, is onbekend.
        begin_stand = voor[i] if voor[i] is not None else na[i]
        t1, t2, gas = verschil(begin_stand, voor[i + 1])
        begin_meting = na[i]
        reeks.append({
            "periode": label,
            "begin": grenzen[i],
            "eind": grenzen[i + 1],
            "t1": t1, "t2": t2, "gas": gas,
            "g_t1": 0.0, "g_t2": 0.0, "g_gas": 0.0,
            "gemeten": begin_meting is not None
                       and begin_meting[0] < grenzen[i + 1],
            "toekomst": grenzen[i] > nu,
        })

    # Lange gaten opsporen: op elke grens de laatste meting ervoor en
    # de eerste erna vergelijken. Hetzelfde gat kan meer grenzen raken,
    # daarom een verzameling op begin- en eindtijd.
    gaten = {}
    for a, b in zip(voor, na):
        if a is None or b is None or b[0] - a[0] <= drempel_s:
            continue
        gaten[(a[0], b[0])] = (a, b)

    for (t_begin, t_eind), (a, b) in gaten.items():
        duur = t_eind - t_begin
        deel = verschil(a, b)
        # De regel hierboven legt dit hele verbruik in de periode die als
        # eerste eindigt op of na de eerste meting na het gat. Daar eerst
        # weghalen. Let op de grenzen: eindigt het gat precies op een
        # periodegrens, dan hoort het bij de periode ervoor.
        for p in reeks:
            if p["begin"] < t_eind <= p["eind"]:
                p["t1"] -= deel[0]
                p["t2"] -= deel[1]
                p["gas"] -= deel[2]
                break
        # En daarna naar rato van de tijd terugleggen.
        for p in reeks:
            overlap = min(p["eind"], t_eind) - max(p["begin"], t_begin)
            if overlap <= 0:
                continue
            aandeel = overlap / duur
            for veld, geschat, waarde in (("t1", "g_t1", deel[0]),
                                          ("t2", "g_t2", deel[1]),
                                          ("gas", "g_gas", deel[2])):
                p[veld] += waarde * aandeel
                p[geschat] += waarde * aandeel

    uit = []
    for p in reeks:
        if eerste is not None and p["eind"] <= eerste and not uit:
            continue          # periode van voor de eerste meting ooit
        if p["toekomst"]:
            uit.append({"periode": p["periode"], "kwh": None, "gas_m3": None,
                        "kosten": None, "geschat_kwh": 0, "geschat_gas_m3": 0,
                        "gemeten": False, "toekomst": True})
            continue
        t1, t2, gas = max(p["t1"], 0.0), max(p["t2"], 0.0), max(p["gas"], 0.0)
        uit.append({
            "periode": p["periode"],
            "kwh": round(t1 + t2, 3),
            "gas_m3": round(gas, 3),
            "kosten": kosten(t1, t2, gas),
            "geschat_kwh": round(p["g_t1"] + p["g_t2"], 3),
            "geschat_gas_m3": round(p["g_gas"], 3),
            "gemeten": p["gemeten"],
            "toekomst": False,
        })
    return uit


# ── Grenzen van uren, dagen en maanden ──────────────────
#
# De grenzen komen uit de lokale klok. Op de dagen dat de klok
# verspringt heeft een dag 23 of 25 uur; het aantal staven volgt
# dat vanzelf, want er wordt in stappen van een uur echte tijd van
# middernacht naar middernacht gelopen.


def middernacht(dag):
    return int(datetime.combine(dag, time.min).timestamp())


def grenzen_dag(dag):
    """Uurgrenzen van een kalenderdag."""
    start, eind = middernacht(dag), middernacht(dag + timedelta(days=1))
    grenzen, labels, t = [start], [], start
    while t < eind:
        labels.append(datetime.fromtimestamp(t).strftime("%H:%M"))
        t = min(t + 3600, eind)
        grenzen.append(t)
    return grenzen, labels


def grenzen_dagen(aantal):
    """Daggrenzen voor de laatste dagen, vandaag als laatste."""
    vandaag = date.today()
    dagen = [vandaag - timedelta(days=i) for i in range(aantal - 1, -1, -1)]
    grenzen = [middernacht(d) for d in dagen]
    grenzen.append(middernacht(vandaag + timedelta(days=1)))
    return grenzen, [d.isoformat() for d in dagen]


def volgende_maand(m):
    return date(m.year + 1, 1, 1) if m.month == 12 else date(m.year, m.month + 1, 1)


def vorige_maand(m):
    return date(m.year - 1, 12, 1) if m.month == 1 else date(m.year, m.month - 1, 1)


def grenzen_maanden(aantal):
    """Maandgrenzen voor de laatste maanden, deze maand als laatste."""
    m = date.today().replace(day=1)
    maanden = []
    for _ in range(aantal):
        maanden.append(m)
        m = vorige_maand(m)
    maanden.reverse()
    grenzen = [middernacht(x) for x in maanden]
    grenzen.append(middernacht(volgende_maand(maanden[-1])))
    return grenzen, [x.strftime("%Y-%m") for x in maanden]


def schrijf_json(db, d):
    map_uit = Path(CONFIG["webroot_data"])
    map_uit.mkdir(parents=True, exist_ok=True)

    drempel = int(CONFIG.get("gat_drempel_minuten", 15)) * 60
    vandaag_datum = date.today()

    g, l = grenzen_dag(vandaag_datum)
    uren_vandaag = verbruik_reeks(db, g, l, drempel)
    g, l = grenzen_dag(vandaag_datum - timedelta(days=1))
    uren_gisteren = verbruik_reeks(db, g, l, drempel)
    g, l = grenzen_dagen(31)
    dagen = verbruik_reeks(db, g, l, drempel)
    g, l = grenzen_maanden(24)
    maanden = verbruik_reeks(db, g, l, drempel)

    vandaag = dagen[-1] if dagen else {"kwh": 0, "gas_m3": 0, "kosten": 0}

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

    uren = {
        "dag": vandaag_datum.isoformat(),
        "vandaag": uren_vandaag,
        "gisteren": uren_gisteren,
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
