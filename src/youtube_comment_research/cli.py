from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated

import typer

from .analysis import analyze_file
from .anonymizer import anonymize_records
from .api_client import YouTubeAPIClient
from .collector import CommentCollector
from .decision_provider import (
    CodexFileDecisionSupportProvider,
    load_decision_support_provider,
)
from .decision_support import (
    build_decision_briefs,
    load_decision_briefs,
    resolve_decision_input,
    write_decision_briefs,
)
from .errors import (
    ClassificationWorkflowError,
    ConfigurationError,
    YouTubeCommentResearchError,
)
from .evidence import ResearchContext
from .exporters import export_csv, export_jsonl, export_summary, export_xlsx
from .human_review import (
    apply_review_file,
    load_human_review_bundle,
    prepare_review_packet,
)
from .insight_synthesis import (
    load_insights_file,
    merge_insights,
    prepare_insight_context,
)
from .models import CollectionConfig, CollectionSummary, VideoFailure
from .profiles import DEFAULT_PROFILE_ID, load_profile
from .readable_workbook import create_readable_workbook
from .reporting import load_rows
from .semantic_workflow import (
    create_blind_input_copy,
    merge_classifications,
    prepare_classification_batches,
)
from .theme_discovery import (
    generate_theme_insights,
    load_theme_insight_bundle,
)
from .theme_provider import (
    CodexFileThemeDiscoveryProvider,
    load_theme_discovery_provider,
)
from .urls import extract_video_id, load_video_inputs
from .verification import resolve_effective_insight_file

app = typer.Typer(
    no_args_is_help=True,
    help="Turn generic evidence into traceable research outputs, with an optional YouTube source workflow.",
)


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


