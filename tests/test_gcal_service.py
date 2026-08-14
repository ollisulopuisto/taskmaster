"""Tests for the Google Calendar service."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError

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
    _VALID_TOKEN_JSON = (
        '{"token": "ya29.fake", "refresh_token": "1//fake", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"client_id": "cid", "client_secret": "secret", '
        '"scopes": ["https://www.googleapis.com/auth/calendar.readonly"], '
        '"expiry": "2099-01-01T00:00:00Z"}'
    )

    def _service(self, calendar_ids: list[str] | None = None) -> GCalService:
        # Pre-stub a valid token so the constructor's _load_credentials
        # short-circuits via the cached-token branch and never hits the network.
        token_path = "/tmp/fake-token.json"
        with open(token_path, "w") as fh:
            fh.write(self._VALID_TOKEN_JSON)
        return GCalService(
            calendar_ids=calendar_ids or ["primary"],
            credentials_path="/tmp/fake-credentials.json",
            token_path=token_path,
        )

    def _patch_path_exists(self, *, token_exists: bool = False, credentials_exists: bool = False):
        def fake_exists(path: str) -> bool:
            if path == "/tmp/fake-token.json":
                return token_exists
            if path == "/tmp/fake-credentials.json":
                return credentials_exists
            return False

        return patch("os.path.exists", side_effect=fake_exists)

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


class TestGCalAuth:
    """Verify the OAuth credential-resolution flow without hitting the network."""

    VALID_TOKEN_JSON = (
        '{"token": "ya29.fake", "refresh_token": "1//fake", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"client_id": "cid", "client_secret": "secret", '
        '"scopes": ["https://www.googleapis.com/auth/calendar.readonly"], '
        '"expiry": "2099-01-01T00:00:00Z"}'
    )

    def test_uses_existing_token_when_valid(self, tmp_path) -> None:
        token_path = tmp_path / "token.json"
        token_path.write_text(self.VALID_TOKEN_JSON)

        with patch("services.gcal_service.build") as mock_build:
            svc = GCalService(
                credentials_path=str(tmp_path / "missing.json"),
                token_path=str(token_path),
            )
            _ = svc.service

        mock_build.assert_called_once()
        # build() must be invoked with resolved credentials
        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs["credentials"] is not None
        assert call_kwargs["credentials"].valid is True

    def test_missing_token_triggers_consent_flow(self, tmp_path) -> None:
        credentials_path = tmp_path / "credentials.json"
        credentials_path.write_text("{}")

        fake_creds = MagicMock()
        fake_creds.to_json.return_value = "{}"

        with (
            patch("services.gcal_service.build"),
            patch("services.gcal_service.InstalledAppFlow") as MockFlow,
            patch("os.path.exists", side_effect=lambda p: p == str(credentials_path)),
        ):
            MockFlow.from_client_secrets_file.return_value.run_local_server.return_value = (
                fake_creds
            )
            svc = GCalService(
                credentials_path=str(credentials_path),
                token_path=str(tmp_path / "token.json"),
            )
            _ = svc.service

        MockFlow.from_client_secrets_file.assert_called_once_with(
            str(credentials_path),
            ["https://www.googleapis.com/auth/calendar.readonly"],
        )
        MockFlow.from_client_secrets_file.return_value.run_local_server.assert_called_once_with(
            port=0,
            success_message=(
                "Kirjautuminen onnistui! Voit sulkea tämän välilehden ja palata sovellukseen."
            ),
        )

    def test_consent_flow_writes_token_file(self, tmp_path) -> None:
        credentials_path = tmp_path / "credentials.json"
        credentials_path.write_text("{}")
        token_path = tmp_path / "token.json"

        fake_creds = MagicMock()
        fake_creds.to_json.return_value = self.VALID_TOKEN_JSON

        with (
            patch("services.gcal_service.build"),
            patch("services.gcal_service.InstalledAppFlow") as MockFlow,
            patch("os.path.exists", side_effect=lambda p: p == str(credentials_path)),
        ):
            MockFlow.from_client_secrets_file.return_value.run_local_server.return_value = (
                fake_creds
            )
            svc = GCalService(
                credentials_path=str(credentials_path),
                token_path=str(token_path),
            )
            _ = svc.service

        assert token_path.exists()
        assert token_path.read_text() == self.VALID_TOKEN_JSON

    def test_missing_credentials_file_raises(self, tmp_path) -> None:
        with patch("services.gcal_service.build"), patch("os.path.exists", return_value=False):
            with pytest.raises(FileNotFoundError):
                svc = GCalService(
                    credentials_path=str(tmp_path / "nope.json"),
                    token_path=str(tmp_path / "token.json"),
                )
                _ = svc.service

    def test_parse_dt_handles_naive_date_strings(self) -> None:
        dt = GCalService._parse_dt("2026-07-22")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2026 and dt.month == 7 and dt.day == 22

    def test_expired_token_refresh_error_removes_token_and_triggers_consent_flow(
        self, tmp_path
    ) -> None:
        credentials_path = tmp_path / "credentials.json"
        credentials_path.write_text("{}")
        token_path = tmp_path / "token.json"
        token_path.write_text(self.VALID_TOKEN_JSON)

        fake_creds = MagicMock()
        fake_creds.expired = True
        fake_creds.refresh_token = "1//fake"
        fake_creds.valid = False
        fake_creds.refresh.side_effect = RefreshError("invalid_grant: Bad Request")

        new_creds = MagicMock()
        new_creds.to_json.return_value = self.VALID_TOKEN_JSON

        with (
            patch("services.gcal_service.build"),
            patch(
                "services.gcal_service.Credentials.from_authorized_user_file",
                return_value=fake_creds,
            ),
            patch("services.gcal_service.InstalledAppFlow") as MockFlow,
        ):
            MockFlow.from_client_secrets_file.return_value.run_local_server.return_value = new_creds
            svc = GCalService(
                credentials_path=str(credentials_path),
                token_path=str(token_path),
            )
            _ = svc.service

        assert not token_path.exists() or token_path.read_text() == self.VALID_TOKEN_JSON
        MockFlow.from_client_secrets_file.assert_called_once()

    def test_validate_credentials_valid(self, tmp_path) -> None:
        token_path = tmp_path / "token.json"
        token_path.write_text(self.VALID_TOKEN_JSON)

        with patch("services.gcal_service.build"):
            svc = GCalService(
                credentials_path=str(tmp_path / "missing.json"),
                token_path=str(token_path),
            )
            ok, msg = svc.validate_credentials()
            assert ok is True
            assert "valid" in msg.lower()

    def test_validate_credentials_missing_secrets(self, tmp_path) -> None:
        with patch("os.path.exists", return_value=False):
            ok, msg = GCalService.validate_credentials_static(
                credentials_path=str(tmp_path / "nope.json"),
                token_path=str(tmp_path / "token.json"),
            )
            assert ok is False
            assert "missing" in msg.lower()


class TestGCalInteractiveAuth:
    """Manual in-app OAuth flow: auth URL generation and code exchange."""

    _SECRETS_JSON = (
        '{"installed": {"client_id": "cid", "project_id": "p", '
        '"auth_uri": "https://accounts.google.com/o/oauth2/auth", '
        '"token_uri": "https://oauth2.googleapis.com/token", '
        '"client_secret": "secret", "redirect_uris": ["http://localhost"]}}'
    )

    def _service(self, tmp_path) -> GCalService:
        return GCalService(
            credentials_path=str(tmp_path / "credentials.json"),
            token_path=str(tmp_path / "token.json"),
        )

    def test_get_auth_url_missing_credentials_raises(self, tmp_path) -> None:
        svc = self._service(tmp_path)
        with pytest.raises(FileNotFoundError, match="client secrets"):
            svc.get_auth_url()
        assert not (tmp_path / "token.json").exists()

    def test_get_auth_url_returns_flow_and_url(self, tmp_path) -> None:
        secrets = tmp_path / "credentials.json"
        secrets.write_text(self._SECRETS_JSON)
        svc = self._service(tmp_path)

        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = (
            "https://accounts.google.com/o/oauth2/auth?x=1",
            "state-123",
        )
        with patch(
            "services.gcal_service.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        ) as mock_from_file:
            flow, url = svc.get_auth_url()

        mock_from_file.assert_called_once_with(
            str(secrets),
            ["https://www.googleapis.com/auth/calendar.readonly"],
            redirect_uri="http://localhost",
        )
        mock_flow.authorization_url.assert_called_once_with(access_type="offline", prompt="consent")
        assert flow is mock_flow
        assert url == "https://accounts.google.com/o/oauth2/auth?x=1"
        assert not (tmp_path / "token.json").exists()

    def test_complete_auth_saves_token(self, tmp_path) -> None:
        svc = self._service(tmp_path)
        mock_flow = MagicMock()
        mock_credentials = MagicMock()
        mock_credentials.to_json.return_value = '{"token": "ya29.new"}'
        mock_flow.credentials = mock_credentials

        ok, msg = svc.complete_auth(mock_flow, "  abc-123  ")

        assert ok is True
        assert "saved" in msg.lower()
        mock_flow.fetch_token.assert_called_once_with(code="abc-123")
        assert (tmp_path / "token.json").read_text() == '{"token": "ya29.new"}'

    def test_complete_auth_fetch_failure_reports_error(self, tmp_path) -> None:
        svc = self._service(tmp_path)
        mock_flow = MagicMock()
        mock_flow.fetch_token.side_effect = Exception("invalid_grant")

        ok, msg = svc.complete_auth(mock_flow, "bad")

        assert ok is False
        assert "invalid_grant" in msg
        assert not (tmp_path / "token.json").exists()

    def test_format_event_time_range_for_timed_event(self) -> None:
        ev = {
            "summary": "Itä-Helsingin baarikierros",
            "start": {"dateTime": "2026-08-14T18:00:00+03:00"},
            "end": {"dateTime": "2026-08-14T21:00:00+03:00"},
        }
        res = GCalService.format_event_time(ev)
        assert res == "18:00 → 21:00"

    def test_format_event_time_for_single_time(self) -> None:
        ev = {
            "summary": "Pressure",
            "start": {"dateTime": "2026-08-14T09:30:00+03:00"},
        }
        res = GCalService.format_event_time(ev)
        assert res == "09:30"

    def test_format_event_time_for_all_day_event(self) -> None:
        ev = {
            "summary": "Holiday",
            "start": {"date": "2026-08-14"},
        }
        res = GCalService.format_event_time(ev)
        assert res == "All Day"
