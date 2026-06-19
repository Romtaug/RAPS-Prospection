"""
scrapers/notaires.py — version RAPS (Rhône 69, bug pagination corrigé)
"""

from __future__ import annotations

import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.scraper_base import ExportConfig, ScraperBase
from core.utils import (
    clean_text,
    decode_cfemail,
    extract_emails,
    extract_phones,
    pick_best_email,
)


BASE_URL = "https://www.notaires.fr"

START_URLS_FULL = [
    "https://www.notaires.fr/fr/annuaire/auvergne-rhone-alpes",
]

START_URLS_TEST = [
    "https://www.notaires.fr/fr/annuaire/auvergne-rhone-alpes",
]

TEST_MAX_ANNUAIRE_PAGES = 30
TEST_MAX_OFFICES = 20

SLEEP_CRAWL_MIN = 0.3
SLEEP_CRAWL_MAX = 0.8
SLEEP_OFFICE_MIN = 0.4
SLEEP_OFFICE_MAX = 1.0

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
}


def _normalize_url(url: str) -> str | None:
    """Force l'URL sur www.notaires.fr + canonicalise la pagination."""
    url, _ = urldefrag(url)
    parsed = urlparse(url)

    if parsed.netloc and parsed.netloc not in {"www.notaires.fr", "notaires.fr"}:
        return None

    full = urljoin(BASE_URL, parsed.path)
    # On ne garde qu'UN SEUL page=N (le premier) -> empêche l'empilement
    # ?page=4&page=2&page=... qui faisait exploser la file à l'infini.
    if parsed.query:
        from urllib.parse import parse_qs
        pages = [p for p in parse_qs(parsed.query).get("page", []) if p.isdigit()]
        if pages:
            full += "?page=" + pages[0]
    return full.rstrip("/")


def _is_annuaire_url(url: str) -> bool:
    return urlparse(url).path.startswith("/fr/annuaire/")


def _is_office_url(url: str) -> bool:
    return urlparse(url).path.startswith("/fr/office/")


# On ne crawle QUE le Rhône (69) + métropole de Lyon.
_RHONE_PATHS = (
    "/fr/annuaire/auvergne-rhone-alpes/rhone",
    "/fr/annuaire/auvergne-rhone-alpes/metropole-de-lyon",
)


def _is_rhone_annuaire(url: str) -> bool:
    p = urlparse(url).path.rstrip("/")
    return any(p == base or p.startswith(base) for base in _RHONE_PATHS)


def _extract_breadcrumbs(soup: BeautifulSoup) -> list[str]:
    crumbs: list[str] = []
    for el in soup.select(".breadcrumb__list span, .breadcrumb__list a"):
        t = clean_text(el.get_text(" ", strip=True))
        if t and t not in crumbs:
            crumbs.append(t)
    return crumbs