@app.command()
def collect(
    url: Annotated[list[str] | None, typer.Option("--url", "-u", help="YouTube URL or video ID; repeatable.")] = None,
    input_file: Annotated[Path | None, typer.Option("--input-file", "-i", help="TXT/CSV file with one URL per line.")] = None,
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")] = Path("output"),
    max_comments: Annotated[int, typer.Option("--max-comments", min=1)] = 100,
    order: Annotated[str, typer.Option("--order", help="relevance or time")] = "relevance",
    include_replies: Annotated[bool, typer.Option("--include-replies/--no-replies")] = False,
    anonymize_authors: Annotated[bool, typer.Option("--anonymize-authors/--keep-authors")] = True,
    formats: Annotated[str, typer.Option("--formats", help="Comma-separated: csv,xlsx,jsonl")] = "csv,xlsx,jsonl",
    generate_report: Annotated[
        bool,
        typer.Option(
            "--generate-report/--no-generate-report",
            help="Generate the readable research workbook when XLSX is selected.",
        ),
    ] = True,
    minimum_likes: Annotated[int, typer.Option("--minimum-likes", min=0)] = 0,
    keyword_filter: Annotated[str | None, typer.Option("--keyword-filter")] = None,
    published_after: Annotated[str | None, typer.Option("--published-after", help="YYYY-MM-DD")] = None,
    published_before: Annotated[str | None, typer.Option("--published-before", help="YYYY-MM-DD")] = None,
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    profile: Annotated[
        str,
        typer.Option("--profile", help="Research profile ID."),
    ] = DEFAULT_PROFILE_ID,
    research_goal: Annotated[
        str | None,
        typer.Option("--research-goal", help="Project-specific research goal."),
    ] = None,
    research_questions: Annotated[
        str | None,
        typer.Option("--research-questions", help="Semicolon-separated questions."),
    ] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", envvar="YOUTUBE_API_KEY", hidden=True)] = None,
) -> None:
    """Collect comments using the official YouTube Data API v3."""
    try:
        selected_profile = load_profile(profile)
        inputs = load_video_inputs(url, input_file)
        if not inputs:
            raise ConfigurationError("Provide at least one --url or --input-file.")
        key = api_key or os.getenv("YOUTUBE_API_KEY")
        if not key:
            raise ConfigurationError("YOUTUBE_API_KEY is not set.")
        selected_formats = [item.strip().lower() for item in formats.split(",") if item.strip()]
        unsupported = set(selected_formats) - {"csv", "xlsx", "jsonl"}
        if unsupported:
            raise ConfigurationError(f"Unsupported formats: {', '.join(sorted(unsupported))}")

        config = CollectionConfig(
            max_comments=max_comments,
            order=order,
            include_replies=include_replies,
            minimum_likes=minimum_likes,
            keyword_filter=keyword_filter,
            published_after=_parse_date(published_after),
            published_before=_parse_date(published_before),
        )
        started_at = datetime.now(timezone.utc)
        batch_id = project_id or started_at.strftime("yt-comments-%Y%m%d-%H%M%S")
        batch_dir = output_dir / batch_id
        all_records = []
        failures: list[VideoFailure] = []
        successful = 0

        with YouTubeAPIClient(key) as client:
            collector = CommentCollector(client)
            for item in inputs:
                try:
                    video_id = extract_video_id(item)
                    typer.echo(f"Collecting {video_id} ...")
                    _, records = collector.collect_video(
                        item,
                        project_id=batch_id,
                        config=config,
                        collected_at=started_at,
                    )
                    all_records.extend(records)
                    successful += 1
                    typer.echo(f"  collected {len(records)} record(s)")
                except Exception as exc:  # Continue other videos and record precise failure type.
                    video_id = None
                    try:
                        video_id = extract_video_id(item)
                    except Exception:
                        pass
                    failures.append(
                        VideoFailure(
                            input_value=item,
                            video_id=video_id,
                            error_type=type(exc).__name__,
                            message=str(exc),
                        )
                    )
                    typer.echo(f"  failed: {type(exc).__name__}: {exc}", err=True)

        if anonymize_authors:
            all_records = anonymize_records(all_records)

        stamp = started_at.strftime("%Y%m%d_%H%M%S")
        base = batch_dir / f"youtube_comments_{stamp}"
        output_files: list[str] = []
        if "csv" in selected_formats:
            output_files.append(str(export_csv(all_records, base.with_suffix(".csv"))))
        if "xlsx" in selected_formats:
            output_files.append(
                str(
                    export_xlsx(
                        all_records,
                        base.with_suffix(".xlsx"),
                        generate_report=generate_report,
                        profile_id=selected_profile.profile_id,
                        research_goal=research_goal,
                        research_questions=(
                            [item.strip() for item in research_questions.split(";") if item.strip()]
                            if research_questions
                            else selected_profile.research_questions
                        ),
                    )
                )
            )
        if "jsonl" in selected_formats:
            output_files.append(str(export_jsonl(all_records, base.with_suffix(".jsonl"))))

        summary = CollectionSummary(
            project_id=batch_id,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            requested_videos=len(inputs),
            successful_videos=successful,
            failed_videos=len(failures),
            exported_comments=len(all_records),
            include_replies=include_replies,
            anonymized_authors=anonymize_authors,
            formats=selected_formats,
            output_files=output_files,
            failures=failures,
            notes=[
                "Only public comments returned by the YouTube Data API are included.",
                "The dataset is a research sample, not a representative consumer survey.",
                f"Research profile: {selected_profile.profile_id}",
            ],
        )
        summary_path = export_summary(summary, batch_dir / "summary.json")
        typer.echo(f"Done. Summary: {summary_path}")
        if failures and not all_records:
            raise typer.Exit(code=1)
    except (ConfigurationError, ValueError, FileNotFoundError, YouTubeCommentResearchError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command()
def analyze(
    input_path: Annotated[Path, typer.Argument(help="CSV, XLSX, or JSONL comment export.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")] = Path("analysis"),
    profile: Annotated[str, typer.Option("--profile")] = DEFAULT_PROFILE_ID,
) -> None:
    """Run deterministic, reviewable marketing-topic classification."""
    if profile != DEFAULT_PROFILE_ID:
        raise typer.BadParameter(
            "Rule-based analyze supports consumer_product only; use prepare-classification for other profiles."
        )
    classified, summary = analyze_file(input_path, output_dir)
    typer.echo(f"Classified comments: {classified}")
    typer.echo(f"Analysis summary: {summary}")


@app.command()
def report(
    input_file: Annotated[
        Path,
        typer.Argument(help="Existing CSV, XLSX, JSONL, or Generic Evidence JSON."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination .xlsx workbook."),
    ],
    profile: Annotated[str, typer.Option("--profile")] = DEFAULT_PROFILE_ID,
    research_goal: Annotated[str | None, typer.Option("--research-goal")] = None,
    research_questions: Annotated[
        str | None, typer.Option("--research-questions", help="Semicolon-separated questions.")
    ] = None,
    insights: Annotated[
        Path | None, typer.Option("--insights", help="Validated insight JSONL.")
    ] = None,
    theme_insights: Annotated[
        Path | None,
        typer.Option(
            "--theme-insights",
            help="Phase 3-lite Theme Discovery and Insight Draft JSON bundle.",
        ),
    ] = None,
    human_review: Annotated[
        Path | None,
        typer.Option(
            "--human-review",
            help="Phase 3-lite-2 Human Review bundle JSON.",
        ),
    ] = None,
    decision_briefs: Annotated[
        Path | None,
        typer.Option(
            "--decision-briefs",
            help="Validated Evidence-to-Decision JSON or JSONL.",
        ),
    ] = None,
    review_sample_seed: Annotated[
        int, typer.Option("--review-sample-seed")
    ] = 20260816,
) -> None:
    """Regenerate a readable research workbook from an existing export."""
    try:
        if output.suffix.lower() != ".xlsx":
            raise ConfigurationError("--output must use the .xlsx extension.")
        rows = load_rows(input_file)
        profile_config = load_profile(profile)
        insight_rows = (
            load_insights_file(insights, rows, profile_id=profile) if insights else []
        )
        theme_bundle = (
            load_theme_insight_bundle(
                theme_insights,
                source_rows=rows,
                profile_id=profile,
            )
            if theme_insights
            else None
        )
        review_bundle = (
            load_human_review_bundle(human_review) if human_review else None
        )
        decision_brief_rows = (
            load_decision_briefs(decision_briefs) if decision_briefs else []
        )
        if review_bundle and review_bundle.profile_id != profile:
            raise ClassificationWorkflowError(
                f"Human Review profile {review_bundle.profile_id!r} does not match "
                f"{profile!r}."
            )
        result = create_readable_workbook(
            rows,
            output,
            profile_id=profile,
            research_goal=research_goal or profile_config.research_goal,
            research_questions=(
                [item.strip() for item in research_questions.split(";") if item.strip()]
                if research_questions
                else profile_config.research_questions
            ),
            insights=insight_rows,
            theme_bundle=theme_bundle,
            human_review_bundle=review_bundle,
            decision_briefs=decision_brief_rows,
            review_sample_seed=review_sample_seed,
        )
        typer.echo(f"Readable workbook: {result}")
    except (
        ClassificationWorkflowError,
        ConfigurationError,
        ValueError,
        FileNotFoundError,
    ) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("generate-decision-brief")
def generate_decision_brief_command(
    input_file: Annotated[
        Path,
        typer.Argument(
            help="Effective Insight JSONL or source Theme + Insight Draft JSON bundle."
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination Decision Brief JSON/JSONL."),
    ],
    human_review: Annotated[
        Path | None,
        typer.Option(
            "--human-review",
            help="Optional Human Review bundle when INPUT is a Theme bundle.",
        ),
    ] = None,
    work_dir: Annotated[
        Path | None,
        typer.Option(
            "--work-dir",
            help="Local request/response directory for file-based Codex assistance.",
        ),
    ] = None,
    decision_provider: Annotated[
        str | None,
        typer.Option(
            "--decision-provider",
            help="Authorized provider factory in module:attribute form.",
        ),
    ] = None,
) -> None:
    """Generate evidence-bound, AI-assisted decision support; never a final decision."""
    try:
        effective = resolve_decision_input(
            input_file,
            human_review_file=human_review,
        )
        provider = (
            load_decision_support_provider(decision_provider)
            if decision_provider
            else CodexFileDecisionSupportProvider(
                work_dir or output.parent / f"{output.stem}_work"
            )
        )
        records = build_decision_briefs(effective, provider)
        write_decision_briefs(output, records)
        typer.echo(f"Decision Briefs generated: {len(records)}")
        typer.echo("Status: AI-generated decision support")
        typer.echo(f"Decision Brief output: {output}")
    except (ClassificationWorkflowError, ValueError, FileNotFoundError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("prepare-review")
def prepare_review_command(
    input_file: Annotated[
        Path,
        typer.Argument(help="Existing preliminary Theme + Insight Draft JSON bundle."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination Human Review Packet JSON."),
    ],
) -> None:
    """Prepare optional Theme, Insight Draft, and evidence verification items."""
    try:
        packet = prepare_review_packet(input_file, output)
        typer.echo(f"Optional review items: {len(packet.items)}")
        typer.echo(f"Human Review Packet: {output}")
    except (ClassificationWorkflowError, ValueError, FileNotFoundError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("apply-review")
def apply_review_command(
    input_file: Annotated[
        Path,
        typer.Argument(help="Original preliminary Theme + Insight Draft JSON bundle."),
    ],
    review: Annotated[
        Path,
        typer.Option("--review", help="Explicit Human Review Response JSON."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination Human Review bundle JSON."),
    ],
) -> None:
    """Apply optional confirm/edit/reject decisions without changing AI drafts."""
    try:
        bundle = apply_review_file(input_file, review, output)
        typer.echo(f"Confirmed insights: {len(bundle.confirmed_insights)}")
        typer.echo(f"Rejected reviews: {len(bundle.rejected_review_ids)}")
        typer.echo(f"Pending reviews: {len(bundle.pending_review_ids)}")
        typer.echo(f"Human Review bundle: {output}")
    except (ClassificationWorkflowError, ValueError, FileNotFoundError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("resolve-insights")
def resolve_insights_command(
    input_file: Annotated[
        Path,
        typer.Argument(help="Source Theme + Insight Draft JSON bundle."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination Effective Insight JSONL."),
    ],
    human_review: Annotated[
        Path | None,
        typer.Option(
            "--human-review",
            help="Optional Human Review bundle with confirm/edit/reject decisions.",
        ),
    ] = None,
    require_review: Annotated[
        bool,
        typer.Option(
            "--require-review/--no-require-review",
            help="Strict mode: include only reviewed or edited insights.",
        ),
    ] = False,
) -> None:
    """Resolve AI and optionally verified insights into one downstream interface."""
    try:
        records = resolve_effective_insight_file(
            input_file,
            output,
            human_review_file=human_review,
            require_review=require_review,
        )
        typer.echo(f"Downstream eligible insights: {len(records)}")
        typer.echo(f"Effective Insights: {output}")
    except (ClassificationWorkflowError, ValueError, FileNotFoundError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("prepare-classification")
def prepare_classification(
    input_file: Annotated[
        Path,
        typer.Argument(help="Source comment CSV or Generic Evidence JSON array."),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Destination batch directory."),
    ],
    batch_size: Annotated[
        int,
        typer.Option("--batch-size", min=1, max=20),
    ] = 20,
    profile: Annotated[str, typer.Option("--profile")] = DEFAULT_PROFILE_ID,
    classifier_version: Annotated[
        str, typer.Option("--classifier-version")
    ] = "codex-semantic-v2",
    review_sample_seed: Annotated[
        int, typer.Option("--review-sample-seed")
    ] = 20260816,
) -> None:
    """Prepare sanitized, checksummed JSONL batches for Codex classification."""
    try:
        summary = prepare_classification_batches(
            input_file,
            output_dir,
            batch_size=batch_size,
            profile_id=profile,
            classifier_version=classifier_version,
            review_sample_seed=review_sample_seed,
        )
        typer.echo(f"Prepared records: {summary.total_records}")
        typer.echo(f"Batches: {summary.batch_count}")
        typer.echo(f"Manifest: {summary.manifest_path}")
        typer.echo(f"Checksums: {summary.checksums_path}")
    except ClassificationWorkflowError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("generate-insights")
def generate_insights_command(
    input_file: Annotated[
        Path,
        typer.Argument(help="Existing classified CSV/XLSX/JSONL or Generic Evidence JSON."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination Theme + Insight Draft .json file."),
    ],
    profile: Annotated[str, typer.Option("--profile")] = DEFAULT_PROFILE_ID,
    theme_method: Annotated[
        str,
        typer.Option(
            "--theme-method",
            help=(
                "Theme discovery method: taxonomy-based baseline, or file-based "
                "LLM-assisted multi-pass workflow."
            ),
        ),
    ] = "taxonomy",
    theme_batch_size: Annotated[
        int,
        typer.Option("--theme-batch-size", min=1, max=100),
    ] = 20,
    min_evidence_count: Annotated[
        int,
        typer.Option("--min-evidence-count", min=1),
    ] = 2,
    llm_work_dir: Annotated[
        Path | None,
        typer.Option(
            "--llm-work-dir",
            help="Local request/response directory for Codex-assisted LLM themes.",
        ),
    ] = None,
    theme_provider: Annotated[
        str | None,
        typer.Option(
            "--theme-provider",
            help="Authorized provider factory in module:attribute form.",
        ),
    ] = None,
    research_question: Annotated[
        str | None,
        typer.Option("--research-question", help="Question that frames Theme Discovery and drafts."),
    ] = None,
    decision_context: Annotated[
        str | None,
        typer.Option("--decision-context", help="Decision situation the research may inform."),
    ] = None,
    analysis_mode: Annotated[
        str,
        typer.Option("--analysis-mode", help="exploratory or decision_focused"),
    ] = "exploratory",
) -> None:
    """Generate taxonomy-based themes and drafts, or prepare/consume LLM-assisted files."""
    try:
        normalized_method = theme_method.strip().casefold()
        if normalized_method not in {"taxonomy", "llm"}:
            raise ClassificationWorkflowError(
                "--theme-method must be either taxonomy or llm."
            )
        provider = None
        if normalized_method == "llm":
            provider = (
                load_theme_discovery_provider(theme_provider)
                if theme_provider
                else CodexFileThemeDiscoveryProvider(
                    llm_work_dir or output.parent / f"{output.stem}_llm_work"
                )
            )
        result = generate_theme_insights(
            input_file,
            output,
            profile_id=profile,
            theme_method=normalized_method,
            provider=provider,
            batch_size=theme_batch_size,
            min_evidence_count=min_evidence_count,
            research_context=ResearchContext(
                research_question=research_question,
                decision_context=decision_context,
                analysis_mode=analysis_mode,
            ),
        )
        typer.echo(f"Themes generated: {len(result.themes)}")
        typer.echo(f"Insight drafts generated: {len(result.insight_drafts)}")
        typer.echo(f"Draft label: {result.draft_label}")
        typer.echo(f"Theme + Insight output: {output}")
    except (
        ClassificationWorkflowError,
        ValueError,
        FileNotFoundError,
    ) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("merge-classification")
def merge_classification(
    input_file: Annotated[
        Path,
        typer.Argument(help="Original comment CSV, Generic Evidence JSON, or merged CSV."),
    ],
    classification_dir: Annotated[
        Path,
        typer.Argument(help="Prepared directory with classified_batch_*.jsonl."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination merged .csv file."),
    ],
    profile: Annotated[str, typer.Option("--profile")] = DEFAULT_PROFILE_ID,
    review_sample_seed: Annotated[
        int | None, typer.Option("--review-sample-seed")
    ] = None,
) -> None:
    """Validate and merge Codex-assisted JSONL without overwriting human review."""
    try:
        summary = merge_classifications(
            input_file,
            classification_dir,
            output,
            profile_id=profile,
            review_sample_seed=review_sample_seed,
        )
        typer.echo(f"Total records: {summary.total_records}")
        typer.echo(f"Successfully classified: {summary.classified_records}")
        typer.echo(f"Manual review required: {summary.manual_review_count}")
        typer.echo(f"Category counts: {summary.category_counts}")
        typer.echo(f"Failed batches: {summary.failed_batches}")
        typer.echo(f"Merged CSV: {summary.output_file}")
    except ClassificationWorkflowError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("prepare-insights")
def prepare_insights_command(
    input_file: Annotated[Path, typer.Argument(help="Merged classification CSV.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")],
    profile: Annotated[str, typer.Option("--profile")] = DEFAULT_PROFILE_ID,
) -> None:
    """Prepare traceable context for offline Codex-assisted insight synthesis."""
    try:
        summary = prepare_insight_context(
            input_file, output_dir, profile_id=profile
        )
        typer.echo(f"Prepared insight records: {summary.total_records}")
        typer.echo(f"Insight context: {summary.context_file}")
        typer.echo(f"Insight manifest: {summary.manifest_file}")
    except ClassificationWorkflowError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("merge-insights")
def merge_insights_command(
    input_file: Annotated[Path, typer.Argument(help="Merged classification CSV.")],
    insight_dir: Annotated[Path, typer.Argument(help="Prepared insight directory.")],
    output: Annotated[Path, typer.Option("--output", "-o")],
    profile: Annotated[str, typer.Option("--profile")] = DEFAULT_PROFILE_ID,
) -> None:
    """Validate traceable insight JSONL against the current project and profile."""
    try:
        results = merge_insights(
            input_file, insight_dir, output, profile_id=profile
        )
        typer.echo(f"Validated insights: {len(results)}")
        typer.echo(f"Merged insights: {output}")
    except ClassificationWorkflowError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("sanitize-classification-input")
def sanitize_classification_input(
    input_file: Annotated[
        Path,
        typer.Argument(help="CSV that may contain predictions or expected labels."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Sanitized destination .csv file."),
    ],
) -> None:
    """Create a blind input copy containing only comment context fields."""
    try:
        count = create_blind_input_copy(input_file, output)
        typer.echo(f"Sanitized records: {count}")
        typer.echo(f"Sanitized CSV: {output}")
    except ClassificationWorkflowError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("validate-url")
def validate_url(value: str) -> None:
    """Print the parsed video ID for a YouTube URL or raw ID."""
    typer.echo(extract_video_id(value))


if __name__ == "__main__":
    app()
