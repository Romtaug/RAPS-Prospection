"""
R.A.P.S. SERVICES - BUILD MASTER (nettoyage, Lyon / Rhône 69)
Fusionne les exports immo + notaires -> master CSV, en NE GARDANT QUE le 69.
Ajoute le téléphone. Tracking préservé entre les runs.
"""

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
EXPORTS_DIR = BASE_DIR / "exports"
MASTER_PATH = BASE_DIR / "emailing" / "data" / "raps_contacts_master.csv"

CP_RHONE = re.compile(r"\b69\d{3}\b")
VILLES_RHONE = {
    "lyon", "villeurbanne", "venissieux", "vénissieux", "caluire", "bron",
    "vaulx-en-velin", "saint-priest", "decines", "décines", "meyzieu",
    "rillieux", "oullins", "sainte-foy", "tassin", "ecully", "écully",
    "givors", "saint-genis-laval", "francheville", "craponne", "mions",
    "corbas", "genay", "neuville", "irigny", "feyzin", "chassieu",
    "dardilly", "limonest", "champagne-au-mont-d-or", "albigny",
    "villefranche-sur-saone", "villefranche", "tarare", "brignais",
}


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _in_rhone_notaire(row: dict) -> bool:
    dept = _norm(row.get("department"))
    if "rhone" in dept or "rhône" in dept:
        return True
    return bool(CP_RHONE.search(row.get("address") or ""))


def _in_rhone_immo(row: dict) -> bool:
    adresse = _norm(row.get("adresse"))
    if CP_RHONE.search(adresse):
        return True
    return any(v in adresse for v in VILLES_RHONE)


# (vertical, fichier, col_email, cols_extra, col_company, col_city, col_phone, rank, filtre)
SOURCES = [
    ("notaires", "notaires/annuaire_notaires_france.csv",
     "email", ["emails_all"], "office", "city", "phone", 6, _in_rhone_notaire),
    ("immo", "immo/base_prospection_immomatin.csv",
     "email_principal", ["emails_trouves"], "nom", None, "telephone_principal", 5, _in_rhone_immo),
]

FIELDNAMES = [
    "email", "vertical", "company", "city", "phone", "score_source_rank",
    "email_sent", "sent_at", "send_status", "send_attempts",
    "last_error", "last_subject", "created_at", "updated_at",
]
TRACKING_FIELDS = [
    "email_sent", "sent_at", "send_status", "send_attempts",
    "last_error", "last_subject", "created_at",
]

_EMAIL_SPLIT = re.compile(r"[;,|\s]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(v) -> str:
    return "" if v is None else str(v).strip()


def _valid_email(e: str) -> bool:
    return bool(e and "@" in e and "." in e.split("@")[-1]
                and " " not in e and len(e) <= 254)


def _emails_from(value) -> list[str]:
    out: list[str] = []
    for tok in _EMAIL_SPLIT.split(_clean(value).lower()):
        tok = tok.strip(" ;,|")
        if _valid_email(tok) and tok not in out:
            out.append(tok)
    return out


def collect_prospects() -> dict[str, dict]:
    prospects: dict[str, dict] = {}
    for vertical, rel, col_email, cols_extra, col_company, col_city, col_phone, rank, geo in SOURCES:
        path = EXPORTS_DIR / rel
        if not path.exists():
            print(f"!! {vertical:<10} : export absent ({rel}) - ignore")
            continue
        n_rows = n_geo = n_kept = 0
        with path.open("r", newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                n_rows += 1
                if not geo(row):
                    continue
                n_geo += 1
                emails = _emails_from(row.get(col_email))
                for c in cols_extra:
                    for e in _emails_from(row.get(c)):
                        if e not in emails:
                            emails.append(e)
                for email in emails:
                    existing = prospects.get(email)
                    if existing and int(existing["score_source_rank"]) >= rank:
                        continue
                    prospects[email] = {
                        "email": email,
                        "vertical": vertical,
                        "company": _clean(row.get(col_company)) if col_company else "",
                        "city": _clean(row.get(col_city)) if col_city else "",
                        "phone": _clean(row.get(col_phone)) if col_phone else "",
                        "score_source_rank": str(rank),
                    }
                    n_kept += 1
        print(f"[{vertical:<10}] {n_rows:>5} lignes -> {n_geo:>4} dans le 69 -> {n_kept:>4} emails")
    return prospects


def load_existing_master() -> dict[str, dict]:
    if not MASTER_PATH.exists():
        return {}
    out: dict[str, dict] = {}
    with MASTER_PATH.open("r", newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            email = _clean(row.get("email")).lower()
            if email:
                out[email] = row
    return out


def main() -> int:
    print(f"\nBuild master RAPS (Rhone 69) - {_now()}\n")
    prospects = collect_prospects()
    if not prospects:
        print("Aucun prospect dans le 69 - exports manquants ou vides ?")
        return 1

    existing = load_existing_master()
    now = _now()
    n_new = n_kept_tracking = 0
    merged: list[dict] = []

    for email, p in prospects.items():
        row = {f: "" for f in FIELDNAMES}
        row.update(p)
        old = existing.get(email)
        if old:
            for f in TRACKING_FIELDS:
                row[f] = _clean(old.get(f))
            if _clean(old.get("send_status")):
                n_kept_tracking += 1
        if not row["send_status"]:
            row["send_status"] = "pending"
            row["email_sent"] = "false"
            row["send_attempts"] = "0"
            row["created_at"] = now
            n_new += 1
        row["updated_at"] = now
        merged.append(row)

    n_orphans = 0
    for email, old in existing.items():
        if email not in prospects:
            row = {f: _clean(old.get(f)) for f in FIELDNAMES}
            row["email"] = email
            merged.append(row)
            n_orphans += 1

    merged.sort(key=lambda r: (-int(r.get("score_source_rank") or 0), r["email"]))

    MASTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MASTER_PATH.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        w.writerows(merged)

    n_pending = sum(1 for r in merged if r["send_status"] == "pending")
    n_sent = sum(1 for r in merged if r["send_status"] == "sent")
    print(f"\nMaster ecrit : {len(merged)} contacts (69)")
    print(f"  nouveaux pending  : {n_new}")
    print(f"  tracking preserve : {n_kept_tracking}")
    print(f"  orphelins gardes  : {n_orphans}")
    print(f"  etat : {n_pending} pending / {n_sent} sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
