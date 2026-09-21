from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterator

from .api_client import YouTubeAPIClient
from .errors import VideoNotFoundError
from .models import CollectionConfig, CommentRecord, VideoMetadata
from .urls import extract_video_id


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _author_channel_id(snippet: dict[str, Any]) -> str | None:
    author = snippet.get("authorChannelId")
    if isinstance(author, dict):
        return author.get("value")
    return None


def _passes_filters(record: CommentRecord, config: CollectionConfig) -> bool:
    if record.comment_like_count < config.minimum_likes:
        return False
    if config.keyword_filter and config.keyword_filter.casefold() not in record.comment_text.casefold():
        return False
    published = record.comment_published_at.date() if record.comment_published_at else None
    if config.published_after and (published is None or published < config.published_after):
        return False
    if config.published_before and (published is None or published > config.published_before):
        return False
    return True


class CommentCollector:
    def __init__(self, client: YouTubeAPIClient) -> None:
        self.client = client

    def get_video_metadata(self, video_ref: str) -> VideoMetadata:
        video_id = extract_video_id(video_ref)
        item = self.client.get_video(video_id)
        if not item:
            raise VideoNotFoundError(
                f"Video not found or unavailable: {video_ref}", status_code=404, reason="videoNotFound"
            )

        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        return VideoMetadata(
            video_id=video_id,
            video_url=f"https://www.youtube.com/watch?v={video_id}",
            video_title=snippet.get("title", ""),
            channel_id=snippet.get("channelId"),
            channel_title=snippet.get("channelTitle"),
            video_published_at=_parse_datetime(snippet.get("publishedAt")),
            video_view_count=_int_or_none(stats.get("viewCount")),
            video_like_count=_int_or_none(stats.get("likeCount")),
            video_comment_count=_int_or_none(stats.get("commentCount")),
        )

    def collect_video(
        self,
        video_ref: str,
        *,
        project_id: str,
        config: CollectionConfig,
        collected_at: datetime | None = None,
    ) -> tuple[VideoMetadata, list[CommentRecord]]:
        metadata = self.get_video_metadata(video_ref)
        timestamp = collected_at or datetime.now(timezone.utc)
        output: list[CommentRecord] = []
        seen: set[str] = set()
        page_token: str | None = None
        order_index = 0

        while len(output) < config.max_comments:
            response = self.client.get_comment_threads(
                metadata.video_id,
                order=str(config.order),
                page_token=page_token,
                max_results=min(100, config.max_comments - len(output)),
            )
            items = response.get("items", [])
            if not items:
                break

            for thread in items:
                top = thread.get("snippet", {}).get("topLevelComment", {})
                top_snippet = top.get("snippet", {})
                comment_id = top.get("id")
                if not comment_id or comment_id in seen:
                    continue
                seen.add(comment_id)
                order_index += 1
                top_record = self._make_record(
                    metadata,
                    project_id=project_id,
                    comment_id=comment_id,
                    snippet=top_snippet,
                    is_reply=False,
                    parent_comment_id=None,
                    reply_count=int(thread.get("snippet", {}).get("totalReplyCount", 0) or 0),
                    collected_at=timestamp,
                    collection_order=order_index,
                )
                if _passes_filters(top_record, config):
                    output.append(top_record)
                    if len(output) >= config.max_comments:
                        break

                total_replies = top_record.reply_count
                if config.include_replies and total_replies > 0 and len(output) < config.max_comments:
                    for reply in self._iter_replies(comment_id):
                        reply_id = reply.get("id")
                        if not reply_id or reply_id in seen:
                            continue
                        seen.add(reply_id)
                        order_index += 1
                        reply_record = self._make_record(
                            metadata,
                            project_id=project_id,
                            comment_id=reply_id,
                            snippet=reply.get("snippet", {}),
                            is_reply=True,
                            parent_comment_id=comment_id,
                            reply_count=0,
                            collected_at=timestamp,
                            collection_order=order_index,
                        )
                        if _passes_filters(reply_record, config):
                            output.append(reply_record)
                            if len(output) >= config.max_comments:
                                break
                if len(output) >= config.max_comments:
                    break

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return metadata, output

    def _iter_replies(self, parent_id: str) -> Iterator[dict[str, Any]]:
        page_token: str | None = None
        while True:
            response = self.client.get_replies(parent_id, page_token=page_token, max_results=100)
            yield from response.get("items", [])
            page_token = response.get("nextPageToken")
            if not page_token:
                break

    @staticmethod
    def _make_record(
        metadata: VideoMetadata,
        *,
        project_id: str,
        comment_id: str,
        snippet: dict[str, Any],
        is_reply: bool,
        parent_comment_id: str | None,
        reply_count: int,
        collected_at: datetime,
        collection_order: int,
    ) -> CommentRecord:
        return CommentRecord(
            project_id=project_id,
            **metadata.model_dump(),
            comment_id=comment_id,
            parent_comment_id=parent_comment_id,
            is_reply=is_reply,
            comment_text=snippet.get("textDisplay", ""),
            comment_like_count=int(snippet.get("likeCount", 0) or 0),
            reply_count=reply_count,
            comment_published_at=_parse_datetime(snippet.get("publishedAt")),
            comment_updated_at=_parse_datetime(snippet.get("updatedAt")),
            author_display_name=snippet.get("authorDisplayName"),
            author_channel_id=_author_channel_id(snippet),
            collected_at=collected_at,
            collection_order=collection_order,
        )
