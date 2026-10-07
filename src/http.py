"""Client HTTP : timeouts, retries avec backoff, gestion des limites GitHub, compteur de requêtes."""
from __future__ import annotations

import logging
import threading
import time

import httpx

try:  # optionnel : utile derrière un proxy d'entreprise / antivirus (Windows)
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover
    pass

log = logging.getLogger("ai_radar")
UA = "ai-radar/1.0"
STAR_ACCEPT = "application/vnd.github.star+json"


def plain_get(url: str, *, params=None, headers=None, timeout: float = 20, retries: int = 3,
              backoff: float = 1.5, ok_statuses: tuple = (200,)) -> httpx.Response:
    """GET avec retries (réseau, 5xx, 429). Lève l'exception de la dernière tentative."""
    hdrs = {"User-Agent": UA, **(headers or {})}
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = httpx.get(url, params=params, headers=hdrs, timeout=timeout, follow_redirects=True)
            if r.status_code in ok_statuses:
                return r
            if r.status_code == 429 or r.status_code >= 500:
                wait = float(r.headers.get("retry-after", backoff ** (attempt + 1)))
                last_exc = RuntimeError(f"HTTP {r.status_code}")
                time.sleep(min(wait, 30))
                continue
            return r
        except httpx.HTTPError as exc:
            last_exc = exc
            time.sleep(backoff ** (attempt + 1))
    raise RuntimeError(f"échec de la requête après {retries + 1} essais ({type(last_exc).__name__})")


class GitHubClient:
    """Client de l'API REST GitHub avec budget de requêtes et gestion des rate limits.

    Toute erreur définitive renvoie `None` (jamais d'exception) : les appelants dégradent proprement.
    """

    def __init__(self, token: str | None, max_requests: int = 900, search_interval: float = 2.2,
                 timeout: float = 20):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": UA}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url="https://api.github.com", headers=headers, timeout=timeout,
                                 follow_redirects=True)
        self.authenticated = bool(token)
        self.max_requests = max_requests
        self.search_interval = search_interval if token else max(search_interval, 6.5)
        self.calls = 0
        self.search_calls = 0
        self.exhausted = False
        self._lock = threading.Lock()
        self._search_lock = threading.Lock()
        self._last_search = 0.0

    # -- budget -----------------------------------------------------------
    def _take(self) -> bool:
        with self._lock:
            if self.calls >= self.max_requests:
                if not self.exhausted:
                    log.warning("Budget API épuisé (%d requêtes) : les mesures restantes sont ignorées.",
                                self.max_requests)
                self.exhausted = True
                return False
            self.calls += 1
            return True

    def init_budget(self) -> None:
        """Lit /rate_limit (gratuit) et plafonne le budget au quota réellement disponible."""
        try:
            r = self.http.get("/rate_limit")
            if r.status_code == 200:
                core = r.json()["resources"]["core"]
                remaining = int(core["remaining"])
                self.max_requests = max(0, min(self.max_requests, remaining - 30))
                log.info("Quota API : %d/%d restants → budget de cette exécution : %d requêtes",
                         remaining, core["limit"], self.max_requests)
        except Exception as exc:  # noqa: BLE001
            log.warning("Lecture de /rate_limit impossible (%s)", type(exc).__name__)

    # -- requêtes ---------------------------------------------------------
    def get(self, path: str, params: dict | None = None, headers: dict | None = None,
            search: bool = False, retries: int = 3) -> httpx.Response | None:
        for attempt in range(retries + 1):
            if not self._take():
                return None
            if search:
                with self._search_lock:
                    wait = self._last_search + self.search_interval - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                    self._last_search = time.monotonic()
                    self.search_calls += 1
            try:
                r = self.http.get(path, params=params, headers=headers)
            except httpx.HTTPError as exc:
                log.debug("Erreur réseau %s (%s)", path, type(exc).__name__)
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (403, 429):
                ra = r.headers.get("retry-after")
                remaining = r.headers.get("x-ratelimit-remaining")
                if ra:
                    wait = float(ra)
                elif remaining == "0":
                    wait = float(r.headers.get("x-ratelimit-reset", 0)) - time.time() + 1
                else:
                    wait = 2 ** (attempt + 2)  # limite secondaire / abus : on temporise
                if wait > 65:
                    log.warning("Rate limit GitHub : attente de %.0f s trop longue, on abandonne.", wait)
                    self.exhausted = True
                    return None
                time.sleep(max(wait, 1))
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            return r
        return None

    def json(self, path: str, params: dict | None = None, headers: dict | None = None,
             search: bool = False):
        r = self.get(path, params=params, headers=headers, search=search)
        if r is not None and r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                return None
        return None
