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
from datetime import UTC, date, datetime
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from models.task import TimeBlock

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
_TOKEN_PATH = "token.json"


class GCalService:
    """Wraps the Google Calendar API and returns normalized events + free blocks."""

    def __init__(
        self,
        calendar_ids: list[str] | None = None,
        credentials_path: str = "credentials.json",
        token_path: str = _TOKEN_PATH,
    ) -> None:
        self._calendar_ids = calendar_ids or ["primary"]
        self._credentials_path = credentials_path
        self._token_path = token_path
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

    def _run_consent_flow(self) -> Credentials:
        if not os.path.exists(self._credentials_path):
            raise FileNotFoundError(
                f"OAuth client secrets not found at {self._credentials_path!r}. "
                "Download it from Google Cloud Console and place it alongside "
                "this file, or set GOOGLE_CREDENTIALS_PATH in .env."
            )
        flow = InstalledAppFlow.from_client_secrets_file(self._credentials_path, SCOPES)
        creds = flow.run_local_server(
            port=0,
            success_message=(
                "Kirjautuminen onnistui! Voit sulkea tämän välilehden ja palata sovellukseen."
            ),
        )
        self._save_token(creds)
        return creds

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
