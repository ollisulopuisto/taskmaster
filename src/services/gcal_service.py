"""Google Calendar ingestion service.

Fetches today's events and computes free-time blocks from one or more
calendar IDs. All external calls go through `googleapiclient.discovery.build`,
which is mocked in tests.

Authentication uses the local desktop OAuth flow
(`google_auth_oauthlib.flow.InstalledAppFlow`) with credentials resolved in
this order:

1. If `token.json` exists and yields valid credentials, use it as-is.
2. Otherwise, read the OAuth client secrets file at `credentials_path`
   (defaults to `credentials.json`) and launch the browser consent flow.
3. Persist the authorized credentials back to `token.json` for subsequent
   runs, then build the Calendar API client with those credentials.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from models.task import TimeBlock

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
_TOKEN_PATH = "token.json"
# Project root = taskmaster/ (two levels up from src/services/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FALLBACK_TZ = "Europe/Helsinki"


def _display_tz(tz: str | ZoneInfo | None = None) -> ZoneInfo:
    """Resolve the timezone calendar times are rendered in.

    Mirrors the rest of the app: explicit argument, else ``TIMEZONE`` from the
    environment, else Europe/Helsinki.
    """
    if isinstance(tz, ZoneInfo):
        return tz
    name = tz or os.getenv("TIMEZONE") or _FALLBACK_TZ
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(_FALLBACK_TZ)


def _parse_iso(value: Any) -> datetime | None:
    """Best-effort ISO-8601 parse; returns None for anything unparseable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _short_date(dt: datetime | date) -> str:
    """Render a date the way Finnish calendars do: ``14.8.``"""
    return f"{dt.day}.{dt.month}."


def _raw_time(value: Any) -> str:
    """Last-resort formatting for values that are not valid ISO-8601."""
    text = str(value or "")
    return text.split("T")[-1][:5] if "T" in text else text


def _anchor(path: str) -> str:
    """Resolve *path* relative to CWD or project root when it is a bare filename.

    If *path* is already absolute or contains directory separators it is
    returned unchanged. Otherwise, checks CWD first, then project root.
    """
    p = Path(path)
    if p.is_absolute() or len(p.parts) > 1:
        return str(p)
    cwd_candidate = Path.cwd() / p
    if cwd_candidate.exists():
        return str(cwd_candidate)
    return str(_PROJECT_ROOT / p)


def _resolve_token_path(credentials_path: str, token_path: str) -> str:
    """If token_path is a bare filename, place it next to credentials_path."""
    if os.path.dirname(token_path):
        return token_path
    creds_dir = os.path.dirname(os.path.abspath(_anchor(credentials_path)))
    return str(Path(creds_dir) / token_path)


class LocalAuthSession:
    """Handle on a running loopback OAuth callback server.

    ``done_event`` fires once Google redirects back with either an authorization
    code (in ``code_box[0]``) or an error (in ``error``). Requests that carry
    neither — favicon probes, prefetches, port scans — are answered and ignored,
    so a stray hit cannot end the flow with an empty code.

    Always call :meth:`close` when finished (including on timeout) to release the
    port and stop the background thread.
    """

    def __init__(
        self,
        *,
        flow: Any,
        url: str,
        done_event: threading.Event,
        code_box: list[str],
        error_box: list[str | None],
        port: int,
        redirect_uri: str,
        server: HTTPServer,
        thread: threading.Thread,
    ) -> None:
        self.flow = flow
        self.url = url
        self.done_event = done_event
        self.code_box = code_box
        self.port = port
        self.redirect_uri = redirect_uri
        self.thread = thread
        self._error_box = error_box
        self._server = server

    @property
    def error(self) -> str | None:
        """OAuth error reported by Google (e.g. ``access_denied``), if any."""
        return self._error_box[0]

    def close(self, timeout: float = 3.0) -> None:
        """Stop the callback server and wait for its thread to exit."""
        self.done_event.set()
        self.thread.join(timeout=timeout)
        try:
            self._server.server_close()
        except Exception:
            pass


