"""
R.A.P.S SERVICES - Lecture de la boite Gmail (IMAP).

Marque dans le master :
  - replied  = true  -> l'etude a repondu, on ne relancera pas
  - bounced  = true  -> adresse morte, on ne relancera pas non plus

A lancer AVANT chaque envoi. Utilise le meme mot de passe
d'application que l'envoi (variable SMTP_PASSWORD).
"""

import csv
import email
import imaplib
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from pathlib import Path

IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993
IMAP_LOGIN = os.getenv("SMTP_LOGIN", "raps.services69@gmail.com")
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS") or 45)

MASTER_PATH = Path(__file__).resolve().parent / "data" / "raps_contacts_master.csv"

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Expediteurs / sujets qui signalent une adresse morte plutot qu'une reponse
BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail-daemon")
BOUNCE_SUBJECTS = (
    "delivery status notification",
    "undelivered mail returned",
    "undeliverable",
    "returned mail",
    "echec de la remise",
    "non remis",
)
# Reponses automatiques : ce n'est pas un vrai contact, on ne marque rien
AUTO_SUBJECTS = (
    "absence du bureau",
    "out of office",
    "reponse automatique",
    "réponse automatique",
    "automatic reply",
    "congés",
    "conges",
)


def _decode(value: str) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def fetch_inbox() -> tuple[set[str], set[str]]:
    """Retourne (adresses ayant repondu, adresses en echec)."""
    pwd = os.getenv("SMTP_PASSWORD", "").strip()
    if not pwd:
        print("❌ SMTP_PASSWORD absent (mot de passe d'application Gmail).")
        sys.exit(1)

    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    replied: set[str] = set()
    bounced: set[str] = set()

    box = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    try:
        box.login(IMAP_LOGIN, pwd)
        box.select("INBOX", readonly=True)
        status, data = box.search(None, f'(SINCE {since})')
        if status != "OK":
            print("⚠️  Recherche IMAP en echec")
            return replied, bounced

        ids = data[0].split()
        print(f"  {len(ids)} messages reçus depuis {since}")

        for mid in ids:
            # on ne lit d'abord que les en-tetes : le corps complet
            # n'est telecharge que pour les rapports d'echec
            status, raw = box.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if status != "OK" or not raw or not raw[0]:
                continue
            head = email.message_from_bytes(raw[0][1])
            sender = _decode(head.get("From", "")).lower()
            subject = _decode(head.get("Subject", "")).lower()

            is_bounce = (
                any(s in sender for s in BOUNCE_SENDERS)
                or any(s in subject for s in BOUNCE_SUBJECTS)
            )

            if is_bounce:
                status, full = box.fetch(mid, "(BODY.PEEK[])")
                if status != "OK" or not full or not full[0]:
                    continue
                msg = email.message_from_bytes(full[0][1])
                body = ""
                for part in msg.walk():
                    if part.get_content_type() in ("text/plain", "message/delivery-status"):
                        try:
                            body += part.get_payload(decode=True).decode("utf-8", "ignore")
                        except Exception:
                            pass
                bounced.update(e.lower() for e in EMAIL_RE.findall(body))
                continue

            if any(s in subject for s in AUTO_SUBJECTS):
                continue

            found = EMAIL_RE.findall(sender)
            if found:
                replied.add(found[0].lower())
    finally:
        try:
            box.logout()
        except Exception:
            pass

    return replied, bounced


def main() -> int:
    print(f"\n{'='*60}\n  Lecture de la boîte {IMAP_LOGIN}\n{'='*60}")
    if not MASTER_PATH.exists():
        print(f"❌ Master introuvable : {MASTER_PATH}")
        return 1

    with MASTER_PATH.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for col in ("replied", "replied_at", "bounced"):
        if col not in fieldnames:
            fieldnames.append(col)

    replied, bounced = fetch_inbox()
    # on ne retient que les adresses qu'on a reellement contactees
    contacts = {(r.get("email") or "").strip().lower(): r for r in rows}
    now = datetime.now(timezone.utc).isoformat()
    n_rep = n_bou = 0

    for addr in replied:
        row = contacts.get(addr)
        if row and (row.get("replied") or "").lower() != "true":
            row["replied"] = "true"
            row["replied_at"] = now
            row["updated_at"] = now
            n_rep += 1

    for addr in bounced:
        row = contacts.get(addr)
        if row and (row.get("bounced") or "").lower() != "true":
            row["bounced"] = "true"
            row["updated_at"] = now
            n_bou += 1

    with MASTER_PATH.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    total_rep = sum(1 for r in rows if (r.get("replied") or "").lower() == "true")
    total_bou = sum(1 for r in rows if (r.get("bounced") or "").lower() == "true")
    print(f"\n  Nouvelles réponses  : {n_rep}  (total {total_rep})")
    print(f"  Nouveaux rebonds    : {n_bou}  (total {total_bou})")
    print("  Ces contacts ne seront pas relancés.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
