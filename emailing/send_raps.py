"""
R.A.P.S SERVICES - Prospection notaires / agences (Rhone 69)
Envoi via SMTP Gmail, cadence sur la journee.

Modes (variable SEND_MODE) :
  PREVIEW -> ecrit preview_notaires.html / preview_immo.html, n'envoie rien.
  TEST    -> envoie les deux variantes a TEST_RECIPIENT.
  MASS    -> envoie le lot du creneau en cours aux contacts 'pending'.

Reglages utiles :
  DAILY_LIMIT      nb de mails par jour au total (defaut 30)
  VERTICAL         'notaires', 'immo' ou 'all' (defaut notaires)
  IGNORE_SCHEDULE  'true' pour forcer un envoi hors creneau
  DRY_RUN          'true' pour tout simuler sans rien envoyer
"""

import base64
import csv
import math
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
from zoneinfo import ZoneInfo

# ════════════════════════════════════════════════════════════════════
#  ①  IDENTITE DE L'EXPEDITEUR
# ════════════════════════════════════════════════════════════════════
#  ⚠️ Avec Gmail, FROM_EMAIL DOIT etre le compte authentifie (ou un
#     alias verifie dans Parametres > Comptes > Envoyer des e-mails
#     en tant que). Sinon Google reecrit l'adresse a l'envoi.
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_LOGIN = os.getenv("SMTP_LOGIN", "raps.services69@gmail.com")

FROM_NAME = "Raphaël AUDRAS - R.A.P.S SERVICES"
FROM_EMAIL = os.getenv("FROM_EMAIL", "raps.services69@gmail.com")
REPLY_TO = os.getenv("REPLY_TO", FROM_EMAIL)
TEST_RECIPIENT = os.getenv("TEST_RECIPIENT", FROM_EMAIL)

SITE_URL = "https://www.raps-services-nettoyage.fr"
SITE_LABEL = "www.raps-services-nettoyage.fr"
TEL_1 = "07.77.05.06.50"
TEL_2 = "06.41.03.48.82"
ADRESSE_1 = "43 rue du Docteur Albéric Pont"
ADRESSE_2 = "69005 LYON - Bât. 7, Allée 4"

C_LINK = "#37175F"       # violet du logo, uniquement pour les liens
C_TEXT = "#222222"
C_MUTED = "#8a8a8a"
LOGO_PATH = Path(__file__).resolve().parent / "RAPS-logo.png"
MASTER_PATH = Path(__file__).resolve().parent / "data" / "raps_contacts_master.csv"
PREVIEW_DIR = Path(__file__).resolve().parent.parent

# ════════════════════════════════════════════════════════════════════
#  ②  CADENCE : quand et combien
# ════════════════════════════════════════════════════════════════════
PARIS = ZoneInfo("Europe/Paris")

# Creneaux d'envoi, heure de Paris. Choisis pour tomber quand le
# secretariat d'une etude releve la boite d'accueil :
#   08h45 -> juste avant l'ouverture, le mail est en haut de la pile
#   10h15 -> milieu de matinee, l'etude tourne
#   14h15 -> reprise d'apres-midi
#   16h00 -> avant que la journee se termine
SLOTS = [(8, 45), (10, 15), (14, 15), (16, 0)]

# Creneaux retenus selon le jour (0 = lundi ... 6 = dimanche).
# Lundi matin = rattrapage du week-end, on evite.
# Vendredi apres-midi = etudes deja parties, on evite.
DAY_SLOTS = {
    0: [2, 3],
    1: [0, 1, 2, 3],
    2: [0, 1, 2, 3],
    3: [0, 1, 2, 3],
    4: [0, 1],
}

# Tolerance : GitHub Actions declenche souvent avec 5 a 30 min de retard.
SLOT_TOLERANCE_MIN = 20

DAILY_LIMIT = int(os.getenv("DAILY_LIMIT") or 30)
MAX_PER_RUN = int(os.getenv("MAX_PER_RUN") or 15)
PAUSE_MIN = int(os.getenv("PAUSE_MIN") or 40)
PAUSE_MAX = int(os.getenv("PAUSE_MAX") or 110)

