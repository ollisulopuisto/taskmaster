"""Tests for the Streamlit frontend's data-fetching layer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from app import fetch_morning_plan, submit_evening


class TestFetchMorningPlan:
    @patch("app.requests.get")
    def test_returns_json_on_success(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"big": [], "medium": [], "small": []}
        mock_get.return_value = mock_response

        result = fetch_morning_plan()

        assert result == {"big": [], "medium": [], "small": []}
        mock_get.assert_called_once()

    @patch("app.requests.get")
    def test_returns_none_on_network_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.ConnectionError("down")

        result = fetch_morning_plan()

        assert result is None

    @patch("app.requests.get")
    def test_returns_none_on_http_error(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("500")
        mock_get.return_value = mock_response

        result = fetch_morning_plan()

        assert result is None


class TestSubmitEvening:
    @patch("app.requests.post")
    def test_returns_true_on_success(self, mock_post: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        assert submit_evening(["1", "2"], ["3"]) is True
        mock_post.assert_called_once_with(
            "http://localhost:8000/api/triage/evening",
            json={"completed_ids": ["1", "2"], "rolled_over_ids": ["3"]},
            timeout=30,
        )

    @patch("app.requests.post")
    def test_returns_false_on_error(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = requests.ConnectionError("down")

        assert submit_evening(["1"], []) is False
