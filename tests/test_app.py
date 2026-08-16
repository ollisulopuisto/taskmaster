"""Tests for the Streamlit frontend's data-fetching layer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from app import fetch_morning_payload, submit_evening


class TestFetchMorningPayload:
    @patch("app.requests.get")
    def test_returns_json_on_success(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"plan": {}, "schedule": {}}
        mock_get.return_value = mock_response

        result = fetch_morning_payload()

        assert result == {"plan": {}, "schedule": {}}
        mock_get.assert_called_once()

    @patch("app.requests.get")
    def test_returns_none_on_network_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.ConnectionError("down")

        result = fetch_morning_payload()

        assert result is None

    @patch("app.requests.get")
    def test_returns_none_on_http_error(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("500")
        mock_get.return_value = mock_response

        result = fetch_morning_payload()

        assert result is None


class TestSubmitEvening:
    @patch("app.requests.post")
    def test_returns_true_on_success(self, mock_post: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        assert submit_evening(["1", "2"], ["3"]) is True
        mock_post.assert_called_once_with(
            "http://localhost:8002/api/triage/evening",
            json={"completed_ids": ["1", "2"], "rolled_over_ids": ["3"]},
            timeout=30,
        )

    @patch("app.requests.post")
    def test_returns_false_on_error(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = requests.ConnectionError("down")

        assert submit_evening(["1"], []) is False


class TestScheduleRendering:
    """The Streamlit column must reuse the shared GCal formatter, not re-slice strings."""

    def test_free_blocks_use_the_shared_formatter(self) -> None:
        from app import render_schedule_column

        schedule = {
            "events": [
                {
                    "summary": "Team Sync",
                    "start": {"dateTime": "2026-08-14T09:00:00+03:00"},
                    "end": {"dateTime": "2026-08-14T09:30:00+03:00"},
                }
            ],
            "free_blocks": [
                {"start": "2026-08-14T10:00:00+03:00", "end": "2026-08-14T12:00:00+03:00"}
            ],
        }

        with patch("app.st"), patch("app.GCalService") as mock_gcal:
            mock_gcal.format_event_time.return_value = "09:00 → 09:30"
            mock_gcal.format_time_range.return_value = "10:00 → 12:00"
            render_schedule_column(schedule)

        mock_gcal.format_event_time.assert_called_once()
        mock_gcal.format_time_range.assert_called_once_with(
            "2026-08-14T10:00:00+03:00", "2026-08-14T12:00:00+03:00"
        )

    def test_debug_tab_free_blocks_use_the_shared_formatter(self) -> None:
        from app import render_free_block_lines

        lines = render_free_block_lines(
            [{"start": "2026-08-14T10:00:00+03:00", "end": "2026-08-14T12:00:00+03:00"}]
        )
        assert lines == ["10:00 → 12:00"]
