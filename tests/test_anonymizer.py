from datetime import datetime, timezone

from youtube_comment_research.anonymizer import anonymize_records
from youtube_comment_research.models import CommentRecord


def make_record(comment_id: str, author_id: str, author_name: str) -> CommentRecord:
    return CommentRecord(
        project_id="test",
        video_id="25_IVHMGTVU",
        video_url="https://www.youtube.com/watch?v=25_IVHMGTVU",
        video_title="Video",
        comment_id=comment_id,
        comment_text="Hello",
        author_display_name=author_name,
        author_channel_id=author_id,
        collected_at=datetime.now(timezone.utc),
        collection_order=1,
    )


def test_anonymization_is_stable_per_author() -> None:
    records = [
        make_record("c1", "author-a", "Alice"),
        make_record("c2", "author-a", "Alice"),
        make_record("c3", "author-b", "Bob"),
    ]
    result = anonymize_records(records)
    assert [record.author_display_name for record in result] == [
        "Commenter 001",
        "Commenter 001",
        "Commenter 002",
    ]
    assert all(record.author_channel_id is None for record in result)
