import json
from urllib.parse import parse_qs

import httpx

from youtube_comment_research.api_client import YouTubeAPIClient
from youtube_comment_research.collector import CommentCollector
from youtube_comment_research.models import CollectionConfig

VIDEO_ID = "25_IVHMGTVU"


def response(request: httpx.Request, payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, request=request, content=json.dumps(payload).encode())


def transport_handler(request: httpx.Request) -> httpx.Response:
    params = parse_qs(request.url.query.decode())
    path = request.url.path

    if path.endswith("/videos"):
        return response(
            request,
            {
                "items": [
                    {
                        "id": VIDEO_ID,
                        "snippet": {
                            "title": "Demo video",
                            "channelId": "channel-1",
                            "channelTitle": "Demo channel",
                            "publishedAt": "2026-01-01T00:00:00Z",
                        },
                        "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "3"},
                    }
                ]
            },
        )

    if path.endswith("/commentThreads"):
        if "pageToken" not in params:
            return response(
                request,
                {
                    "nextPageToken": "page-2",
                    "items": [
                        {
                            "snippet": {
                                "totalReplyCount": 2,
                                "topLevelComment": {
                                    "id": "top-1",
                                    "snippet": {
                                        "textDisplay": "Is it comfortable for running?",
                                        "likeCount": 4,
                                        "publishedAt": "2026-02-01T00:00:00Z",
                                        "updatedAt": "2026-02-01T00:00:00Z",
                                        "authorDisplayName": "Alice",
                                        "authorChannelId": {"value": "author-a"},
                                    },
                                },
                            }
                        }
                    ],
                },
            )
        return response(
            request,
            {
                "items": [
                    {
                        "snippet": {
                            "totalReplyCount": 0,
                            "topLevelComment": {
                                "id": "top-2",
                                "snippet": {
                                    "textDisplay": "Great review",
                                    "likeCount": 1,
                                    "publishedAt": "2026-02-02T00:00:00Z",
                                    "updatedAt": "2026-02-02T00:00:00Z",
                                    "authorDisplayName": "Bob",
                                },
                            },
                        }
                    }
                ]
            },
        )

    if path.endswith("/comments"):
        if "pageToken" not in params:
            return response(
                request,
                {
                    "nextPageToken": "reply-2",
                    "items": [
                        {
                            "id": "reply-1",
                            "snippet": {
                                "textDisplay": "Yes, for me.",
                                "likeCount": 2,
                                "publishedAt": "2026-02-01T01:00:00Z",
                                "updatedAt": "2026-02-01T01:00:00Z",
                                "authorDisplayName": "Carol",
                                "authorChannelId": {"value": "author-c"},
                            },
                        }
                    ],
                },
            )
        return response(
            request,
            {
                "items": [
                    {
                        "id": "reply-2",
                        "snippet": {
                            "textDisplay": "Not with glasses.",
                            "likeCount": 0,
                            "publishedAt": "2026-02-01T02:00:00Z",
                            "updatedAt": "2026-02-01T02:00:00Z",
                            "authorDisplayName": "Dan",
                        },
                    }
                ]
            },
        )

    return response(request, {"error": {"message": "Unexpected route"}}, 404)


def test_collector_fetches_all_reply_pages_and_thread_pages() -> None:
    client = httpx.Client(transport=httpx.MockTransport(transport_handler))
    api = YouTubeAPIClient("test-key", client=client, max_retries=0)
    collector = CommentCollector(api)
    _, records = collector.collect_video(
        VIDEO_ID,
        project_id="test",
        config=CollectionConfig(max_comments=10, include_replies=True, order="relevance"),
    )
    assert [record.comment_id for record in records] == ["top-1", "reply-1", "reply-2", "top-2"]
    assert records[1].parent_comment_id == "top-1"
    assert records[1].is_reply is True


def test_comments_disabled_error_is_specific() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos"):
            return response(
                request,
                {
                    "items": [
                        {
                            "id": VIDEO_ID,
                            "snippet": {"title": "Demo", "publishedAt": "2026-01-01T00:00:00Z"},
                            "statistics": {},
                        }
                    ]
                },
            )
        return response(
            request,
            {
                "error": {
                    "message": "The video has disabled comments.",
                    "errors": [{"reason": "commentsDisabled"}],
                }
            },
            403,
        )

    from youtube_comment_research.errors import CommentsDisabledError

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = YouTubeAPIClient("test-key", client=client, max_retries=0)
    collector = CommentCollector(api)
    import pytest

    with pytest.raises(CommentsDisabledError):
        collector.collect_video(
            VIDEO_ID,
            project_id="test",
            config=CollectionConfig(max_comments=10),
        )
