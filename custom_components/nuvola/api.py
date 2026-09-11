from __future__ import annotations

import json
import logging
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import aiohttp

from .const import AUTH_HOST, BASE_URL, USER_AGENT

_LOGGER = logging.getLogger(__name__)


class NuvolaAuthError(Exception):
    """Authentication/API error."""


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.inputs = {}
        self.in_form = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag.lower() == "form" and not self.in_form:
            self.in_form = True
            self.action = a.get("action")
        elif self.in_form and tag.lower() == "input" and a.get("name"):
            self.inputs[a["name"]] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag.lower() == "form":
            self.in_form = False


class NuvolaAPI:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.token = None
        self.session = None
        self.student = None

    async def _session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=45),
                cookie_jar=aiohttp.CookieJar(),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
                },
            )
        return self.session

    @staticmethod
    def _safe_url(url):
        try:
            p = urlparse(url)
            return f"{p.scheme}://{p.netloc}{p.path}"
        except Exception:
            return "<invalid-url>"

    @staticmethod
    def _safe_title(body):
        match = re.search(r"<title[^>]*>(.*?)</title>", body or "", re.I | re.S)
        if not match:
            return None
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()
        return title[:160] or None

    @staticmethod
    def _markers(body):
        text = (body or "").lower()
        names = (
            "openid", "oauth", "oidc", "keycloak", "authentication-service",
            "login_check", "password", "username", "csrf",
        )
        return [name for name in names if name in text]

    async def _text(self, response, limit=12000):
        return (await response.text(errors="replace"))[:limit]

    async def _auth_start(self):
        s = await self._session()

        for path in ("/connect/authentication-service", "/login"):
            async with s.get(BASE_URL + path, allow_redirects=False) as r:
                body = await self._text(r)
                location = r.headers.get("Location")
                _LOGGER.debug(
                    "Nuvola auth start %s: HTTP %s location=%s type=%s body=%r",
                    path, r.status, self._safe_url(location or ""),
                    r.headers.get("Content-Type"), body[:800],
                )

                if location:
                    return urljoin(str(r.url), location)

                match = re.search(
                    r'https?://auth\.nuvola\.madisoft\.it/[^"\']+',
                    body, re.I,
                )
                if match:
                    return match.group(0)

                match = re.search(
                    r'(?:href|location)\s*=\s*["\']([^"\']*authentication-service[^"\']*)',
                    body, re.I,
                )
                if match:
                    return urljoin(str(r.url), match.group(1))

        raise NuvolaAuthError(
            "Nuvola non ha restituito l'URL del servizio di autenticazione"
        )

    async def login(self):
        """Authenticate and obtain the token used by the student API.

        Nuvola's current web frontend may use OIDC/Keycloak, while the
        student API used by several unofficial clients still exposes the
        session-based ``/login_check`` + ``/api-studente/v1/login-from-web``
        flow. We try the latter directly so a frontend OIDC redirect does not
        prevent a valid legacy API session from being established.
        """
        s = await self._session()

        # Establish the Nuvola session first.
        async with s.get(BASE_URL + "/login", allow_redirects=False) as r:
            body = await self._text(r, 10000)
            location = r.headers.get("Location")
            _LOGGER.debug(
                "Nuvola diagnostic 0.6.4 /login: HTTP %s location=%s type=%s title=%s markers=%s",
                r.status, self._safe_url(location or ""),
                r.headers.get("Content-Type"), self._safe_title(body), self._markers(body),
            )

            # The legacy login form requires Symfony's CSRF token. Current
            # unofficial Nuvola clients extract it from /login and send it
            # back to /login_check together with _username/_password.
            parser = _FormParser()
            parser.feed(body)
            csrf_token = parser.inputs.get("_csrf_token")
            form_action = parser.action

            _LOGGER.debug(
                "Nuvola diagnostic 0.6.4 /login form: action=%s input_names=%s csrf_present=%s",
                self._safe_url(urljoin(str(r.url), form_action)) if form_action else None,
                sorted(parser.inputs),
                bool(csrf_token),
            )

            if location and AUTH_HOST in urlparse(urljoin(str(r.url), location)).netloc.lower():
                raise NuvolaAuthError(
                    "Il portale Nuvola utilizza il nuovo login OIDC/Keycloak: "
                    "la pagina /login non espone più il modulo legacy."
                )

            if not csrf_token:
                # Diagnostic build: do not stop here. The current Nuvola
                # frontend may no longer expose the legacy Symfony form.
                # Continue only when /login_check can still be probed, so the
                # logs reveal the actual authentication path without logging
                # credentials, cookies or tokens.
                _LOGGER.warning(
                    "Nuvola diagnostic 0.6.4: nessun _csrf_token trovato in /login; "
                    "verifico comunque /login_check per determinare il flusso di autenticazione."
                )
                csrf_token = None

        # Try the historical Symfony-style endpoint directly. It may still
        # be available even when the browser UI has moved to OIDC.
        login_check = BASE_URL + "/login_check"
        payload = {
            "_username": self.username,
            "_password": self.password,
        }
        if csrf_token:
            payload["_csrf_token"] = csrf_token

        async with s.post(
            login_check,
            data=payload,
            allow_redirects=False,
            headers={
                "Referer": BASE_URL + "/login",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
            },
        ) as r:
            response_body = await self._text(r, 12000)
            location = r.headers.get("Location")
            _LOGGER.warning(
                "Nuvola diagnostic 0.6.4 /login_check: HTTP %s location=%s type=%s cookies=%s title=%s markers=%s",
                r.status, self._safe_url(location or ""),
                r.headers.get("Content-Type"),
                sorted(c.key for c in s.cookie_jar),
                self._safe_title(response_body), self._markers(response_body),
            )

            if r.status in (401, 403):
                raise NuvolaAuthError(
                    "login_check ha rifiutato la richiesta (HTTP %s). "
                    "Il tenant potrebbe richiedere il nuovo flusso OIDC/Keycloak." % r.status
                )

            if location:
                next_url = urljoin(str(r.url), location)
                host = urlparse(next_url).netloc.lower()
                # A redirect back into Nuvola normally means the session was
                # accepted. A redirect to Keycloak means the legacy endpoint
                # is no longer usable on this tenant.
                if AUTH_HOST in host:
                    raise NuvolaAuthError(
                        "Il portale Nuvola utilizza ora il nuovo login OIDC/Keycloak "
                        "e non espone più il login automatico /login_check."
                    )
            elif r.status not in (200, 204):
                raise NuvolaAuthError(
                    f"login_check ha restituito HTTP {r.status} senza redirect; "
                    "vedere il log diagnostico 0.6.3."
                )

        # The legacy web flow requires entering the student area before
        # converting the authenticated session into the API JWT. This is
        # also what current unofficial Nuvola clients do.
        async with s.get(
            BASE_URL + "/area-studente",
            allow_redirects=True,
            headers={
                "Referer": BASE_URL + "/login",
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
            },
        ) as r:
            area_body = await self._text(r, 5000)
            _LOGGER.warning(
                "Nuvola diagnostic 0.6.4 /area-studente: HTTP %s final_url=%s type=%s cookies=%s title=%s markers=%s",
                r.status, self._safe_url(str(r.url)),
                r.headers.get("Content-Type"),
                sorted(c.key for c in s.cookie_jar),
                self._safe_title(area_body), self._markers(area_body),
            )
            if AUTH_HOST in urlparse(str(r.url)).netloc.lower():
                raise NuvolaAuthError(
                    "La sessione Nuvola è stata reindirizzata al nuovo servizio OIDC/Keycloak "
                    "durante l'accesso all'area studente."
                )

        # Convert the authenticated web session into the API JWT.
        async with s.get(
            BASE_URL + "/api-studente/v1/login-from-web",
            allow_redirects=True,
            headers={
                "Referer": BASE_URL + "/area-studente",
                "Accept": "application/json, text/plain, */*",
            },
        ) as r:
            body = await self._text(r, 8000)
            _LOGGER.warning(
                "Nuvola diagnostic 0.6.4 login-from-web: HTTP %s final_url=%s type=%s title=%s markers=%s",
                r.status, self._safe_url(str(r.url)),
                r.headers.get("Content-Type"), self._safe_title(body), self._markers(body),
            )

            if AUTH_HOST in urlparse(str(r.url)).netloc.lower():
                raise NuvolaAuthError(
                    "La sessione Nuvola è stata reindirizzata al nuovo servizio OIDC/Keycloak."
                )
            if r.status >= 400:
                raise NuvolaAuthError(f"login-from-web HTTP {r.status}")
            if not body.strip():
                raise NuvolaAuthError(
                    "login-from-web ha restituito una risposta vuota; "
                    "la sessione web non ha prodotto un token API."
                )

            try:
                data = json.loads(body)
            except json.JSONDecodeError as err:
                raise NuvolaAuthError(
                    "login-from-web non ha restituito JSON; il log diagnostico 0.6.3 "
                    "contiene URL finale, Content-Type e un estratto sicuro della risposta."
                ) from err

            if isinstance(data, dict):
                self.token = data.get("token") or data.get("access_token")
            elif isinstance(data, str):
                self.token = data

            if not self.token:
                raise NuvolaAuthError(
                    "login-from-web non ha restituito un token API."
                )

    async def _json(self, path, params=None):
        if not self.token:
            await self.login()

        s = await self._session()
        async with s.get(
            BASE_URL + path,
            params=params,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
        ) as r:
            body = await self._text(r, 12000)
            if r.status >= 400:
                _LOGGER.error(
                    "Nuvola API %s: HTTP %s body=%r",
                    path, r.status, body[:1500],
                )
                if r.status in (401, 403):
                    self.token = None
                    raise NuvolaAuthError(
                        f"Token Nuvola rifiutato da {path} (HTTP {r.status})"
                    )
                raise NuvolaAuthError(f"API Nuvola {path} HTTP {r.status}")

            try:
                return json.loads(body)
            except json.JSONDecodeError as err:
                raise NuvolaAuthError(
                    f"API Nuvola {path} non ha restituito JSON"
                ) from err

    async def students(self):
        data = await self._json("/api-studente/v1/alunni")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("alunni", "students", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
        return []

    async def student(self):
        students = await self.students()
        self.student = students[0] if students else None
        return self.student

    async def periods(self, student_id):
        data = await self._json(
            f"/api-studente/v1/alunno/{student_id}/frazioni-temporali"
        )
        return data.get("frazioni_temporali", data) if isinstance(data, dict) else data

    async def grades(self, student_id, period_id):
        return await self._json(
            f"/api-studente/v1/alunno/{student_id}/frazione-temporale/{period_id}/voti/materie"
        )

    async def absences(self, student_id):
        return await self._json(
            f"/api-studente/v1/alunno/{student_id}/assenze"
        )

    async def notes(self, student_id):
        return await self._json(
            f"/api-studente/v1/alunno/{student_id}/note"
        )

    async def homework(self, student_id, when=None):
        when = when or date.today().isoformat()
        return await self._json(
            f"/api-studente/v1/alunno/{student_id}/compito/elenco/{when}"
        )

    async def fetch_all(self):
        students = await self.students()
        result = {
            "students": students,
            "student": students[0] if students else None,
            "periods": [],
            "grades": [],
            "absences": [],
            "notes": {},
            "homework": [],
        }

        if not students:
            return result

        sid = students[0].get("id") or students[0].get("id_alunno")
        if sid is None:
            return result

        result["student"] = students[0]

        try:
            periods = await self.periods(sid)
            result["periods"] = periods if isinstance(periods, list) else []
        except Exception as err:
            _LOGGER.warning("Nuvola periods unavailable: %s", err)

        if result["periods"]:
            first = result["periods"][-1]
            pid = (
                first.get("id")
                or first.get("id_frazione_temporale")
                or first.get("codice")
            )
            if pid is not None:
                try:
                    result["grades"] = await self.grades(sid, pid)
                except Exception as err:
                    _LOGGER.warning("Nuvola grades unavailable: %s", err)

        try:
            result["absences"] = await self.absences(sid)
        except Exception as err:
            _LOGGER.warning("Nuvola absences unavailable: %s", err)

        try:
            result["notes"] = await self.notes(sid)
        except Exception as err:
            _LOGGER.warning("Nuvola notes unavailable: %s", err)

        try:
            result["homework"] = await self.homework(sid)
        except Exception as err:
            _LOGGER.warning("Nuvola homework unavailable: %s", err)

        return result

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
