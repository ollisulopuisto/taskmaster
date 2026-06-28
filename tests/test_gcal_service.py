"""Tests for the Google Calendar service."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

from models.task import TimeBlock
from services.gcal_service import GCalService


def _event(
    *,
    id: str = "evt-1",
    summary: str = "Meeting",
    start: str = "2026-06-28T10:00:00+00:00",
    end: str = "2026-06-28T11:00:00+00:00",
) -> dict:
    return {
        "id": id,
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }


def _all_day_event(*, id: str = "evt-1", summary: str = "Holiday", day: str = "2026-06-28") -> dict:
    return {
        "id": id,
        "summary": summary,
        "start": {"date": day},
        "end": {"date": day},
    }


class TestGCalService:
    def _service(self, calendar_ids: list[str] | None = None) -> GCalService:
        return GCalService(calendar_ids=calendar_ids or ["primary"])

    def _fake_service(self) -> MagicMock:
        """Build a mocked google calendar service object."""
        return MagicMock()

    def _patch_build(self, fake_svc: MagicMock):
        """Patch googleapiclient.discovery.build to return our fake service."""
        return patch("services.gcal_service.build", return_value=fake_svc)

    def test_get_todays_events_returns_normalized_events(self) -> None:
        fake = self._fake_service()
        fake.events.return_value.list.return_value.execute.return_value = {
            "items": [
                _event(
                    id="1",
                    summary="Standup",
                    start="2026-06-28T09:00:00+00:00",
                    end="2026-06-28T09:30:00+00:00",
                ),
                _event(
                    id="2",
                    summary="Lunch",
                    start="2026-06-28T12:00:00+00:00",
                    end="2026-06-28T13:00:00+00:00",
                ),
            ]
        }

        with self._patch_build(fake):
            result = self._service().get_todays_events(today=date(2026, 6, 28))

        assert len(result) == 2
        assert result[0]["summary"] == "Standup"
        assert result[0]["id"] == "1"
        assert result[1]["summary"] == "Lunch"

    def test_get_todays_events_queries_secondary_calendars(self) -> None:
        fake = self._fake_service()
        fake.events.return_value.list.return_value.execute.return_value = {"items": []}

        with self._patch_build(fake):
            self._service(calendar_ids=["primary", "work@example.com"]).get_todays_events(
                today=date(2026, 6, 28)
            )

        # One list() call per calendar
        assert fake.events.return_value.list.call_count == 2
        # Verify calendarId was passed for the second calendar
        second_call = fake.events.return_value.list.call_args_list[1]
        assert second_call.kwargs["calendarId"] == "work@example.com"

    def test_get_todays_events_handles_all_day_events(self) -> None:
        fake = self._fake_service()
        fake.events.return_value.list.return_value.execute.return_value = {
            "items": [_all_day_event(id="1", summary="Holiday", day="2026-06-28")]
        }

        with self._patch_build(fake):
            result = self._service().get_todays_events(today=date(2026, 6, 28))

        assert len(result) == 1
        assert result[0]["summary"] == "Holiday"

    def test_get_todays_events_paginates(self) -> None:
        fake = self._fake_service()
        fake.events.return_value.list.return_value.execute.side_effect = [
            {
                "items": [_event(id="1", summary="A")],
                "nextPageToken": "tok",
            },
            {
                "items": [_event(id="2", summary="B")],
            },
        ]

        with self._patch_build(fake):
            result = self._service().get_todays_events(today=date(2026, 6, 28))

        assert [e["id"] for e in result] == ["1", "2"]
        # Second call must include the page token
        assert fake.events.return_value.list.call_args_list[1].kwargs["pageToken"] == "tok"

    def test_get_todays_events_skips_cancelled(self) -> None:
        fake = self._fake_service()
        fake.events.return_value.list.return_value.execute.return_value = {
            "items": [
                _event(id="1", summary="Kept"),
                _event(id="2", summary="Cancelled"),
                _event(id="3", summary="Also kept"),
            ]
        }
        # Mark the second item as cancelled by mutating the returned dict
        items = fake.events.return_value.list.return_value.execute.return_value["items"]
        items[1]["status"] = "cancelled"

        with self._patch_build(fake):
            result = self._service().get_todays_events(today=date(2026, 6, 28))

        assert [e["id"] for e in result] == ["1", "3"]

    def test_get_free_time_blocks_gaps_between_events(self) -> None:
        fake = self._fake_service()
        fake.freebusy.return_value.query.return_value.execute.return_value = {
            "calendars": {
                "primary": {
                    "busy": [
                        {"start": "2026-06-28T10:00:00+00:00", "end": "2026-06-28T11:00:00+00:00"},
                        {"start": "2026-06-28T14:00:00+00:00", "end": "2026-06-28T15:00:00+00:00"},
                    ]
                }
            }
        }

        with self._patch_build(fake):
            blocks = self._service().get_free_time_blocks(
                today=date(2026, 6, 28),
                day_start=datetime(2026, 6, 28, 8, 0, tzinfo=UTC),
                day_end=datetime(2026, 6, 28, 18, 0, tzinfo=UTC),
            )

        # Expect free blocks: 08-10, 11-14, 15-18
        assert len(blocks) == 3
        assert blocks[0] == TimeBlock(
            start="2026-06-28T08:00:00+00:00",
            end="2026-06-28T10:00:00+00:00",
        )
        assert blocks[1] == TimeBlock(
            start="2026-06-28T11:00:00+00:00",
            end="2026-06-28T14:00:00+00:00",
        )
        assert blocks[2] == TimeBlock(
            start="2026-06-28T15:00:00+00:00",
            end="2026-06-28T18:00:00+00:00",
        )

    def test_get_free_time_blocks_all_day_busy(self) -> None:
        fake = self._fake_service()
        fake.freebusy.return_value.query.return_value.execute.return_value = {
            "calendars": {
                "primary": {
                    "busy": [
                        {"start": "2026-06-28T00:00:00+00:00", "end": "2026-06-29T00:00:00+00:00"},
                    ]
                }
            }
        }

        with self._patch_build(fake):
            blocks = self._service().get_free_time_blocks(
                today=date(2026, 6, 28),
                day_start=datetime(2026, 6, 28, 8, 0, tzinfo=UTC),
                day_end=datetime(2026, 6, 28, 18, 0, tzinfo=UTC),
            )

        assert blocks == []

    def test_get_free_time_blocks_no_events(self) -> None:
        fake = self._fake_service()
        fake.freebusy.return_value.query.return_value.execute.return_value = {
            "calendars": {"primary": {"busy": []}}
        }

        with self._patch_build(fake):
            blocks = self._service().get_free_time_blocks(
                today=date(2026, 6, 28),
                day_start=datetime(2026, 6, 28, 8, 0, tzinfo=UTC),
                day_end=datetime(2026, 6, 28, 18, 0, tzinfo=UTC),
            )

        assert blocks == [
            TimeBlock(start="2026-06-28T08:00:00+00:00", end="2026-06-28T18:00:00+00:00")
        ]
