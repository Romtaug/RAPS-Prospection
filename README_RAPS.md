# R.A.P.S. SERVICES - Prospection automatique (notaires + agences immo, Rhône 69)

Système de prospection par email pour **R.A.P.S. SERVICES** (nettoyage, Lyon 5e).
Il scrape les **notaires** et **agences immobilières du Rhône (69)**, puis envoie un
mail de présentation personnalisé, en automatique, via GitHub Actions.

Deux angles selon la cible :
- **Notaires** → successions / débarras / syndrome de Diogène + entretien d'étude.
- **Agences immo** → fin de chantier, remise en état avant vente/location, parties communes, vitrines.

---

## 1. Ce qu'il faut télécharger / récupérer

| Élément | Où | Pourquoi |
|---|---|---|
| Ce dossier `RAPS-Prospection/` | (ce zip) | tout le code |
| Un compte **Brevo** (ex-Sendinblue) | brevo.com - offre gratuite = 300 mails/j | l'envoi SMTP |
| Accès au DNS du domaine `raps-services-nettoyage.fr` | chez l'hébergeur du domaine | authentifier l'expéditeur (anti-spam) |
| Un compte **GitHub** | github.com | héberger + automatiser (gratuit) |
| **Python 3.12** | python.org | si tu veux tester en local |

---

## 2. Réglage Brevo (15 min, le plus important pour ne PAS finir en spam)

1. Crée le compte Brevo avec l'email **contact@raps-services-nettoyage.fr**.
2. Menu **Expéditeurs & domaines** → ajoute et **authentifie le domaine**
   `raps-services-nettoyage.fr` : Brevo te donne des enregistrements **SPF, DKIM et DMARC**
   à coller dans le DNS du domaine. Fais-le, attends la validation (coche verte).
3. Menu **SMTP & API** → récupère :
   - le **login SMTP** (ressemble à `xxxxxx@smtp-brevo.com`)
   - la **clé SMTP** (= mot de passe)

> Sans l'authentification du domaine, les mails partent en spam. C'est l'étape à ne pas zapper.

---

## 3. Mise en place sur GitHub

1. Crée un repo (**privé** recommandé - voir §6) et pousse le contenu de ce dossier.
2. Repo → **Settings → Secrets and variables → Actions** → ajoute :
   - `SMTP_LOGIN` = le login SMTP Brevo
   - `SMTP_PASSWORD` = la clé SMTP Brevo
3. C'est tout. Les deux automatisations sont déjà prêtes dans `.github/workflows/`.

---

## 4. Comment ça tourne (automatique)

| Workflow | Quand | Ce qu'il fait |
|---|---|---|
| `scrape_weekly.yml` | **dimanche 06h UTC** | scrape notaires + immo, filtre Rhône, met à jour la base |
| `send_daily.yml` | **lun-sam 07h50 Paris** | envoie 80 mails/jour aux contacts pas encore contactés |

Chaque contact n'est mailé **qu'une fois** (le suivi est gardé dans
`emailing/data/raps_contacts_master.csv`). Quand il n'y a plus de « pending »,
l'envoi s'arrête tout seul jusqu'à la prochaine moisson.

---

## 5. Tester en local (optionnel)

```bash
pip install -r requirements.txt

# 1) Scraper (mets RAPS_TEST=true pour un essai rapide)
RAPS_TEST=true python scrape_raps.py

# 2) Construire la base filtrée Rhône
python build_master_raps.py

# 3) Voir les mails SANS envoyer
SEND_MODE=TEST DRY_RUN=true python emailing/send_raps.py

# 4) Envoi réel d'un lot (après avoir mis les variables Brevo)
export SMTP_LOGIN="...@smtp-brevo.com"
export SMTP_PASSWORD="ta_cle_brevo"
SEND_MODE=MASS DAILY_LIMIT=20 python emailing/send_raps.py
```

Réglages utiles (variables d'environnement) :
- `DAILY_LIMIT` : nb de mails/jour (défaut 80).
- `TEST_RECIPIENT` : à qui le mode TEST envoie l'aperçu.
- `PAUSE_MIN` / `PAUSE_MAX` : pauses entre envois (anti-spam).

---

## 6. Deux points honnêtes à connaître

**RGPD.** On vise des adresses **professionnelles** (étude, agence) pour un service
lié à leur activité → prospection B2B autorisée en France **avec un lien de
désinscription** (déjà présent : « répondez STOP »). En revanche, si le repo passe
**public**, toute la base de contacts devient téléchargeable par tout le monde
(emails de personnes réelles). Garde le repo **privé** (les 2 000 min/mois gratuites
suffisent), ou dé-commente les lignes du `.gitignore` pour ne pas publier les données.

**Source agences immo.** Le scraper `immo` lit immomatin.com, qui mélange pas mal de
plateformes nationales : le filtre Rhône y laissera peu d'agences locales. Les
**notaires** (source notaires.fr, structurée par département) sont bien plus fiables.
Pour récupérer un vrai annuaire d'agences lyonnaises, la meilleure piste est une
requête **Google Places** (« agence immobilière Lyon ») - à brancher plus tard si besoin.

---

## 7. Personnaliser le mail

Tout est dans `emailing/send_raps.py` :
- Couleurs : variables `C_PRIMARY`, `C_DARK`, etc. (mets les couleurs exactes du site).
- Textes des deux cibles : fonction `_pitch_blocks()`.
- Objets : fonction `_subject()`.
- Coordonnées / CTA : en haut du fichier (tél, adresse, lien devis).

Aperçus fournis : `preview_notaires.html` et `preview_immo.html` (ouvre-les dans un navigateur).