# Relance : delai en jours calendaires apres le 1er envoi.
# Une seule relance par contact, jamais deux.
FOLLOWUP_DAYS = int(os.getenv("FOLLOWUP_DAYS") or 10)
FOLLOWUP_ON = os.getenv("FOLLOWUP_ON", "true").strip().lower() in {"1", "true", "yes"}
SHUFFLE_SEED = int(os.getenv("SHUFFLE_SEED") or 20260914)

SEND_MODE = os.getenv("SEND_MODE", "PREVIEW").strip().upper()
VERTICAL = os.getenv("VERTICAL", "notaires").strip().lower()
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in {"1", "true", "yes"}
IGNORE_SCHEDULE = os.getenv("IGNORE_SCHEDULE", "false").strip().lower() in {"1", "true", "yes"}


def slot_quota(now: datetime | None = None) -> tuple[int, str]:
    """Combien de mails doivent etre partis a cette heure-ci de la journee.

    Retourne (cumul_vise_depuis_ce_matin, explication).
    Le cumul se rattrape tout seul : si un creneau saute, le suivant
    envoie le retard au lieu de le perdre.
    """
    now = now or datetime.now(PARIS)
    indexes = DAY_SLOTS.get(now.weekday(), [])
    if not indexes:
        return 0, "week-end, aucun envoi"

    passed = 0
    for i in indexes:
        h, m = SLOTS[i]
        slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if (now - slot_dt).total_seconds() >= -SLOT_TOLERANCE_MIN * 60:
            passed += 1

    if passed == 0:
        h, m = SLOTS[indexes[0]]
        return 0, f"trop tot, premier creneau a {h:02d}h{m:02d}"

    cumul = math.ceil(DAILY_LIMIT * passed / len(indexes))
    return cumul, f"creneau {passed}/{len(indexes)}, cumul vise {cumul}/{DAILY_LIMIT}"


def sent_today(rows: list[dict]) -> int:
    today = datetime.now(PARIS).date()
    n = 0
    for r in rows:
        raw = (r.get("sent_at") or "").strip()
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(PARIS).date() == today:
            n += 1
    return n


# ════════════════════════════════════════════════════════════════════
#  ③  CONTENU DU MAIL
# ════════════════════════════════════════════════════════════════════
#  Le texte notaires reprend mot pour mot la lettre de Raphael.
#  Pas de bouton, pas de bandeau, pas de liste a coches : une lettre.

def _subject(vertical: str) -> str:
    if vertical == "notaires":
        return "Débarras de logements en succession - secteur lyonnais"
    return "Débarras et remise en état de biens - secteur lyonnais"


def _salutation(vertical: str, nom: str = "") -> str:
    """Le nom vient de la colonne contact_name, remplie par extract_names.py.
    Vide = formule sans nom, toujours correcte."""
    nom = (nom or "").strip()
    if vertical == "notaires":
        return f"Cher Maître {nom}," if nom else "Cher Maître,"
    return "Bonjour,"


def _followup_paragraphs(vertical: str) -> list[str]:
    """Relance : courte, sans reproche, une seule fois."""
    if vertical == "notaires":
        return [
            "Je me permets de revenir vers vous concernant mon message précédent.",
            "Si un dossier de succession nécessite le débarras d’un logement, nous "
            "intervenons sur tout le secteur lyonnais et établissons un devis "
            "gratuit sous 48 heures.",
            "Je reste à votre disposition,",
            "Sincères salutations,",
        ]
    return [
        "Je me permets de revenir vers vous concernant mon message précédent.",
        "Si un bien est à vider ou à remettre en état avant une vente ou une "
        "location, nous intervenons sur tout le secteur lyonnais et établissons "
        "un devis gratuit sous 48 heures.",
        "Je reste à votre disposition,",
        "Sincères salutations,",
    ]


