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
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

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


def _anchor(path: str) -> str:
    """Resolve *path* relative to the project root when it is a bare filename.

    If *path* is already absolute or contains directory separators it is
    returned unchanged, so callers that already pass a full path still work.
    """
    p = Path(path)
    if p.is_absolute() or len(p.parts) > 1:
        return str(p)
    return str(_PROJECT_ROOT / p)


def _resolve_token_path(credentials_path: str, token_path: str) -> str:
    """If token_path is a bare filename, place it next to credentials_path."""
    if os.path.dirname(token_path):
        return token_path
    creds_dir = os.path.dirname(os.path.abspath(_anchor(credentials_path)))
    return str(Path(creds_dir) / token_path)


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
        """Pre-check Google Calendar OAuth credential & token state without blocking."""
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

    def start_local_auth_server(
        self,
    ) -> tuple[Any, str, threading.Event, list[str]]:
        """Start a local loopback HTTP server and return ``(flow, url, done_event, code_box)``.

        Binds to a random free port on localhost, builds the OAuth URL pointing
        at it, then starts a background thread that handles exactly one request
        (the Google redirect) and stores the authorization code in ``code_box[0]``
        before setting ``done_event``.  The caller should poll or wait on
        ``done_event`` and read ``code_box[0]``.

        >>> flow, url, done, code_box = gcal.start_local_auth_server()
        >>> webbrowser.open(url)           # open in browser
        >>> done.wait(timeout=120)         # wait up to 2 min
        >>> gcal.complete_auth(flow, code_box[0])
        """
        self._ensure_credentials_file()

        # Bind on a random free port to get the port number.
        srv = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        port = srv.server_address[1]
        srv.server_close()

        redirect_uri = f"http://localhost:{port}"
        flow = InstalledAppFlow.from_client_secrets_file(
            self._credentials_path,
            SCOPES,
            redirect_uri=redirect_uri,
        )
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")

        done_event: threading.Event = threading.Event()
        code_box: list[str] = [""]

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:  # silence server logs
                pass

            def do_GET(self) -> None:  # noqa: N802
                qs = parse_qs(urlparse(self.path).query)
                codes = qs.get("code", [])
                code_box[0] = codes[0] if codes else ""
                body = (
                    b"<html><body style='font-family:sans-serif;padding:2em'>"
                    b"<h2>\xe2\x9c\x85 Kirjautuminen onnistui!</h2>"
                    b"<p>Voit sulkea t\xc3\xa4m\xc3\xa4n v\xc3\xa4lilehden "
                    b"ja palata sovellukseen.</p>"
                    b"</body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                done_event.set()

        one_shot_server = HTTPServer(("127.0.0.1", port), _Handler)

        def _serve() -> None:
            one_shot_server.handle_request()  # blocks until one request arrives
            one_shot_server.server_close()

        t = threading.Thread(target=_serve, daemon=True)
        t.start()

        return flow, url, done_event, code_box

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
