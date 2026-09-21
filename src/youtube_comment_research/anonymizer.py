from __future__ import annotations

from .models import CommentRecord


def anonymize_records(records: list[CommentRecord]) -> list[CommentRecord]:
    """Return copies with stable sequential author aliases and no channel IDs."""
    aliases: dict[str, str] = {}
    next_number = 1
    anonymized: list[CommentRecord] = []

    for record in records:
        identity = record.author_channel_id or record.author_display_name or f"unknown:{record.comment_id}"
        if identity not in aliases:
            aliases[identity] = f"Commenter {next_number:03d}"
            next_number += 1
        anonymized.append(
            record.model_copy(
                update={
                    "author_display_name": aliases[identity],
                    "author_channel_id": None,
                }
            )
        )
    return anonymized
