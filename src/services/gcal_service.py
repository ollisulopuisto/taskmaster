"""Google Calendar ingestion service.

Fetches today's events and computes free-time blocks from one or more
calendar IDs. All external calls go through `googleapiclient.discovery.build`,
which is mocked in tests.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from googleapiclient.discovery import build

from models.task import TimeBlock


class GCalService:
    """Wraps the Google Calendar API and returns normalized events + free blocks."""

    def __init__(self, calendar_ids: list[str] | None = None) -> None:
        self._calendar_ids = calendar_ids or ["primary"]
        self._service = build("calendar", "v3")

    def get_todays_events(self, *, today: date | None = None) -> list[dict[str, Any]]:
        """Return non-cancelled events occurring on `today` across all calendars."""
        reference = today or date.today()
        time_min = datetime(
            reference.year, reference.month, reference.day, 0, 0, 0, tzinfo=UTC
        )
        time_max = datetime(
            reference.year, reference.month, reference.day, 23, 59, 59, tzinfo=UTC
        )

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

                response = self._service.events().list(**kwargs).execute()
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
        response = self._service.freebusy().query(body=body).execute()

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
            return datetime.fromisoformat(value)
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
