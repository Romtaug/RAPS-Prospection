"""
╔══════════════════════════════════════════════════════════════════╗
║      R.A.P.S. SERVICES - ENVOI PROSPECTION (nettoyage Lyon)       ║
╠══════════════════════════════════════════════════════════════════╣
║  Cible : agences immobilières + notaires du Rhône (69).           ║
║  Deux angles selon la cible :                                     ║
║    - notaires  → successions / débarras / syndrome de Diogène     ║
║                  + entretien de l'étude                           ║
║    - immo      → fin de chantier, remise en état avant vente /    ║
║                  location, parties communes de copro, vitrines    ║
║                                                                   ║
║  Modes :                                                          ║
║    TEST → envoie les deux variantes à TEST_RECIPIENT (aperçu).    ║
║    MASS → prend les N prochains 'pending' du master, envoie,      ║
║           met à jour le tracking.                                 ║
║                                                                   ║
║  Seul secret obligatoire : SMTP_PASSWORD (GitHub Secret / env).   ║
║  Usage : python emailing/send_raps.py                             ║
╚══════════════════════════════════════════════════════════════════╝
"""

import csv
import os
import random
import smtplib
import ssl
import sys
import time
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from pathlib import Path
from urllib.parse import quote

# ════════════════════════════════════════════════════════════════════
#  ①  CONFIGURATION (à adapter au compte Brevo de RAPS)
# ════════════════════════════════════════════════════════════════════
#  ⚠️ Le login Brevo et l'expéditeur DOIVENT être ceux de RAPS, et le
#     domaine raps-services-nettoyage.fr doit être authentifié dans Brevo
#     (SPF + DKIM + DMARC) sinon ça part en spam.
SMTP_SERVER = "smtp-relay.brevo.com"
SMTP_PORT = 587
SMTP_LOGIN = os.getenv("SMTP_LOGIN", "REMPLIR_LOGIN_BREVO_RAPS@smtp-brevo.com")

FROM_NAME = "R.A.P.S. SERVICES - Nettoyage professionnel à Lyon"
FROM_EMAIL = "contact@raps-services-nettoyage.fr"
REPLY_TO = "contact@raps-services-nettoyage.fr"
TEST_RECIPIENT = os.getenv("TEST_RECIPIENT", "contact@raps-services-nettoyage.fr")

SITE_URL = "https://www.raps-services-nettoyage.fr"
DEVIS_URL = "https://www.raps-services-nettoyage.fr/formulaire-de-contact"
TEL_AFFICHE = "07 77 05 06 50"
TEL_LIEN = "0777050650"
ADRESSE = "43 Rue du Docteur Albéric Pont, 69005 Lyon"

# Palette RÉELLE du logo RAPS (extraite de l'image) : violet profond + blanc.
C_PRIMARY = "#37175F"     # violet de marque (en-tête, titres, CTA)
C_DARK = "#2A1247"        # texte foncé
C_LIGHT = "#F2ECFA"       # lavande très clair (blocs doux)
C_ACCENT = "#8A6FC0"      # violet clair (sous-titres)
LOGO_PATH = Path(__file__).resolve().parent / "RAPS-logo.png"

SEND_MODE = os.getenv("SEND_MODE", "TEST").strip().upper()
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT") or 80)      # local = volume modéré
PAUSE_MIN = int(os.getenv("PAUSE_MIN") or 2)
PAUSE_MAX = int(os.getenv("PAUSE_MAX") or 6)
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in {"1", "true", "yes"}

MASTER_PATH = Path(__file__).resolve().parent / "data" / "raps_contacts_master.csv"

# ── Copie par cible ─────────────────────────────────────────────────
VERTICAL_LABEL = {
    "notaires": "Étude notariale",
    "immo": "Agence immobilière",
}

# ════════════════════════════════════════════════════════════════════
#  ②  CONTENU DU MAIL (sujet + HTML), par cible
# ════════════════════════════════════════════════════════════════════

def _greeting(company: str, city: str) -> str:
    company = (company or "").strip()
    city = (city or "").strip()
    if company and city:
        return f"{company}, à {city}"
    if company:
        return company
    return "Bonjour"


def _subject(vertical: str, company: str) -> str:
    c = (company or "").strip()
    if vertical == "notaires":
        base = "Successions, débarras, entretien d'étude - votre partenaire nettoyage à Lyon"
    else:
        base = "Remise en état, fin de chantier, parties communes - nettoyage pro à Lyon"
    return f"{base}" if not c else f"{c} - {base}"


