from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CommentOrder(str, Enum):
    RELEVANCE = "relevance"
    TIME = "time"


class CollectionConfig(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    max_comments: int = Field(default=100, ge=1, le=100_000)
    order: CommentOrder = CommentOrder.RELEVANCE
    include_replies: bool = False
    minimum_likes: int = Field(default=0, ge=0)
    keyword_filter: str | None = None
    published_after: date | None = None
    published_before: date | None = None

    @field_validator("keyword_filter")
    @classmethod
    def normalize_keyword(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class VideoMetadata(BaseModel):
    video_id: str
    video_url: str
    video_title: str
    channel_id: str | None = None
    channel_title: str | None = None
    video_published_at: datetime | None = None
    video_view_count: int | None = None
    video_like_count: int | None = None
    video_comment_count: int | None = None


class CommentRecord(BaseModel):
    project_id: str
    video_id: str
    video_url: str
    video_title: str
    channel_title: str | None = None
    video_published_at: datetime | None = None
    video_view_count: int | None = None
    video_like_count: int | None = None
    video_comment_count: int | None = None

    comment_id: str
    parent_comment_id: str | None = None
    is_reply: bool = False
    comment_text: str
    comment_like_count: int = 0
    reply_count: int = 0
    comment_published_at: datetime | None = None
    comment_updated_at: datetime | None = None

    author_display_name: str | None = None
    author_channel_id: str | None = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    collection_order: int

    def export_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class VideoFailure(BaseModel):
    input_value: str
    video_id: str | None = None
    error_type: str
    message: str


class CollectionSummary(BaseModel):
    project_id: str
    started_at: datetime
    finished_at: datetime
    requested_videos: int
    successful_videos: int
    failed_videos: int
    exported_comments: int
    include_replies: bool
    anonymized_authors: bool
    formats: list[str]
    output_files: list[str] = Field(default_factory=list)
    failures: list[VideoFailure] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
