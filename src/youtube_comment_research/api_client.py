from __future__ import annotations

import time
from typing import Any

import httpx

from .errors import CommentsDisabledError, QuotaExceededError, YouTubeAPIError


class YouTubeAPIClient:
    BASE_URL = "https://www.googleapis.com/youtube/v3"
    RETRYABLE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError"}

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        max_retries: int = 4,
        backoff_seconds: float = 1.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "YouTubeAPIClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {**params, "key": self.api_key}
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.get(f"{self.BASE_URL}/{endpoint}", params=query)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise YouTubeAPIError(f"Network error calling YouTube API: {exc}") from exc
                self._sleep(attempt)
                continue

            if response.status_code < 400:
                return response.json()

            reason, message = self._parse_error(response)
            retryable = response.status_code in {429, 500, 502, 503, 504} or reason in self.RETRYABLE_REASONS
            if retryable and attempt < self.max_retries:
                self._sleep(attempt)
                continue

            if reason == "commentsDisabled":
                raise CommentsDisabledError(message, status_code=response.status_code, reason=reason)
            if reason in {"quotaExceeded", "dailyLimitExceeded"}:
                raise QuotaExceededError(message, status_code=response.status_code, reason=reason)
            raise YouTubeAPIError(message, status_code=response.status_code, reason=reason)

        raise YouTubeAPIError(f"YouTube API request failed: {last_error}")

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        data = self.request(
            "videos",
            {"part": "snippet,statistics", "id": video_id, "maxResults": 1},
        )
        items = data.get("items", [])
        return items[0] if items else None

    def get_comment_threads(
        self,
        video_id: str,
        *,
        order: str,
        page_token: str | None = None,
        max_results: int = 100,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": min(max_results, 100),
            "order": order,
            "textFormat": "plainText",
        }
        if page_token:
            params["pageToken"] = page_token
        return self.request("commentThreads", params)

    def get_replies(
        self,
        parent_id: str,
        *,
        page_token: str | None = None,
        max_results: int = 100,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "part": "snippet",
            "parentId": parent_id,
            "maxResults": min(max_results, 100),
            "textFormat": "plainText",
        }
        if page_token:
            params["pageToken"] = page_token
        return self.request("comments", params)

    def _sleep(self, attempt: int) -> None:
        time.sleep(self.backoff_seconds * (2**attempt))

    @staticmethod
    def _parse_error(response: httpx.Response) -> tuple[str | None, str]:
        try:
            payload = response.json()
        except ValueError:
            return None, f"YouTube API returned HTTP {response.status_code}: {response.text[:300]}"

        error = payload.get("error", {})
        errors = error.get("errors", [])
        reason = errors[0].get("reason") if errors else None
        message = error.get("message") or f"YouTube API returned HTTP {response.status_code}"
        return reason, message