def _body_paragraphs(vertical: str, relance: bool = False) -> list[str]:
    if relance:
        return _followup_paragraphs(vertical)
    if vertical == "notaires":
        return [
            "Conscient que les successions représentent une partie importante de votre "
            "travail, je pense que vos clients sont régulièrement confrontés à des "
            "logements remplis qu’ils ont à charge d’évacuer, notamment dans le cadre "
            "de la revente du bien.",
            "Nous sommes une société intervenant sur tout le secteur lyonnais et nous "
            "proposons du débarras sur tout type de volume, de la simple cave jusqu’au "
            "syndrome de Diogène.",
            "Si ces solutions peuvent intéresser vos clients, nous nous tenons à "
            "disposition pour proposer nos services afin de les faciliter.",
            "Au plaisir d’échanger de vive voix avec vous,",
            "Sincères salutations,",
        ]
    return [
        "Vous avez régulièrement des biens à faire vider ou à remettre en état avant "
        "une visite, une vente ou une nouvelle location.",
        "Nous sommes une société intervenant sur tout le secteur lyonnais et nous "
        "proposons du débarras sur tout type de volume, de la simple cave jusqu’au "
        "syndrome de Diogène, ainsi que le nettoyage de remise en état.",
        "Si ces solutions peuvent vous être utiles, nous nous tenons à disposition "
        "pour intervenir dans vos délais.",
        "Au plaisir d’échanger de vive voix avec vous,",
        "Sincères salutations,",
    ]


def build_text(vertical: str, relance: bool = False, nom: str = "") -> str:
    corps = "\n\n".join(_body_paragraphs(vertical, relance))
    return (
        f"{_salutation(vertical, nom)}\n\n{corps}\n\n"
        f"Raphaël AUDRAS\n"
        f"R.A.P.S SERVICES\n"
        f"{ADRESSE_1}\n{ADRESSE_2}\n"
        f"Tél : {TEL_1} - {TEL_2}\n"
        f"Mail : {FROM_EMAIL}\n{SITE_LABEL}\n\n"
        f"Si vous ne souhaitez pas recevoir d’autre message de ma part, "
        f"dites-le-moi simplement en réponse à ce mail."
    )


def build_html(vertical: str, logo_src: str = "cid:rapslogo", relance: bool = False,
               nom: str = "") -> str:
    font = "Arial,Helvetica,sans-serif"
    paras = "".join(
        f'<p style="margin:0 0 18px 0;">{p}</p>' for p in _body_paragraphs(vertical, relance)
    )
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#ffffff;">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation">
<tr><td align="left" style="padding:24px 18px;">
<table width="560" cellpadding="0" cellspacing="0" role="presentation"
       style="max-width:560px;width:100%;">

  <tr><td style="font-family:{font};font-size:15px;line-height:1.65;color:{C_TEXT};">
    <p style="margin:0 0 18px 0;">{_salutation(vertical, nom)}</p>
    {paras}
  </td></tr>

  <tr><td style="padding-top:6px;">
    <img src="{logo_src}" width="120" alt="R.A.P.S Services"
         style="display:block;width:120px;max-width:120px;height:auto;border:0;">
  </td></tr>

  <tr><td style="font-family:{font};font-size:13px;line-height:1.6;
                 color:{C_TEXT};padding-top:12px;">
    <strong>Raphaël AUDRAS</strong><br>
    R.A.P.S SERVICES<br>
    {ADRESSE_1}<br>
    {ADRESSE_2}<br>
    Tél : {TEL_1} - {TEL_2}<br>
    <a href="mailto:{FROM_EMAIL}"
       style="color:{C_LINK};text-decoration:none;">{FROM_EMAIL}</a><br>
    <a href="{SITE_URL}"
       style="color:{C_LINK};text-decoration:none;">{SITE_LABEL}</a>
  </td></tr>

  <tr><td style="padding-top:22px;border-top:1px solid #e8e8e8;
                 font-family:{font};font-size:11px;line-height:1.5;color:{C_MUTED};">
    Si vous ne souhaitez pas recevoir d’autre message de ma part,
    dites-le-moi simplement en réponse à ce mail.
  </td></tr>

