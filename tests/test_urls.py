import pytest

from youtube_comment_research.errors import InvalidVideoURLError
from youtube_comment_research.urls import extract_video_id


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("25_IVHMGTVU", "25_IVHMGTVU"),
        ("https://www.youtube.com/watch?v=25_IVHMGTVU", "25_IVHMGTVU"),
        ("https://youtu.be/25_IVHMGTVU?t=12", "25_IVHMGTVU"),
        ("https://www.youtube.com/shorts/PWJFtFLNQJk", "PWJFtFLNQJk"),
        ("https://youtube.com/embed/cuRJYVZx5ME", "cuRJYVZx5ME"),
    ],
)
def test_extract_video_id(value: str, expected: str) -> None:
    assert extract_video_id(value) == expected


def test_extract_video_id_rejects_invalid_value() -> None:
    with pytest.raises(InvalidVideoURLError):
        extract_video_id("not-a-youtube-url")
