"""Source-neutral research utilities with a YouTube collection adapter."""

from .evidence import EvidenceRecord, ResearchContext
from .decision_support import DecisionBriefRecord, build_decision_briefs
from .models import CollectionConfig, CommentRecord, VideoMetadata
from .urls import extract_video_id
from .verification import EffectiveInsightRecord, resolve_effective_insights

__all__ = [
    "CollectionConfig",
    "CommentRecord",
    "EvidenceRecord",
    "DecisionBriefRecord",
    "EffectiveInsightRecord",
    "ResearchContext",
    "VideoMetadata",
    "extract_video_id",
    "resolve_effective_insights",
    "build_decision_briefs",
]
__version__ = "0.1.0"
