from __future__ import annotations

import json
import logging
import re
from html import unescape
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

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

    @staticmethod
    def _html_diagnostics(body):
        """Return safe structural diagnostics for the Keycloak HTML page."""
        text = body or ""
        lower = text.lower()
        return {
            "length": len(text),
            "forms": len(re.findall(r"<form\b", lower)),
            "inputs": len(re.findall(r"<input\b", lower)),
            "has_login_form": "kc-form-login" in lower,
            "has_authenticate_action": "login-actions/authenticate" in lower,
            "has_username": re.search(r'name\s*=\s*[\"\']username[\"\']', lower) is not None,
            "has_password": re.search(r'name\s*=\s*[\"\']password[\"\']', lower) is not None,
            "markers": NuvolaAPI._markers(text),
        }

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
        """Authenticate through Nuvola's current OIDC/Keycloak web flow.

        The current Nuvola frontend starts authentication at
        ``/connect/authentication-service`` and redirects to Keycloak. The
        Keycloak login form contains the ephemeral ``session_code``,
        ``execution`` and ``tab_id`` values in its form action/hidden fields.
        We submit that form with the supplied credentials and let aiohttp
        follow the resulting redirects back to Nuvola. The callback establishes
        the authenticated ``nuvola`` web-session cookie; the existing
        ``login-from-web`` endpoint then exchanges that session for the API
        token used by the student API.
        """
        s = await self._session()

        # 1. Start the same authentication flow used by the Nuvola web UI.
        async with s.get(
            BASE_URL + "/connect/authentication-service",
            allow_redirects=False,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
            },
        ) as r:
            body = await self._text(r, 12000)
            location = r.headers.get("Location")
            start_url = urljoin(str(r.url), location) if location else str(r.url)
            _LOGGER.debug(
                "Nuvola OIDC  auth start: HTTP %s location=%s type=%s title=%s",
                r.status,
                self._safe_url(start_url),
                r.headers.get("Content-Type"),
                self._safe_title(body),
            )

            if not location and r.status >= 400:
                raise NuvolaAuthError(
                    f"Avvio autenticazione Nuvola fallito (HTTP {r.status})"
                )

        # 2. Load the Keycloak login page. If the auth-start response was not
        # a redirect, the page itself may contain the authentication URL.
        auth_url = start_url
        if AUTH_HOST not in urlparse(auth_url).netloc.lower():
            match = re.search(
                r'https?://auth\.nuvola\.madisoft\.it/[^"\'\s<>]+',
                body or "",
                re.I,
            )
            if match:
                auth_url = match.group(0)
            else:
                raise NuvolaAuthError(
                    "Nuvola non ha restituito l'URL Keycloak del servizio di autenticazione"
                )

        async with s.get(
            auth_url,
            allow_redirects=True,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
            },
        ) as r:
            login_body = await self._text(r, 20000)
            final_url = str(r.url)
            _LOGGER.warning(
                "[0.8.2 DIAG] Nuvola OIDC Keycloak login page: HTTP %s final_url=%s type=%s title=%s diagnostics=%s",
                r.status,
                self._safe_url(final_url),
                r.headers.get("Content-Type"),
                self._safe_title(login_body),
                self._html_diagnostics(login_body),
            )

            if r.status >= 400:
                raise NuvolaAuthError(
                    f"Pagina di autenticazione Nuvola non disponibile (HTTP {r.status})"
                )

            parser = _FormParser()
            parser.feed(login_body)
            form_action = parser.action
            input_names = sorted(parser.inputs)

            # The Nuvola Keycloak theme currently exposes the login action in
            # the HTML, but its username/password controls are rendered by the
            # frontend rather than as a literal <form>/<input> tree. This is
            # why aiohttp can see the login-actions/authenticate URL while the
            # simple HTML form parser reports forms=0 and inputs=0.
            #
            # Prefer a real form when present. Otherwise extract the standard
            # Keycloak login action directly from the page and submit the same
            # username/password fields that Firefox sends.
            action_url = None
            payload = {}
            username_name = "username"
            password_name = "password"

            if form_action:
                action_url = urljoin(final_url, form_action)
                payload.update(parser.inputs)
                username_name = "username" if "username" in parser.inputs else "_username"
                password_name = "password" if "password" in parser.inputs else "_password"
            else:
                page = unescape(login_body).replace(r"\/", "/")
                # Nuvola's current Keycloak theme renders the login controls
                # with JavaScript, so there may be no literal HTML form.
                # Collect all authenticate URLs and prefer the one containing
                # the per-login session_code/execution/client_id/tab_id.
                candidates = re.findall(
                    r"(?:https?://[^\"'\s<>]*login-actions/authenticate[^\"'\s<>]*|/[^\"'\s<>]*login-actions/authenticate[^\"'\s<>]*)",
                    page,
                    re.I,
                )
                normalized = []
                for candidate in candidates:
                    candidate = candidate.replace("\\u0026", "&").replace("\\x26", "&")
                    candidate = candidate.replace("&amp;", "&")
                    normalized.append(urljoin(final_url, candidate))

                def _has_full_session_action(url):
                    keys = {key for key, _value in parse_qsl(urlparse(url).query, keep_blank_values=True)}
                    return {"session_code", "execution", "client_id", "tab_id"}.issubset(keys)

                full_candidates = [url for url in normalized if _has_full_session_action(url)]
                if full_candidates:
                    action_url = full_candidates[0]
                elif normalized:
                    action_url = normalized[0]

                page_has_session_code = bool(re.search(r"session_code", page, re.I))


            if not action_url or "login-actions/authenticate" not in action_url:
                raise NuvolaAuthError(
                    "La pagina Keycloak non contiene l'URL del modulo di autenticazione"
                )

            if password_name not in parser.inputs and form_action:
                raise NuvolaAuthError(
                    "Il modulo Keycloak non contiene il campo password"
                )

            payload[username_name] = self.username
            payload[password_name] = self.password
            # Firefox sends credentialId explicitly, even when it is empty.
            # Keycloak accepts it as an optional field, but Nuvola's current
            # Keycloak theme submits it and some authentication handlers can
            # distinguish the browser request from a reduced payload.
            payload.setdefault("credentialId", "")

            # The browser's POST action is a Keycloak-generated URL carrying
            # ephemeral session parameters (session_code, execution,
            # client_id, tab_id). Never log their values, but verify that the
            # extracted action actually contains them before submitting.
            action_query_keys = sorted({
                key for key, _value in parse_qsl(urlparse(action_url).query, keep_blank_values=True)
            })

            action_query_keys = sorted({key for key, _value in parse_qsl(urlparse(action_url).query, keep_blank_values=True)})
            has_full_action = {"session_code", "execution", "client_id", "tab_id"}.issubset(set(action_query_keys))

            _LOGGER.warning(
                "[0.8.2 DIAG] Nuvola OIDC Keycloak form: action=%s input_names=%s username_field=%s password_field_present=%s parser_form=%s candidates=%d page_has_session_code=%s",
                self._safe_url(action_url),
                input_names,
                username_name,
                password_name in payload,
                bool(form_action),
                len(normalized) if not form_action else 1,
                page_has_session_code if not form_action else False,
            )
            _LOGGER.warning(
                "[0.8.2 DIAG] Nuvola OIDC Keycloak action query keys: %s full_session_action=%s",
                action_query_keys,
                has_full_action,
            )
            if not has_full_action and not form_action:
                raise NuvolaAuthError(
                    "La pagina Keycloak non espone l'URL completo del login con session_code"
                )

        # 3. Submit credentials. Do not log the POST body: it contains the
        # user's password and ephemeral Keycloak session parameters.
        async with s.post(
            action_url,
            data=payload,
            allow_redirects=True,
            headers={
                "Referer": auth_url,
                "Origin": AUTH_HOST,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
            },
        ) as r:
            post_body = await self._text(r, 8000)
            final_url = str(r.url)
            _LOGGER.warning(
                "[0.8.2 DIAG] Nuvola OIDC credential submit: HTTP %s final_url=%s type=%s title=%s cookies=%s",
                r.status,
                self._safe_url(final_url),
                r.headers.get("Content-Type"),
                self._safe_title(post_body),
                sorted(c.key for c in s.cookie_jar),
            )

            # A successful Keycloak login redirects to Nuvola's
            # /connect/authentication-service/check callback. aiohttp follows
            # it automatically and the callback sets the authenticated
            # ``nuvola`` cookie. If we end up back on the login page, the
            # credentials were rejected or the flow changed.
            if AUTH_HOST in urlparse(final_url).netloc.lower():
                markers = self._markers(post_body)
                if "password" in markers or "username" in markers or "keycloak" in markers:
                    raise NuvolaAuthError(
                        "Autenticazione Nuvola rifiutata dal servizio Keycloak"
                    )

            if BASE_URL not in final_url:
                raise NuvolaAuthError(
                    "Il flusso OIDC Nuvola non è tornato al portale dopo il login"
                )

        # 4. The browser reaches the tutor area after the OIDC callback. This
        # explicit request is intentionally kept: it also verifies that the
        # callback created a usable Nuvola session cookie.
        async with s.get(
            BASE_URL + "/area-tutore",
            allow_redirects=True,
            headers={
                "Referer": BASE_URL + "/",
                "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
            },
        ) as r:
            area_body = await self._text(r, 6000)
            final_url = str(r.url)
            _LOGGER.warning(
                "[0.8.2 DIAG] Nuvola OIDC /area-tutore: HTTP %s final_url=%s type=%s title=%s cookies=%s",
                r.status,
                self._safe_url(final_url),
                r.headers.get("Content-Type"),
                self._safe_title(area_body),
                sorted(c.key for c in s.cookie_jar),
            )
            if AUTH_HOST in urlparse(final_url).netloc.lower() or final_url.rstrip("/").endswith("/login"):
                raise NuvolaAuthError(
                    "Il callback OIDC non ha creato una sessione Nuvola autenticata"
                )

        # 5. Convert the authenticated web session into the API JWT.
        async with s.get(
            BASE_URL + "/api-studente/v1/login-from-web",
            allow_redirects=True,
            headers={
                "Referer": BASE_URL + "/area-tutore",
                "Accept": "application/json, text/plain, */*",
            },
        ) as r:
            body = await self._text(r, 8000)
            final_url = str(r.url)
            _LOGGER.warning(
                "[0.8.2 DIAG] Nuvola OIDC login-from-web: HTTP %s final_url=%s type=%s title=%s",
                r.status,
                self._safe_url(final_url),
                r.headers.get("Content-Type"),
                self._safe_title(body),
            )

            if AUTH_HOST in urlparse(final_url).netloc.lower() or final_url.rstrip("/").endswith("/login"):
                raise NuvolaAuthError(
                    "La sessione OIDC Nuvola non è stata convertita in sessione API"
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
                    "login-from-web non ha restituito JSON dopo il login OIDC"
                ) from err

            if isinstance(data, dict):
                self.token = data.get("token") or data.get("access_token")
            elif isinstance(data, str):
                self.token = data

            if not self.token:
                raise NuvolaAuthError(
                    "login-from-web non ha restituito un token API."
                )

    async def _refresh_api_token(self):
        """Refresh the API token using the authenticated Nuvola web session."""
        s = await self._session()
        async with s.get(
            BASE_URL + "/api-studente/v1/login-from-web",
            allow_redirects=True,
            headers={
                "Referer": BASE_URL + "/area-tutore",
                "Accept": "application/json, text/plain, */*",
            },
        ) as r:
            body = await self._text(r, 8000)
            final_url = str(r.url)
            if r.status >= 400:
                raise NuvolaAuthError(f"login-from-web HTTP {r.status}")
            if AUTH_HOST in urlparse(final_url).netloc.lower() or final_url.rstrip("/").endswith("/login"):
                raise NuvolaAuthError("Sessione web Nuvola scaduta durante il rinnovo del token")
            try:
                data = json.loads(body)
            except json.JSONDecodeError as err:
                raise NuvolaAuthError("login-from-web non ha restituito JSON durante il rinnovo del token") from err

            token = data.get("token") or data.get("access_token") if isinstance(data, dict) else data if isinstance(data, str) else None
            if not token:
                raise NuvolaAuthError("login-from-web non ha restituito un nuovo token API")
            self.token = token

    async def _json(self, path, params=None):
        """GET a JSON API endpoint, renewing an expired token once."""
        if not self.token:
            await self.login()

        s = await self._session()
        for attempt in range(2):
            async with s.get(
                BASE_URL + path,
                params=params,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
            ) as r:
                body = await self._text(r, 12000)
                _LOGGER.warning(
                    "[0.8.2 DIAG] API request: path=%s attempt=%d HTTP=%s content_type=%s body_preview=%r",
                    path, attempt + 1, r.status, r.headers.get("Content-Type"), body[:500],
                )

                if r.status == 401:
                    try:
                        error_data = json.loads(body)
                    except json.JSONDecodeError:
                        error_data = {}
                    message = str(error_data.get("message", "")).lower()
                    token_expired = "scadut" in message or "expired" in message

                    if attempt == 0 and token_expired:
                        _LOGGER.info("Nuvola: token API scaduto, rinnovo automatico")
                        self.token = None
                        try:
                            await self._refresh_api_token()
                        except NuvolaAuthError:
                            _LOGGER.info("Nuvola: sessione web scaduta, eseguo nuovamente il login")
                            await self.login()
                        continue

                    _LOGGER.error(
                        "[0.8.2 DIAG] Nuvola API failed after token handling: path=%s HTTP=%s body=%r",
                        path, r.status, body[:1500],
                    )
                    raise NuvolaAuthError(
                        f"Token Nuvola rifiutato da {path} (HTTP {r.status})"
                    )

                if r.status >= 400:
                    _LOGGER.error(
                        "[0.8.2 DIAG] Nuvola API failed after token handling: path=%s HTTP=%s body=%r",
                        path, r.status, body[:1500],
                    )
                    raise NuvolaAuthError(f"API Nuvola {path} HTTP {r.status}")

                try:
                    data = json.loads(body)
                    if isinstance(data, list):
                        shape = f"list(len={len(data)})"
                    elif isinstance(data, dict):
                        shape = f"dict(keys={sorted(str(k) for k in data.keys())[:30]})"
                    else:
                        shape = type(data).__name__
                    _LOGGER.warning("[0.8.2 DIAG] API JSON parsed: path=%s shape=%s", path, shape)
                    return data
                except json.JSONDecodeError as err:
                    raise NuvolaAuthError(
                        f"API Nuvola {path} non ha restituito JSON"
                    ) from err

        raise NuvolaAuthError(f"Token Nuvola rifiutato da {path} dopo il rinnovo automatico")

    async def bulletin_documents(self, student_id):
        """Return the documents visible in the student's Nuvola bulletin boards."""
        data = await self._json(
            "/api-studente/v1/documenti",
            params={"contextAlunno": student_id},
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("data", "documenti", "documents", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    @staticmethod
    def attachment_preview_url(student_id, attachment_id):
        """Build the Nuvola preview endpoint for a bulletin-board attachment."""
        return (
            f"{BASE_URL}/api-studente/v1/alunno/{student_id}/file-preview/"
            f"{attachment_id}?contextAlunno={student_id}"
        )

    @staticmethod
    def _extract_list(data, keys):
        """Extract a list from Nuvola API responses, including the standard 'valori' envelope."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    async def students(self):
        data = await self._json("/api-studente/v1/alunni")
        values = self._extract_list(
            data,
            ("valori", "alunni", "students", "data", "items"),
        )
        _LOGGER.warning(
            "[0.8.2 DIAG] students: extracted %d records from response",
            len(values),
        )
        return values

    async def student(self):
        students = await self.students()
        self.student = students[0] if students else None
        return self.student

    async def periods(self, student_id):
        data = await self._json(
            f"/api-studente/v1/alunno/{student_id}/frazioni-temporali"
        )
        values = self._extract_list(
            data,
            ("valori", "frazioni_temporali", "periodi", "data", "items"),
        )
        _LOGGER.warning(
            "[0.8.2 DIAG] periods: extracted %d records",
            len(values),
        )
        return values

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
        _LOGGER.warning("[0.8.2 DIAG] fetch_all: START")
        students = await self.students()
        _LOGGER.warning(
            "[0.8.2 DIAG] fetch_all: students result count=%d first_keys=%s",
            len(students) if isinstance(students, list) else -1,
            sorted(students[0].keys()) if students and isinstance(students[0], dict) else [],
        )
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
            _LOGGER.error("[0.8.2 DIAG] fetch_all: ZERO STUDENTS -> all sensors will remain 0")
            return result

        sid = students[0].get("id") or students[0].get("id_alunno")
        if sid is None:
            _LOGGER.error("[0.8.2 DIAG] fetch_all: student found but no id/id_alunno field -> all child API calls skipped")
            return result

        result["student"] = students[0]

        try:
            periods = await self.periods(sid)
            result["periods"] = periods if isinstance(periods, list) else []
        except Exception as err:
            _LOGGER.warning("[0.8.2 DIAG] periods unavailable: %s", err)

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
                    _LOGGER.warning("[0.8.2 DIAG] grades unavailable: %s", err)

        try:
            result["absences"] = await self.absences(sid)
        except Exception as err:
            _LOGGER.warning("[0.8.2 DIAG] absences unavailable: %s", err)

        try:
            result["notes"] = await self.notes(sid)
        except Exception as err:
            _LOGGER.warning("[0.8.2 DIAG] notes unavailable: %s", err)

        try:
            result["homework"] = await self.homework(sid)
        except Exception as err:
            _LOGGER.warning("[0.8.2 DIAG] homework unavailable: %s", err)

        try:
            result["bulletin_documents"] = await self.bulletin_documents(sid)
        except Exception as err:
            _LOGGER.warning("[0.8.2 DIAG] bulletin documents unavailable: %s", err)
            result["bulletin_documents"] = []

        _LOGGER.warning(
            "[0.8.2 DIAG] fetch_all: END students=%d periods=%d grades_type=%s absences_type=%s notes_type=%s homework=%d bulletin=%d",
            len(result.get("students", [])),
            len(result.get("periods", [])),
            type(result.get("grades")).__name__,
            type(result.get("absences")).__name__,
            type(result.get("notes")).__name__,
            len(result.get("homework", [])) if isinstance(result.get("homework"), list) else -1,
            len(result.get("bulletin_documents", [])),
        )
        return result

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