def _pitch_blocks(vertical: str) -> tuple[str, str, list[str]]:
    """(accroche, sous-titre, liste de prestations) selon la cible."""
    if vertical == "notaires":
        hook = "Une succession à traiter ? On vide, on nettoie, on désinfecte."
        sub = ("Vous gérez des successions : des biens à libérer, parfois en état "
               "d'incurie. R.A.P.S. SERVICES intervient vite et discrètement - et "
               "entretient aussi votre étude au quotidien.")
        items = [
            "Débarras de biens de succession (meubles, encombrants, déchets)",
            "Nettoyage syndrome de Diogène & remise en état complète",
            "Désinfection avant visite, vente ou relocation",
            "Entretien régulier de votre étude (bureaux, vitres, parties communes)",
        ]
    else:
        hook = "Un bien plus propre se vend et se loue plus vite."
        sub = ("Avant chaque visite, vente ou état des lieux, R.A.P.S. SERVICES "
               "remet vos biens et vos locaux au propre - proprement et dans les délais.")
        items = [
            "Nettoyage de fin de chantier & remise en état avant vente / location",
            "Parties communes de copropriété (si vous faites du syndic)",
            "Vitrines et locaux de l'agence, entretien régulier",
            "Interventions ponctuelles ou contrat récurrent, sur devis",
        ]
    return hook, sub, items


def build_html(contact: dict) -> str:
    vertical = (contact.get("vertical") or "immo").strip().lower()
    company = contact.get("company", "")
    city = contact.get("city", "")
    hook, sub, items = _pitch_blocks(vertical)
    greet = _greeting(company, city)
    label = VERTICAL_LABEL.get(vertical, "Professionnel")

    # mailto pré-rempli (le destinataire n'a qu'à compléter et envoyer)
    sujet_reponse = quote(f"Demande de devis nettoyage - {company}".strip(" -"))
    corps_reponse = quote(
        "Bonjour,\n\nJe souhaite un devis pour :\n- Type de prestation : \n"
        "- Surface / fréquence : \n- Adresse : \n\nMerci."
    )
    mailto = f"mailto:{FROM_EMAIL}?subject={sujet_reponse}&body={corps_reponse}"

    items_html = "".join(
        f'<tr><td style="padding:6px 0;color:{C_DARK};font-size:15px;'
        f'font-family:Arial,sans-serif;">'
        f'<span style="color:{C_PRIMARY};font-weight:bold;">&#10003;</span> {it}</td></tr>'
        for it in items
    )

    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{C_LIGHT};">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation"
       style="background:{C_LIGHT};padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" role="presentation"
       style="background:#ffffff;border-radius:14px;overflow:hidden;
              box-shadow:0 2px 12px rgba(11,43,58,.08);max-width:600px;width:100%;">

  <!-- Logo réel RAPS (image intégrée via CID) -->
  <tr><td align="center" style="background:{C_PRIMARY};padding:18px 0;">
    <img src="cid:rapslogo" width="300" alt="R.A.P.S. Services"
         style="display:block;width:300px;max-width:78%;height:auto;margin:0 auto;border:0;">
    <div style="font-family:Arial,sans-serif;color:#d9ccef;font-size:13px;
                margin-top:6px;">Nettoyage &amp; désinfection de locaux - Lyon 5e</div>
  </td></tr>

  <!-- Accroche -->
  <tr><td style="padding:30px 28px 8px 28px;">
    <div style="font-family:Arial,sans-serif;color:{C_PRIMARY};font-size:13px;
                font-weight:bold;text-transform:uppercase;letter-spacing:.5px;">
      {label}</div>
    <h1 style="font-family:Arial,sans-serif;color:{C_DARK};font-size:23px;
               line-height:1.3;margin:8px 0 0 0;">{hook}</h1>
  </td></tr>

  <!-- Corps -->
  <tr><td style="padding:14px 28px 4px 28px;">
    <p style="font-family:Arial,sans-serif;color:{C_DARK};font-size:15px;
              line-height:1.6;margin:0 0 14px 0;">
      Bonjour {greet},</p>
    <p style="font-family:Arial,sans-serif;color:#3a4a55;font-size:15px;
              line-height:1.6;margin:0 0 16px 0;">{sub}</p>
    <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
           style="background:{C_LIGHT};border-radius:10px;padding:6px 16px;">
      {items_html}
    </table>
  </td></tr>

  <!-- Atouts -->
  <tr><td style="padding:18px 28px 4px 28px;">
    <p style="font-family:Arial,sans-serif;color:#3a4a55;font-size:14px;
              line-height:1.6;margin:0;">
      Interventions <strong>7j/7, 6h-22h</strong>, réactivité en urgence,
      matériel professionnel, <strong>devis gratuit</strong> sous 48 h.</p>
  </td></tr>

  <!-- CTA -->
  <tr><td align="center" style="padding:24px 28px 8px 28px;">
    <a href="{mailto}" style="display:inline-block;background:{C_PRIMARY};
       color:#ffffff;text-decoration:none;font-family:Arial,sans-serif;
       font-size:16px;font-weight:bold;padding:14px 30px;border-radius:8px;">
       Demander un devis gratuit</a>
    <div style="font-family:Arial,sans-serif;color:#3a4a55;font-size:14px;
                margin-top:14px;">ou appelez-nous :
      <a href="tel:{TEL_LIEN}" style="color:{C_PRIMARY};font-weight:bold;
         text-decoration:none;">{TEL_AFFICHE}</a></div>
  </td></tr>

  <!-- Pied -->
  <tr><td style="padding:22px 28px;border-top:1px solid #e6eeed;">
    <div style="font-family:Arial,sans-serif;color:#8aa0a6;font-size:12px;
                line-height:1.6;">
      R.A.P.S. SERVICES - {ADRESSE}<br>
      <a href="{SITE_URL}" style="color:{C_PRIMARY};text-decoration:none;">
        raps-services-nettoyage.fr</a> · {TEL_AFFICHE}<br><br>
      Vous recevez ce message à titre professionnel. Pour ne plus être contacté,
      répondez « STOP » à cet email.
    </div>
  </td></tr>

