"""
╔══════════════════════════════════════════════════════════════════╗
║      R.A.P.S. SERVICES - SCRAPE HEBDO (immo + notaires)           ║
╠══════════════════════════════════════════════════════════════════╣
║  Lance les deux scrapers en mode complet et écrit les exports.    ║
║  Le filtre Rhône (69) est appliqué APRÈS, par build_master_raps.  ║
║                                                                   ║
║  Env : RAPS_TEST=true → volumes réduits (pour tester vite).       ║
║  Usage : python scrape_raps.py  &&  python build_master_raps.py   ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
from datetime import datetime, timezone

from scrapers import REGISTRY


def _bool(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    test_mode = _bool("RAPS_TEST")
    print(f"\n🔄 Scrape RAPS - test_mode={test_mode} - {datetime.now(timezone.utc).isoformat()}\n")
    exit_code = 0
    for vertical in ("notaires", "immo"):
        print(f"{'='*64}\n▶ {vertical}\n{'='*64}")
        try:
            scraper = REGISTRY[vertical](test_mode=test_mode)
            res = scraper.run(mode="update")
            print(f"✅ {vertical} : +{res.inserted} / maj {res.updated} / = {res.unchanged}")
        except Exception as exc:
            exit_code = 1
            print(f"❌ {vertical} : {exc!r}")
    print("\n➡️  Pense à lancer : python build_master_raps.py")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
