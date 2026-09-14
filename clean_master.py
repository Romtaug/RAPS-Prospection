"""
R.A.P.S SERVICES - Nettoyage du master (a lancer UNE FOIS).

1. Ajoute les colonnes de suivi des relances.
2. Notaires : quand un meme bureau a 2 adresses (meme telephone),
   on garde la plus institutionnelle et on exclut l'autre.
3. Immo : la source immomatin.com melange agences et prestataires
   du secteur (logiciels, visites 3D, formation, portails nationaux).
   On ne garde que les vraies agences et regies lyonnaises.
"""

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

MASTER = Path(__file__).resolve().parent / "emailing" / "data" / "raps_contacts_master.csv"

NEW_COLS = ["message_id", "replied", "replied_at", "bounced", "followup_at"]

# Agences et regies reellement implantees dans le 69.
IMMO_GARDES = {
    "cabinet folliet",
    "croix-rousse immobilier",
    "césar & brutus",
    "c’ loué c’ vendu",
    "groupe pruvost",
    "pure gestion",
    "regie saint louis",
    "régie juron et tripier",
    "chez nestor",
    "les maisons février",
    "recherche appartement ou maison",
}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def main() -> int:
    with MASTER.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        rows = list(reader)

    for c in NEW_COLS:
        if c not in cols:
            cols.append(c)
    for r in rows:
        for c in NEW_COLS:
            r.setdefault(c, "")

    now = datetime.now(timezone.utc).isoformat()
    n_dup = n_immo = 0

    # ── 1. doublons notaires ─────────────────────────────────────────
    # Cle = nom d'etude + adresse postale, pas le telephone : cinq
    # etudes ont 0000000000 en base, et deux bureaux voisins peuvent
    # partager un standard. Seul un meme bureau a la meme adresse
    # compte comme doublon.
    par_bureau: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("vertical") != "notaires":
            continue
        cle = norm(r.get("company"))
        if cle and "|" in cle:          # le nom contient l'adresse
            par_bureau.setdefault(cle, []).append(r)

    for cle, groupe in par_bureau.items():
        if len(groupe) < 2:
            continue
        tel = re.sub(r"\D", "", groupe[0].get("phone") or "")
        # on prefere l'adresse generique (accueil@, office@, etude@),
        # qui survit aux departs de notaires
        def score(r):
            local = (r.get("email") or "").split("@")[0].lower()
            return 0 if local.startswith(("accueil", "office", "etude", "contact")) else 1
        groupe.sort(key=score)
        for r in groupe[1:]:
            r["send_status"] = "excluded"
            r["last_error"] = f"doublon du bureau (tel {tel})"
            r["updated_at"] = now
            n_dup += 1

    # ── 2. immo hors cible ───────────────────────────────────────────
    for r in rows:
        if r.get("vertical") != "immo":
            continue
        if norm(r.get("company")) not in IMMO_GARDES:
            r["send_status"] = "excluded"
            r["last_error"] = "hors cible (prestataire du secteur, pas une agence)"
            r["updated_at"] = now
            n_immo += 1

    with MASTER.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    reste = [r for r in rows if r.get("send_status") == "pending"]
    n_not = sum(1 for r in reste if r["vertical"] == "notaires")
    n_imm = sum(1 for r in reste if r["vertical"] == "immo")
    print(f"  Doublons notaires exclus : {n_dup}")
    print(f"  Immo hors cible exclus   : {n_immo}")
    print(f"\n  Reste à prospecter : {len(reste)}")
    print(f"     notaires : {n_not}")
    print(f"     immo     : {n_imm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