</table>
</td></tr></table></body></html>"""


def build_text(contact: dict) -> str:
    vertical = (contact.get("vertical") or "immo").strip().lower()
    hook, sub, items = _pitch_blocks(vertical)
    greet = _greeting(contact.get("company", ""), contact.get("city", ""))
    lignes = "\n".join(f"  - {it}" for it in items)
    return (
        f"R.A.P.S. SERVICES - Nettoyage professionnel à Lyon\n\n"
        f"Bonjour {greet},\n\n{hook}\n\n{sub}\n\n{lignes}\n\n"
        f"Interventions 7j/7 (6h-22h), réactivité urgence, devis gratuit sous 48h.\n\n"
        f"Devis : {FROM_EMAIL} · Tél : {TEL_AFFICHE}\n{SITE_URL}\n\n"
        f"{ADRESSE}\nPour ne plus être contacté, répondez STOP."
    )


def build_message(contact: dict, to_email: str, subject: str) -> MIMEMultipart:
    root = MIMEMultipart("related")
    root["Subject"] = subject
    root["From"] = formataddr((FROM_NAME, FROM_EMAIL))
    root["To"] = to_email
    root["Reply-To"] = REPLY_TO
    root["Message-ID"] = make_msgid(domain="raps-services-nettoyage.fr")
    root["List-Unsubscribe"] = f"<mailto:{FROM_EMAIL}?subject=STOP>"

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(build_text(contact), "plain", "utf-8"))
    alt.attach(MIMEText(build_html(contact), "html", "utf-8"))
    root.attach(alt)

    # Logo réel en pièce jointe inline (CID) → s'affiche dans le mail
    try:
        with open(LOGO_PATH, "rb") as f:
            img = MIMEImage(f.read())
        img.add_header("Content-ID", "<rapslogo>")
        img.add_header("Content-Disposition", "inline", filename="raps-logo.png")
        root.attach(img)
    except FileNotFoundError:
        pass
    return root


# ════════════════════════════════════════════════════════════════════
#  ③  MASTER CSV - lecture / tracking
# ════════════════════════════════════════════════════════════════════

def _safe(v) -> str:
    return "" if v is None else str(v).strip()


def _is_valid_email(e: str) -> bool:
    e = _safe(e)
    return bool(e and "@" in e and "." in e.split("@")[-1])


def load_master() -> tuple[list[str], list[dict]]:
    if not MASTER_PATH.exists():
        print(f"❌ Master introuvable : {MASTER_PATH}")
        print("   → Lance d'abord : python build_master_raps.py")
        sys.exit(1)
    with MASTER_PATH.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    print(f"📂 Master chargé : {len(rows)} contacts")
    return fieldnames, rows


def save_master(fieldnames: list[str], rows: list[dict]) -> None:
    with MASTER_PATH.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def pick_pending(rows: list[dict], limit: int) -> list[dict]:
    pending = [
        r for r in rows
        if _safe(r.get("send_status")).lower() == "pending"
        and _safe(r.get("email_sent")).lower() not in {"true", "1", "yes"}
    ]
    pending.sort(key=lambda r: -int(_safe(r.get("score_source_rank")) or 0))
    return pending[:limit]


def mark_sent(row: dict, subject: str, status: str = "sent", error: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    if status == "sent":
        row["email_sent"] = "true"
    row["sent_at"] = now
    row["send_status"] = status
    row["send_attempts"] = str(int(_safe(row.get("send_attempts")) or "0") + 1)
    row["last_error"] = error[:200]
    row["last_subject"] = subject
    row["updated_at"] = now


# ════════════════════════════════════════════════════════════════════
#  ④  SMTP
# ════════════════════════════════════════════════════════════════════

def _smtp_password() -> str:
    pwd = os.getenv("SMTP_PASSWORD", "").strip()
    if not pwd and not DRY_RUN:
        print("❌ SMTP_PASSWORD absent. (export SMTP_PASSWORD=... ou GitHub Secret)")
        sys.exit(1)
    return pwd


def _send(server, contact: dict, to_email: str) -> str:
    subject = _subject(contact.get("vertical", "immo"), contact.get("company", ""))
    msg = build_message(contact, to_email, subject)
    if not DRY_RUN:
        server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
    return subject


# ════════════════════════════════════════════════════════════════════
#  ⑤  MODES
# ════════════════════════════════════════════════════════════════════

def run_test() -> int:
    print(f"  Mode : TEST → {TEST_RECIPIENT}\n{'='*64}")
    samples = [
        {"vertical": "notaires", "company": "Étude de Maître Exemple", "city": "Lyon"},
        {"vertical": "immo", "company": "Agence Exemple Immobilier", "city": "Villeurbanne"},
    ]
    pwd = _smtp_password()
    ctx = ssl.create_default_context()
    if DRY_RUN:
        for c in samples:
            subj = _subject(c["vertical"], c["company"])
            print(f"  [DRY] {c['vertical']:<9} → {subj[:60]}…")
        print("  (DRY_RUN : rien n'est envoyé)")
        return 0
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
        s.starttls(context=ctx)
        s.login(SMTP_LOGIN, pwd)
        for c in samples:
            subj = _send(s, c, TEST_RECIPIENT)
            print(f"  ✅ envoyé ({c['vertical']}) : {subj[:55]}…")
            time.sleep(random.uniform(PAUSE_MIN, PAUSE_MAX))
    return 0


def run_mass() -> int:
    print(f"  Mode : MASS - limite {DAILY_LIMIT}\n{'='*64}")
    fieldnames, rows = load_master()
    contacts = pick_pending(rows, DAILY_LIMIT)
    total_pending = sum(
        1 for r in rows
        if _safe(r.get("send_status")).lower() == "pending"
        and _safe(r.get("email_sent")).lower() not in {"true", "1", "yes"}
    )
    if not contacts:
        print("ℹ️  Aucun contact pending. Relance build_master_raps.py / le scrape.")
        return 0
    print(f"  À envoyer : {len(contacts)} / pending total : {total_pending}")

    pwd = _smtp_password()
    ctx = ssl.create_default_context()
    sent = err = 0

    def _process(server):
        nonlocal sent, err
        for i, c in enumerate(contacts, 1):
            email = _safe(c.get("email"))
            if not _is_valid_email(email):
                mark_sent(c, "", status="error", error="email invalide")
                err += 1
                continue
            try:
                subj = _send(server, c, email)
                mark_sent(c, subj, status="sent")
                sent += 1
                print(f"  [{i:>3}/{len(contacts)}] ✅ {email}")
            except Exception as exc:
                mark_sent(c, "", status="error", error=str(exc))
                err += 1
                print(f"  [{i:>3}/{len(contacts)}] ❌ {email} - {exc}")
            if i < len(contacts):
                time.sleep(random.uniform(PAUSE_MIN, PAUSE_MAX))

    if DRY_RUN:
        _process(None)
    else:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
            s.starttls(context=ctx)
            s.login(SMTP_LOGIN, pwd)
            _process(s)

    if not DRY_RUN:
        save_master(fieldnames, rows)
    print(f"\n  RÉSULTAT : {sent} envoyés / {err} erreurs")
    print(f"  Pending restant : ~{total_pending - sent - err}")
    return 0


def main() -> int:
    print(f"\n{'='*64}\n  R.A.P.S. SERVICES - Prospection nettoyage (Rhône 69)")
    print(f"  Mode={SEND_MODE}  Dry={DRY_RUN}\n{'='*64}")
    return run_mass() if SEND_MODE == "MASS" else run_test()


if __name__ == "__main__":
    raise SystemExit(main())
