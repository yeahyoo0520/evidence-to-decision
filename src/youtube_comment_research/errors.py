from __future__ import annotations


class YouTubeCommentResearchError(Exception):
    """Base exception for the package."""


class ConfigurationError(YouTubeCommentResearchError):
    """Raised when required configuration is missing or invalid."""


class ClassificationWorkflowError(YouTubeCommentResearchError):
    """Raised when an offline classification batch or merge is invalid."""


class InvalidVideoURLError(YouTubeCommentResearchError):
    """Raised when a YouTube URL or video ID cannot be parsed."""


class YouTubeAPIError(YouTubeCommentResearchError):
    """Raised for YouTube Data API errors."""

    def __init__(self, message: str, *, status_code: int | None = None, reason: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


class CommentsDisabledError(YouTubeAPIError):
    """Raised when comments are disabled for a video."""


class VideoNotFoundError(YouTubeAPIError):
    """Raised when a requested video is unavailable or not found."""


class QuotaExceededError(YouTubeAPIError):
    """Raised when YouTube API quota is exhausted."""