</table>
</td></tr></table></body></html>"""


def build_message(vertical: str, to_email: str, relance: bool = False,
                  in_reply_to: str = "", nom: str = "") -> MIMEMultipart:
    root = MIMEMultipart("related")
    sujet = _subject(vertical)
    root["Subject"] = f"Re: {sujet}" if relance else sujet
    root["From"] = formataddr((FROM_NAME, FROM_EMAIL))
    root["To"] = to_email
    root["Reply-To"] = REPLY_TO
    root["Message-ID"] = make_msgid(domain=FROM_EMAIL.split("@")[-1])
    # la relance se raccroche au fil d'origine : le destinataire
    # retrouve le 1er message juste en dessous
    if relance and in_reply_to:
        root["In-Reply-To"] = in_reply_to
        root["References"] = in_reply_to

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(build_text(vertical, relance, nom), "plain", "utf-8"))
    alt.attach(MIMEText(build_html(vertical, "cid:rapslogo", relance, nom), "html", "utf-8"))
    root.attach(alt)

    try:
        with open(LOGO_PATH, "rb") as f:
            img = MIMEImage(f.read())
        img.add_header("Content-ID", "<rapslogo>")
        img.add_header("Content-Disposition", "inline", filename="raps-logo.png")
        root.attach(img)
    except FileNotFoundError:
        print("⚠️  RAPS-logo.png introuvable, mail envoyé sans logo")
    return root


# ════════════════════════════════════════════════════════════════════
#  ④  MASTER CSV
# ════════════════════════════════════════════════════════════════════

def _safe(v) -> str:
    return "" if v is None else str(v).strip()


def _is_valid_email(e: str) -> bool:
    e = _safe(e)
    return bool(e and "@" in e and "." in e.split("@")[-1] and " " not in e)


def load_master() -> tuple[list[str], list[dict]]:
    if not MASTER_PATH.exists():
        print(f"❌ Master introuvable : {MASTER_PATH}")
        sys.exit(1)
    with MASTER_PATH.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def save_master(fieldnames: list[str], rows: list[dict]) -> None:
    with MASTER_PATH.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def pick_pending(rows: list[dict], limit: int) -> list[dict]:
    out = []
    for r in rows:
        if _safe(r.get("send_status")).lower() != "pending":
            continue
        if _safe(r.get("email_sent")).lower() in {"true", "1", "yes"}:
            continue
        if VERTICAL != "all" and _safe(r.get("vertical")).lower() != VERTICAL:
            continue
        out.append(r)
    # Melange a graine fixe : les bureaux d'une meme etude ne tombent
    # pas le meme matin, et l'ordre reste reproductible d'un run a l'autre.
    random.Random(SHUFFLE_SEED).shuffle(out)
    return out[:limit]


def pick_followups(rows: list[dict], limit: int) -> list[dict]:
    """Contacts a relancer : envoyes il y a assez longtemps, sans reponse,
    sans rebond, et jamais relances."""
    if limit <= 0 or not FOLLOWUP_ON:
        return []
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        if _safe(r.get("send_status")).lower() != "sent":
            continue
        if _safe(r.get("replied")).lower() == "true":
            continue
        if _safe(r.get("bounced")).lower() == "true":
            continue
        if _safe(r.get("followup_at")):
            continue
        if VERTICAL != "all" and _safe(r.get("vertical")).lower() != VERTICAL:
            continue
        raw = _safe(r.get("sent_at"))
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if (now - dt).days >= FOLLOWUP_DAYS:
            out.append(r)
    out.sort(key=lambda r: _safe(r.get("sent_at")))
    return out[:limit]


def mark_followup(row: dict, error: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    row["followup_at"] = now
    row["updated_at"] = now
    if error:
        row["last_error"] = error[:200]


def mark(row: dict, subject: str, status: str = "sent", error: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    if status == "sent":
        row["email_sent"] = "true"
    row["sent_at"] = now
    row["send_status"] = status
    row["send_attempts"] = str(int(_safe(row.get("send_attempts")) or "0") + 1)
    row["last_error"] = error[:200]
    row["last_subject"] = subject
    row["updated_at"] = now


def _store_msgid(row: dict, msg: MIMEMultipart) -> None:
    if not _safe(row.get("message_id")):
        row["message_id"] = msg.get("Message-ID", "")


# ════════════════════════════════════════════════════════════════════
#  ⑤  MODES
# ════════════════════════════════════════════════════════════════════

def _password() -> str:
    pwd = os.getenv("SMTP_PASSWORD", "").strip()
    if not pwd and not DRY_RUN:
        print("❌ SMTP_PASSWORD absent (mot de passe d'application Gmail).")
        sys.exit(1)
    return pwd


def _connect():
    s = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
    s.starttls(context=ssl.create_default_context())
    s.login(SMTP_LOGIN, _password())
    return s


def run_preview() -> int:
    try:
        b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode()
        logo_src = f"data:image/png;base64,{b64}"
    except FileNotFoundError:
        logo_src = ""
    for v, name, nom in (("notaires", "preview_notaires.html", "Baret"),
                         ("immo", "preview_immo.html", "")):
        (PREVIEW_DIR / name).write_text(build_html(v, logo_src, False, nom), encoding="utf-8")
        print(f"  ✅ {name}")
        print(f"     objet : {_subject(v)}")
    print("\n  Ouvre ces fichiers dans un navigateur. Rien n'a été envoyé.")
    return 0


def run_test() -> int:
    print(f"  Envoi des 2 variantes à {TEST_RECIPIENT}")
    if DRY_RUN:
        print("  (DRY_RUN, rien ne part)")
        return 0
    with _connect() as s:
        for v in ("notaires", "immo"):
            s.sendmail(FROM_EMAIL, [TEST_RECIPIENT],
                       build_message(v, TEST_RECIPIENT).as_string())
            print(f"  ✅ {v} : {_subject(v)}")
            time.sleep(5)
    return 0


def run_mass() -> int:
    fieldnames, rows = load_master()
    cumul, why = slot_quota()
    print(f"  Cadence : {why}")

    if cumul == 0 and not IGNORE_SCHEDULE:
        print("  Rien à faire sur ce créneau.")
        return 0

    deja = sent_today(rows)
    reste = MAX_PER_RUN if IGNORE_SCHEDULE else min(max(cumul - deja, 0), MAX_PER_RUN)
    print(f"  Déjà partis aujourd'hui : {deja}")

    if reste <= 0:
        print("  Quota du créneau déjà atteint.")
        return 0

    # Les relances passent en premier : elles ont une date d'echeance,
    # les nouveaux contacts non.
    relances = pick_followups(rows, reste)
    nouveaux = pick_pending(rows, reste - len(relances))
    lot = [(r, True) for r in relances] + [(r, False) for r in nouveaux]

    total_pending = sum(1 for r in rows if _safe(r.get("send_status")).lower() == "pending")
    total_relances = len(pick_followups(rows, 10_000))
    if not lot:
        print(f"  Rien à envoyer en '{VERTICAL}' : "
              f"{total_pending} en attente, {total_relances} relances dues.")
        return 0

    print(f"  Ce lot : {len(nouveaux)} nouveaux + {len(relances)} relances")
    print(f"  Restant : {total_pending} à contacter, {total_relances} à relancer\n")

    sent = err = 0
    server = None if DRY_RUN else _connect()
    try:
        for i, (c, relance) in enumerate(lot, 1):
            email = _safe(c.get("email"))
            vertical = _safe(c.get("vertical")).lower() or "notaires"
            tag = "relance " if relance else "        "
            if not _is_valid_email(email):
                mark(c, "", status="error", error="email invalide")
                err += 1
                continue
            try:
                msg = build_message(vertical, email, relance=relance,
                                    in_reply_to=_safe(c.get("message_id")),
                                    nom=_safe(c.get("contact_name")))
                if not DRY_RUN:
                    server.sendmail(FROM_EMAIL, [email], msg.as_string())
                if relance:
                    mark_followup(c)
                else:
                    mark(c, _subject(vertical), status="sent")
                    _store_msgid(c, msg)
                sent += 1
                print(f"  [{i}/{len(lot)}] ✅ {tag}{email}")
            except Exception as exc:
                if relance:
                    mark_followup(c, error=str(exc))
                else:
                    mark(c, "", status="error", error=str(exc))
                err += 1
                print(f"  [{i}/{len(lot)}] ❌ {tag}{email} · {exc}")
            if i < len(lot):
                time.sleep(random.uniform(PAUSE_MIN, PAUSE_MAX))
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass
        if not DRY_RUN:
            save_master(fieldnames, rows)

    print(f"\n  Résultat : {sent} envoyés, {err} erreurs")
    print(f"  Total du jour : {deja + sent}/{DAILY_LIMIT}")
    return 0


def main() -> int:
    now = datetime.now(PARIS)
    print(f"\n{'='*60}")
    print(f"  R.A.P.S SERVICES - prospection {VERTICAL}")
    print(f"  {now:%d/%m %H:%M} Paris - mode={SEND_MODE} - dry={DRY_RUN}")
    print(f"{'='*60}")
    if SEND_MODE == "MASS":
        return run_mass()
    if SEND_MODE == "TEST":
        return run_test()
    return run_preview()


if __name__ == "__main__":
    raise SystemExit(main())