class GCalService:
    """Wraps the Google Calendar API and returns normalized events + free blocks."""

    def __init__(
        self,
        calendar_ids: list[str] | None = None,
        credentials_path: str = "credentials.json",
        token_path: str = _TOKEN_PATH,
    ) -> None:
        self._calendar_ids = calendar_ids or ["primary"]
        self._credentials_path = _anchor(credentials_path)
        self._token_path = _resolve_token_path(credentials_path, token_path)
        self._service_instance: Any = None

    @property
    def service(self) -> Any:
        """Lazily initialize Google Calendar service on first access."""
        if self._service_instance is None:
            self._service_instance = build("calendar", "v3", credentials=self._load_credentials())
        return self._service_instance

    @property
    def _service(self) -> Any:
        """Backwards compatible property for _service access."""
        return self.service

    @_service.setter
    def _service(self, value: Any) -> None:
        self._service_instance = value

    @classmethod
    def validate_credentials_static(
        cls,
        credentials_path: str = "credentials.json",
        token_path: str = _TOKEN_PATH,
    ) -> tuple[bool, str]:
        """Pre-check Google Calendar OAuth credential & token state without blocking.

        Bare filenames are resolved exactly like the instance resolves them, so a
        pre-check run from a different working directory cannot disagree with the
        service it is gating.
        """
        credentials_path = _anchor(credentials_path)
        token_path = _resolve_token_path(credentials_path, token_path)

        if os.path.exists(token_path):
            try:
                creds = Credentials.from_authorized_user_file(token_path, SCOPES)
                if creds and creds.valid:
                    return True, "Google Calendar OAuth token is valid."
                if creds and creds.expired and creds.refresh_token:
                    try:
                        creds.refresh(Request())
                        with open(token_path, "w") as fh:
                            fh.write(creds.to_json())
                        return True, "Google Calendar OAuth token refreshed."
                    except RefreshError:
                        return (
                            False,
                            "Google Calendar OAuth token expired and refresh failed. "
                            "Re-authentication required.",
                        )
            except Exception as exc:
                return False, f"Invalid token file: {exc}"

        if not os.path.exists(credentials_path):
            return False, f"Google Calendar client secrets file missing at {credentials_path!r}."

        return (
            False,
            "Google Calendar authentication required (token missing). Run OAuth consent flow.",
        )

    def validate_credentials(self) -> tuple[bool, str]:
        """Instance wrapper for static credential check."""
        return self.validate_credentials_static(
            credentials_path=self._credentials_path,
            token_path=self._token_path,
        )

    def _load_credentials(self) -> Credentials:
        """Resolve OAuth credentials from disk or the consent flow."""
        creds = self._load_existing_token()
        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save_token(creds)
                return creds
            except RefreshError:
                if os.path.exists(self._token_path):
                    os.remove(self._token_path)

        return self._run_consent_flow()

    def _load_existing_token(self) -> Credentials | None:
        if os.path.exists(self._token_path):
            try:
                return Credentials.from_authorized_user_file(self._token_path, SCOPES)
            except Exception:
                if os.path.exists(self._token_path):
                    os.remove(self._token_path)
        return None

    def _ensure_credentials_file(self) -> None:
        if not os.path.exists(self._credentials_path):
            raise FileNotFoundError(
                f"OAuth client secrets not found at {self._credentials_path!r}. "
                "Download it from Google Cloud Console and place it alongside "
                "this file, or set GOOGLE_CREDENTIALS_PATH in .env."
            )

    def _run_consent_flow(self) -> Credentials:
        self._ensure_credentials_file()
        flow = InstalledAppFlow.from_client_secrets_file(self._credentials_path, SCOPES)
        creds = flow.run_local_server(
            port=0,
            success_message=(
                "Kirjautuminen onnistui! Voit sulkea tämän välilehden ja palata sovellukseen."
            ),
        )
        self._save_token(creds)
        return creds

    def get_auth_url(self) -> tuple[Any, str]:
        """Build the manual in-app OAuth flow and return ``(flow, auth_url)``.

        The caller must present ``auth_url`` to the user, keep the returned
        ``flow`` object, and pass both to :meth:`complete_auth` together with
        the single-use authorization code. Uses a loopback redirect URI since
        Google retired the out-of-band (OOB) console flow.
        """
        self._ensure_credentials_file()
        flow = InstalledAppFlow.from_client_secrets_file(
            self._credentials_path,
            SCOPES,
            redirect_uri="http://localhost",
        )
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        return flow, url

    _AUTH_PAGE_OK = (
        "<html><body style='font-family:sans-serif;padding:2em'>"
        "<h2>✅ Kirjautuminen onnistui!</h2>"
        "<p>Voit sulkea tämän välilehden ja palata sovellukseen.</p>"
        "</body></html>"
    )
    _AUTH_PAGE_DENIED = (
        "<html><body style='font-family:sans-serif;padding:2em'>"
        "<h2>❌ Kirjautuminen keskeytyi</h2>"
        "<p>Palaa sovellukseen ja yritä uudelleen.</p>"
        "</body></html>"
    )
    _AUTH_PAGE_WAITING = (
        "<html><body style='font-family:sans-serif;padding:2em'>"
        "<p>Odotetaan Googlen uudelleenohjausta…</p>"
        "</body></html>"
    )

    def start_local_auth_server(self, *, poll_interval: float = 0.5) -> LocalAuthSession:
        """Start a loopback HTTP server and return a :class:`LocalAuthSession`.

        The server binds a random free port and keeps that exact socket — the
        advertised redirect URI is always the port already being listened on, so
        no other process can slip in between binding and serving. It then keeps
        serving until Google redirects back with a ``code`` or an ``error``;
        unrelated requests (favicon probes, prefetches) are answered and ignored.

        >>> session = gcal.start_local_auth_server()
        >>> webbrowser.open(session.url)
        >>> try:
        ...     if session.done_event.wait(timeout=120) and session.code_box[0]:
        ...         gcal.complete_auth(session.flow, session.code_box[0])
        ... finally:
        ...     session.close()
        """
        self._ensure_credentials_file()

        done_event: threading.Event = threading.Event()
        code_box: list[str] = [""]
        error_box: list[str | None] = [None]
        pages = (self._AUTH_PAGE_OK, self._AUTH_PAGE_DENIED, self._AUTH_PAGE_WAITING)

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:  # silence server logs
                pass

            def do_GET(self) -> None:  # noqa: N802
                ok_page, denied_page, waiting_page = pages
                qs = parse_qs(urlparse(self.path).query)
                code = (qs.get("code") or [""])[0]
                error = (qs.get("error") or [""])[0]

                if code:
                    code_box[0] = code
                    page = ok_page
                elif error:
                    error_box[0] = error
                    page = denied_page
                else:
                    # Not the OAuth redirect (favicon, prefetch, port probe) —
                    # answer politely and keep waiting for the real one.
                    page = waiting_page

                body = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

                if code or error:
                    done_event.set()

        # Bind once and serve on that same socket — no close/rebind race.
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        server.timeout = poll_interval
        port = server.server_address[1]

        redirect_uri = f"http://localhost:{port}"
        flow = InstalledAppFlow.from_client_secrets_file(
            self._credentials_path,
            SCOPES,
            redirect_uri=redirect_uri,
        )
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")

        def _serve() -> None:
            # handle_request() returns after `poll_interval` if nothing arrived,
            # which is what lets close() stop this loop.
            while not done_event.is_set():
                server.handle_request()
            server.server_close()

        thread = threading.Thread(target=_serve, daemon=True)
        thread.start()

        return LocalAuthSession(
            flow=flow,
            url=url,
            done_event=done_event,
            code_box=code_box,
            error_box=error_box,
            port=port,
            redirect_uri=redirect_uri,
            server=server,
            thread=thread,
        )

    def complete_auth(self, flow: Any, code: str) -> tuple[bool, str]:
        """Exchange an authorization code for tokens and persist them.

        Returns a ``(ok, message)`` tuple. On success the authorized
        credentials are written to ``token.json`` via :meth:`_save_token`.
        """
        if not callable(getattr(flow, "fetch_token", None)):
            return False, "Invalid OAuth flow object."
        try:
            flow.fetch_token(code=code.strip())
            self._save_token(flow.credentials)
            return True, "Google Calendar OAuth token saved."
        except Exception as exc:
            return False, f"OAuth code exchange failed: {exc}"

    def _save_token(self, creds: Credentials) -> None:
        with open(self._token_path, "w") as fh:
            fh.write(creds.to_json())

    def _safe_execute(self, request_factory: Any) -> Any:
        """Execute a GCal API request with automatic reconnect on broken pipes."""
        try:
            return request_factory(self.service).execute()
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Connection died while idle — rebuild service with fresh socket
            self._service_instance = build("calendar", "v3", credentials=self._load_credentials())
            return request_factory(self.service).execute()

    @staticmethod
    def format_time_range(start: Any, end: Any = None, tz: str | ZoneInfo | None = None) -> str:
        """Format an ISO-8601 interval as a human-readable range.

        Times are converted to the display timezone (``TIMEZONE`` env, default
        Europe/Helsinki), so a UTC value renders as local wall-clock time. Spans
        crossing midnight carry their dates so they cannot be mistaken for a
        same-day event. Unparseable input degrades to the raw strings.
        """
        zone = _display_tz(tz)

        def _localize(dt: datetime) -> datetime:
            # Naive values carry no offset; treat them as already local.
            return dt.astimezone(zone) if dt.tzinfo else dt

        start_dt = _parse_iso(start)
        if start_dt is None:
            raw_start, raw_end = _raw_time(start), _raw_time(end)
            return f"{raw_start} → {raw_end}" if raw_end else raw_start

        start_dt = _localize(start_dt)
        end_dt = _parse_iso(end)
        if end_dt is None:
            return start_dt.strftime("%H:%M")

        end_dt = _localize(end_dt)
        if end_dt == start_dt:
            return start_dt.strftime("%H:%M")
        if end_dt.date() == start_dt.date():
            return f"{start_dt:%H:%M} → {end_dt:%H:%M}"
        return f"{_short_date(start_dt)} {start_dt:%H:%M} → {_short_date(end_dt)} {end_dt:%H:%M}"

    @staticmethod
    def format_event_time(ev: dict[str, Any], tz: str | ZoneInfo | None = None) -> str:
        """Format a Google Calendar event's start/end into a human-readable string.

        Returns e.g. "18:00 → 21:00" for a timed event, "14.8. 18:00 → 16.8. 18:00"
        when it spans days, "All Day" / "All Day (14.8. → 16.8.)" for all-day events.
        """
        start_obj = ev.get("start", {}) if isinstance(ev.get("start"), dict) else {}
        end_obj = ev.get("end", {}) if isinstance(ev.get("end"), dict) else {}

        if start_obj.get("dateTime"):
            return GCalService.format_time_range(
                start_obj.get("dateTime"), end_obj.get("dateTime"), tz=tz
            )

        start_date = start_obj.get("date")
        if start_date:
            try:
                first = date.fromisoformat(str(start_date))
                # Google's all-day end.date is exclusive — step back to the last day.
                last = date.fromisoformat(str(end_obj.get("date"))) - timedelta(days=1)
            except (TypeError, ValueError):
                return "All Day"
            if last > first:
                return f"All Day ({_short_date(first)} → {_short_date(last)})"
            return "All Day"

        return "?"

    def get_todays_events(self, *, today: date | None = None) -> list[dict[str, Any]]:
        """Return non-cancelled events occurring on `today` across all calendars."""
        reference = today or date.today()
        time_min = datetime(reference.year, reference.month, reference.day, 0, 0, 0, tzinfo=UTC)
        time_max = datetime(reference.year, reference.month, reference.day, 23, 59, 59, tzinfo=UTC)

        events: list[dict[str, Any]] = []
        for cal_id in self._calendar_ids:
            page_token: str | None = None
            while True:
                kwargs: dict[str, Any] = {
                    "calendarId": cal_id,
                    "timeMin": time_min.isoformat(),
                    "timeMax": time_max.isoformat(),
                    "singleEvents": True,
                    "orderBy": "startTime",
                }
                if page_token:
                    kwargs["pageToken"] = page_token

                current_kwargs = dict(kwargs)
                response = self._safe_execute(lambda s, kw=current_kwargs: s.events().list(**kw))
                for item in response.get("items", []):
                    if item.get("status") == "cancelled":
                        continue
                    events.append(item)

                page_token = response.get("nextPageToken")
                if not page_token:
                    break
        return events

    def get_free_time_blocks(
        self,
        *,
        today: date | None = None,
        day_start: datetime,
        day_end: datetime,
    ) -> list[TimeBlock]:
        """Return free-time windows between `day_start` and `day_end`.

        Queries the freebusy endpoint for all configured calendars and computes
        the gaps between busy periods.
        """
        # `today` is kept for API symmetry with the Todoist service; the
        # actual window is defined by `day_start`/`day_end` passed by the caller.
        _ = today or date.today()
        body = {
            "timeMin": day_start.isoformat(),
            "timeMax": day_end.isoformat(),
            "items": [{"id": cid} for cid in self._calendar_ids],
        }
        response = self._safe_execute(lambda s: s.freebusy().query(body=body))

        # Merge busy windows across all calendars
        busy: list[tuple[datetime, datetime]] = []
        for cal in response.get("calendars", {}).values():
            for window in cal.get("busy", []):
                start = self._parse_dt(window["start"])
                end = self._parse_dt(window["end"])
                if start and end:
                    busy.append((start, end))

        busy.sort(key=lambda w: w[0])
        merged = self._merge_intervals(busy)
        return self._gaps(merged, day_start, day_end)

    @staticmethod
    def _parse_dt(value: str) -> datetime | None:
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return None

    @staticmethod
    def _merge_intervals(
        intervals: list[tuple[datetime, datetime]],
    ) -> list[tuple[datetime, datetime]]:
        if not intervals:
            return []
        merged = [intervals[0]]
        for start, end in intervals[1:]:
            if start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    @staticmethod
    def _gaps(
        busy: list[tuple[datetime, datetime]],
        day_start: datetime,
        day_end: datetime,
    ) -> list[TimeBlock]:
        free: list[TimeBlock] = []
        cursor = day_start
        for start, end in busy:
            if start > cursor:
                free.append(TimeBlock(start=cursor.isoformat(), end=start.isoformat()))
            cursor = max(cursor, end)
        if cursor < day_end:
            free.append(TimeBlock(start=cursor.isoformat(), end=day_end.isoformat()))
        return free
