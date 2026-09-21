from youtube_comment_research.analysis import classify_comment
from youtube_comment_research.reporting import classify_text


def test_rule_based_categories() -> None:
    assert classify_comment("Is this worth the price?")[0] == "Purchase Question"
    assert classify_comment("I use these for running every day")[0] == "Usage Scenario"
    assert classify_comment("How does this compare vs Bose?")[0] == "Competitor Comparison"


def test_brand_mention_alone_is_not_competitor_comparison() -> None:
    assert classify_text("Shokz").primary_category != "Competitor Comparison"


def test_product_feature_question_is_not_purchase_question() -> None:
    result = classify_text("Does Shokz support multipoint Bluetooth?")
    assert result.primary_category == "Product Question"
    assert result.primary_category != "Purchase Question"


def test_low_confidence_result_requires_manual_review() -> None:
    result = classify_text("Interesting.")
    assert result.category_confidence < 0.65
    assert result.manual_review is True