def _extract_address(soup: BeautifulSoup) -> str:
    selectors = [
        "address",
        "[class*=address]",
        "[class*=adresse]",
        ".office-sheet__address",
        ".professional__address",
        ".contact__address",
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            t = clean_text(el.get_text(" ", strip=True))
            if len(t) > 10:
                return t
    return ""


class NotairesScraper(ScraperBase):
    VERTICAL = "notaires"
    TABLE = "notaires"
    NATURAL_KEY = "url"

    BUSINESS_COLUMNS = [
        "office",
        "email",
        "emails_all",
        "phone",
        "phones_all",
        "address",
        "region",
        "department",
        "city",
        "url",
        "source",
        "date_scraping",
    ]

    EXPORT = ExportConfig(
        csv_path=Path("exports/notaires/annuaire_notaires_france.csv"),
        xlsx_path=Path("exports/notaires/annuaire_notaires_france.xlsx"),
        jsonl_path=Path("exports/notaires/annuaire_notaires_france.jsonl"),
        email_column="email",
        table_name="BaseNotaires",
        sheet_name="Notaires",
    )

    def __init__(
        self,
        data_dir=None,
        test_mode=False,
        max_pages=None,
        max_offices=None,
    ):
        if data_dir is not None:
            super().__init__(data_dir=data_dir, test_mode=test_mode)
        else:
            super().__init__(test_mode=test_mode)

        if test_mode:
            self.max_pages = max_pages or TEST_MAX_ANNUAIRE_PAGES
            self.max_offices = max_offices or TEST_MAX_OFFICES
        else:
            self.max_pages = max_pages
            self.max_offices = max_offices

        # RAPS : démarrage direct sur le Rhône (69) + métropole de Lyon.
        self.start_urls = [
            "https://www.notaires.fr/fr/annuaire/auvergne-rhone-alpes/rhone",
            "https://www.notaires.fr/fr/annuaire/auvergne-rhone-alpes/metropole-de-lyon",
        ]
        if self.max_pages is None:
            self.max_pages = 2000  # filet de sécurité

        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        retry = Retry(
            total=5,
            connect=5,
            read=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def _fetch(self, url: str) -> str:
        r = self._session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text

    def _extract_links(self, page_html: str, current_url: str) -> set[str]:
        soup = BeautifulSoup(page_html, "lxml")
        links: set[str] = set()

        for a in soup.find_all("a", href=True):
            u = _normalize_url(urljoin(current_url, a["href"]))
            if u:
                links.add(u)

        for m in re.findall(
            r"""["'](/fr/(?:annuaire|office)/[^"'?#<>\s]+)["']""",
            page_html,
        ):
            u = _normalize_url(urljoin(BASE_URL, m))
            if u:
                links.add(u)

        return links

    def _add_pagination_urls(self, url: str, html_text: str) -> set[str]:
        urls: set[str] = set()
        soup = BeautifulSoup(html_text, "lxml")

        for a in soup.select("a[href*='page=']"):
            u = _normalize_url(urljoin(url, a.get("href")))
            if u:
                urls.add(u)

        for m in set(re.findall(r"[?&]page=(\d+)", html_text)):
            # construit l'URL À PARTIR DU CHEMIN -> jamais d'empilement
            base = url.split("?")[0]
            u = _normalize_url(f"{base}?page={m}")
            if u:
                urls.add(u)

        return urls

    def _crawl_annuaire(self) -> list[str]:
        seen: set[str] = set()
        office_urls: set[str] = set()
        queue: list[str] = []

        for u in self.start_urls:
            n = _normalize_url(u)
            if n and n not in seen and n not in queue:
                queue.append(n)

        done = 0
        max_pages = self.max_pages if self.max_pages is not None else 1_000_000

        self.log.info("=" * 60)
        self.log.info("ETAPE 1 : CRAWL ANNUAIRE (Rhone 69)")
        self.log.info("Start URLs    : %d", len(self.start_urls))
        self.log.info("=" * 60)

        while queue and done < max_pages:
            url = queue.pop(0)
            if url in seen:
                continue

            try:
                page = self._fetch(url)
                seen.add(url)
                done += 1

                links = self._extract_links(page, url)
                links.update(self._add_pagination_urls(url, page))

                new_annuaire = 0
                new_offices = 0

                for link in links:
                    if _is_office_url(link):
                        before = len(office_urls)
                        office_urls.add(link)
                        if len(office_urls) > before:
                            new_offices += 1
                    elif (
                        _is_annuaire_url(link)
                        and _is_rhone_annuaire(link)
                        and link not in seen
                        and link not in queue
                    ):
                        queue.append(link)
                        new_annuaire += 1

                self.log.info(
                    "[CRAWL] pages=%d | queue=%d | offices=%d "
                    "| +annuaire=%d | +offices=%d | url=%s",
                    done, len(queue), len(office_urls),
                    new_annuaire, new_offices, url,
                )

                time.sleep(random.uniform(SLEEP_CRAWL_MIN, SLEEP_CRAWL_MAX))

            except Exception as e:
                self.log.warning("[ERREUR CRAWL] %s -> %r", url, e)

        self.log.info("[CRAWL FINI] offices trouves=%d", len(office_urls))
        return sorted(office_urls)

    def _scrape_office(self, url: str) -> dict:
        page = self._fetch(url)
        soup = BeautifulSoup(page, "lxml")
        text = soup.get_text(" ", strip=True)

        h1 = soup.find("h1")
        title = clean_text(h1.get_text(" ", strip=True)) if h1 else ""
        if not title and soup.find("title"):
            title = clean_text(
                soup.find("title").get_text()
            ).replace("| Notaires de France", "").strip()

        emails = extract_emails(page + " " + text)

        cf_email = soup.select_one(".__cf_email__")
        if cf_email and cf_email.get("data-cfemail"):
            decoded = decode_cfemail(cf_email.get("data-cfemail"))
            if decoded and decoded not in emails:
                emails.insert(0, decoded)

        phones = extract_phones(text)
        crumbs = _extract_breadcrumbs(soup)

        row = {col: "" for col in self.BUSINESS_COLUMNS}
        row.update({
            "office": title,
            "email": pick_best_email(emails),
            "emails_all": ";".join(emails),
            "phone": phones[0] if phones else "",
            "phones_all": ";".join(phones),
            "address": _extract_address(soup),
            "region": crumbs[1] if len(crumbs) > 1 else "",
            "department": crumbs[2] if len(crumbs) > 2 else "",
            "city": crumbs[3] if len(crumbs) > 3 else "",
            "url": url,
            "source": "notaires.fr",
            "date_scraping": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        return row

    def iter_records(self) -> Iterator[dict]:
        office_urls = self._crawl_annuaire()

        skip = getattr(self, "skip_urls", None)
        if skip:
            before = len(office_urls)
            office_urls = [u for u in office_urls if u not in skip]
            self.log.info("Skip deja en base : %d -> %d nouveaux", before, len(office_urls))

        if self.max_offices is not None:
            office_urls = office_urls[: self.max_offices]

        total = len(office_urls)
        self.log.info("=" * 60)
        self.log.info("ETAPE 2 : SCRAPING OFFICES (%d a scraper)", total)
        self.log.info("=" * 60)

        for i, url in enumerate(office_urls, 1):
            try:
                row = self._scrape_office(url)
            except Exception as e:
                self.log.warning("[ERREUR OFFICE] %s -> %r", url, e)
                continue

            self.log.info(
                "[OFFICE] %d/%d | email=%s | office=%s",
                i, total,
                "oui" if row.get("email") else "non",
                (row.get("office") or "")[:80],
            )
            yield row

            time.sleep(random.uniform(SLEEP_OFFICE_MIN, SLEEP_OFFICE_MAX))


if __name__ == "__main__":
    NotairesScraper(test_mode=True).run(mode="update")
