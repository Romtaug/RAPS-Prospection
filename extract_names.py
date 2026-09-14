"""
R.A.P.S SERVICES - Extraction des noms de notaires.

Deux temps :

  python extract_names.py
      -> ecrit noms_a_valider.csv (les candidats, avec un niveau de
         confiance). Tu ouvres le fichier, tu corriges la colonne
         'nom_valide'. Une case vide = pas de nom, on ecrira
         simplement "Cher Maitre,".

  python extract_names.py --apply
      -> relit noms_a_valider.csv et injecte les noms dans le master.

Le nom sert uniquement a la formule d'appel : "Cher Maitre Baret,".
En cas de doute, laisser vide : "Cher Maitre," tout court est
parfaitement correct et ne choque personne.
"""

import csv
import re
import sys
import unicodedata
from pathlib import Path

BASE = Path(__file__).resolve().parent
MASTER = BASE / "emailing" / "data" / "raps_contacts_master.csv"
REVIEW = BASE / "noms_a_valider.csv"

# Mots qui ne sont jamais un patronyme dans une adresse d'etude
GENERIQUES = {
    "accueil", "office", "etude", "etudes", "contact", "notaire", "notaires",
    "scp", "selarl", "sarl", "sas", "selas", "secretariat", "negociation",
    "info", "mail", "cabinet", "associes", "associe", "notarial", "notariale",
    "service", "compta", "rdv", "standard", "direction", "juridique", "gestion",
    "immo", "immobilier", "formalites", "compta", "courrier",
}
# Villes et zones : evite "althemis.lyon@" -> "Maitre Lyon"
LIEUX = {
    "lyon", "villeurbanne", "caluire", "bron", "ecully", "tassin", "oullins",
    "venissieux", "meyzieu", "rillieux", "givors", "brignais", "dardilly",
    "limonest", "chaponost", "francheville", "craponne", "corbas", "genay",
    "anse", "belleville", "tarare", "vaise", "part", "dieu", "confluence",
    "rhone", "alpes", "ouest", "est", "sud", "nord", "centre", "france",
}


def sansacc(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def depuis_email(email: str, office: str = "") -> str | None:
    """prenom.nom@ ou nom.prenom@ -> le patronyme.

    L'ordre varie d'une etude a l'autre. Quand le nom de l'etude
    contient l'un des deux mots, c'est lui le patronyme : c'est ce qui
    evite "Maitre Antoine" pour l'etude VRIGNAUD Antoine.
    Sinon on prend le second mot, convention la plus frequente.
    """
    local = email.split("@")[0].lower()
    bouts = [b for b in local.split(".") if b]
    if len(bouts) != 2:
        return None
    for b in bouts:
        if not b.isalpha() or len(b) < 4 or b in GENERIQUES or b in LIEUX:
            return None
    dans_office = noms_office(office)
    for b in bouts:
        if sansacc(b) in dans_office:
            return b.capitalize()
    return bouts[1].capitalize()


def noms_office(company: str) -> set[str]:
    """Les mots en capitales du nom d'etude, patronymes probables."""
    c = re.sub(r"\s*\|.*$", "", company or "")
    c = re.sub(r"\s+à\s+[^()]*\(\d{5}\)\s*$", "", c)
    c = re.sub(r"^Office notarial\s*", "", c, flags=re.I)
    mots = re.findall(r"\b[A-ZÀ-Ý][A-ZÀ-Ý'\-]{2,}\b", c)
    return {sansacc(m).lower() for m in mots
            if sansacc(m).lower() not in GENERIQUES | LIEUX}


def lire_master():
    with MASTER.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def ecrire_master(cols, rows):
    with MASTER.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def generer() -> int:
    _, rows = lire_master()
    lignes = []
    for r in rows:
        if r.get("vertical") != "notaires" or r.get("send_status") != "pending":
            continue
        nom = depuis_email(r.get("email", ""), r.get("company", ""))
        if not nom:
            continue
        confirme = sansacc(nom).lower() in noms_office(r.get("company", ""))
        etude = re.sub(r"\s*\|.*$", "", r.get("company", ""))
        etude = re.sub(r"^Office notarial\s*", "", etude, flags=re.I)
        lignes.append({
            "email": r["email"],
            "nom_valide": nom,
            "confiance": "confirmé" if confirme else "à vérifier",
            "etude": etude,
        })
    lignes.sort(key=lambda x: (x["confiance"], x["nom_valide"]))

    with REVIEW.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["email", "nom_valide", "confiance", "etude"])
        w.writeheader()
        w.writerows(lignes)

    n_ok = sum(1 for l in lignes if l["confiance"] == "confirmé")
    print(f"  {REVIEW.name} écrit : {len(lignes)} candidats")
    print(f"     confirmés    : {n_ok}")
    print(f"     à vérifier   : {len(lignes) - n_ok}")
    print("\n  Ouvre le fichier, corrige la colonne 'nom_valide'.")
    print("  Laisse une case VIDE si le nom est douteux : on écrira")
    print("  simplement « Cher Maître, », ce qui est toujours correct.")
    print("\n  Puis : python extract_names.py --apply")
    return 0


def appliquer() -> int:
    if not REVIEW.exists():
        print(f"❌ {REVIEW.name} introuvable. Lance d'abord : python extract_names.py")
        return 1
    with REVIEW.open("r", newline="", encoding="utf-8-sig") as fh:
        valides = {r["email"].strip().lower(): (r.get("nom_valide") or "").strip()
                   for r in csv.DictReader(fh)}

    cols, rows = lire_master()
    if "contact_name" not in cols:
        cols.append("contact_name")
    n = 0
    for r in rows:
        r.setdefault("contact_name", "")
        nom = valides.get((r.get("email") or "").strip().lower())
        if nom:
            r["contact_name"] = nom
            n += 1
        elif nom == "":
            r["contact_name"] = ""
    ecrire_master(cols, rows)

    total = sum(1 for r in rows if r.get("send_status") == "pending"
                and r.get("vertical") == "notaires")
    print(f"  {n} noms injectés dans le master.")
    print(f"  {total - n} études recevront « Cher Maître, » sans nom.")
    return 0


if __name__ == "__main__":
    raise SystemExit(appliquer() if "--apply" in sys.argv else generer())
