from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Iterable

CATEGORY_LABELS: tuple[tuple[str, str], ...] = (
    ("Product Praise", "产品认可"),
    ("Product Concern", "产品顾虑"),
    ("Product Question", "产品问题"),
    ("Purchase Question", "购买疑问"),
    ("Usage Scenario", "使用场景"),
    ("Competitor Comparison", "竞品比较"),
    ("Purchase Intent", "购买意向"),
    ("Feature Request", "功能需求"),
    ("After-sales Issue", "售后问题"),
    ("Other", "其他"),
)
CATEGORY_CN = dict(CATEGORY_LABELS)


@dataclass(frozen=True)
class EntityPattern:
    canonical: str
    entity_type: str
    pattern: re.Pattern[str]
    implied_brand: str | None = None


@dataclass(frozen=True)
class EntityMention:
    canonical: str
    entity_type: str
    matched_text: str
    start: int
    end: int


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    pattern: re.Pattern[str]
    score: float
    strength: str


@dataclass(frozen=True)
class RuleMatch:
    matched_phrase: str
    matched_span: str
    rule_id: str
    category: str
    score: float
    target_entity: str
    strength: str

    def export_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ClassificationResult:
    primary_category: str
    primary_category_cn: str
    secondary_categories: tuple[str, ...]
    secondary_categories_cn: tuple[str, ...]
    subtopics: tuple[str, ...]
    lifecycle_stage: str
    mentioned_brands: tuple[str, ...]
    mentioned_products: tuple[str, ...]
    category_confidence: float
    category_evidence: str
    matched_rules: tuple[RuleMatch, ...]
    manual_review: bool
    review_reason: str
    category_scores: dict[str, float]

    @property
    def category_cn(self) -> str:
        """Compatibility alias for Phase 2A callers."""
        return self.primary_category_cn

    @property
    def subtopic(self) -> str:
        """Compatibility alias for Phase 2A callers."""
        return self.subtopics[0] if self.subtopics else "general"

    @property
    def strong_evidence_count(self) -> int:
        return sum(
            match.strength == "strong" and match.category == self.primary_category
            for match in self.matched_rules
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "primary_category": self.primary_category,
            "primary_category_cn": self.primary_category_cn,
            "category_cn": self.primary_category_cn,
            "secondary_categories": "; ".join(self.secondary_categories),
            "secondary_categories_cn": "; ".join(self.secondary_categories_cn),
            "subtopics": "; ".join(self.subtopics),
            "subtopic": self.subtopic,
            "lifecycle_stage": self.lifecycle_stage,
            "mentioned_brands": "; ".join(self.mentioned_brands),
            "mentioned_products": "; ".join(self.mentioned_products),
            "category_confidence": self.category_confidence,
            "category_evidence": self.category_evidence,
            "matched_rules": json.dumps(
                [match.export_dict() for match in self.matched_rules],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "manual_review": self.manual_review,
            "review_reason": self.review_reason,
        }


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


ENTITY_PATTERNS: tuple[EntityPattern, ...] = (
    EntityPattern("OpenRun Pro", "product", _rx(r"\bopen\s*run\s+pro(?:\s*\d+)?\b"), "Shokz"),
    EntityPattern("OpenFit 2+", "product", _rx(r"\bopen\s*fit\s*2\s*\+?\b"), "Shokz"),
    EntityPattern("OpenSwim Pro", "product", _rx(r"\bopen\s*swim\s+pro\b"), "Shokz"),
    EntityPattern("WH-1000XM6", "product", _rx(r"\bwh[\s-]?1000xm6\b"), "Sony"),
    EntityPattern("AirPods", "product", _rx(r"\bair\s*pods?(?:\s+pro(?:\s*\d+)?)?\b"), "Apple"),
    EntityPattern("OpenRun", "product", _rx(r"\bopen\s*run\b"), "Shokz"),
    EntityPattern("OpenFit", "product", _rx(r"\bopen\s*fit\b"), "Shokz"),
    EntityPattern("OpenSwim", "product", _rx(r"\bopen\s*swim\b"), "Shokz"),
    EntityPattern("Shokz", "brand", _rx(r"\bshokz\b")),
    EntityPattern("Bose", "brand", _rx(r"\bbose\b")),
    EntityPattern("JBL", "brand", _rx(r"\bjbl\b")),
    EntityPattern("Sony", "brand", _rx(r"\bsony\b")),
    EntityPattern("Apple", "brand", _rx(r"\bapple\b")),
)


def _rule(
    rule_id: str,
    category: str,
    pattern: str,
    score: float,
    strength: str,
) -> Rule:
    return Rule(rule_id, category, _rx(pattern), score, strength)


CATEGORY_RULES: tuple[Rule, ...] = (
    _rule("praise.works_great", "Product Praise", r"\bworks?\s+(?:really\s+|very\s+)?great\b", 3.2, "strong"),
    _rule("praise.love_these", "Product Praise", r"\blove\s+(?:these|them|mine|my\s+\w+)\b", 3.0, "strong"),
    _rule("praise.very_comfortable", "Product Praise", r"\b(?:very|really|extremely|so)\s+comfortable\b", 3.0, "strong"),
    _rule("praise.game_changer", "Product Praise", r"\bgame[\s-]?changer\b", 3.2, "strong"),
    _rule("praise.couldnt_be_happier", "Product Praise", r"\bcould(?:n['’]t| not)\s+be\s+happier\b", 3.2, "strong"),
    _rule("praise.satisfied", "Product Praise", r"\b(?:very\s+)?satisf(?:ied|ying)\b", 2.5, "strong"),
    _rule("praise.impressed", "Product Praise", r"\b(?:very\s+|extremely\s+)?impress(?:ed|ive)\b", 2.5, "strong"),
    _rule("praise.enjoying", "Product Praise", r"\benjoy(?:ing|ed|s)?\b", 2.2, "strong"),
    _rule("praise.incredible", "Product Praise", r"\bincredib(?:le|ly)\b", 2.2, "strong"),
    _rule("praise.love", "Product Praise", r"\blov(?:e|ed|ing|es)\b", 1.4, "weak"),
    _rule("praise.amazing", "Product Praise", r"\bamazing\b", 1.5, "weak"),
    _rule("praise.great", "Product Praise", r"\bgreat\b", 1.2, "weak"),
    _rule("praise.comfortable", "Product Praise", r"\bcomfortable\b", 1.3, "weak"),
    _rule("praise.positive_cn", "Product Praise", r"(?:好用|喜欢|舒服|很棒|优秀|不错|满意)", 2.0, "strong"),
    _rule("concern.not_enough_bass", "Product Concern", r"\b(?:not|no)\s+enough\s+bass\b|\bno\s+bass\b", 3.4, "strong"),
    _rule("concern.sound_leakage", "Product Concern", r"\bsound\b.{0,15}\bleak(?:age|s|ed|ing)?\b|\bleak(?:s|ed|ing)?\s+sound\b", 3.2, "strong"),
    _rule("concern.falls_off", "Product Concern", r"\bfall(?:s|ing)?\s+off\b", 3.0, "strong"),
    _rule("concern.cracked_broke", "Product Concern", r"\b(?:crack(?:ed|ing|s)?|brok(?:e|en))\b", 2.8, "strong"),
    _rule("concern.uncomfortable", "Product Concern", r"\buncomfortable\b|\bnot\s+comfortable\b", 3.0, "strong"),
    _rule("concern.disappointed", "Product Concern", r"\bdisappoint(?:ed|ing|ment)\b", 2.8, "strong"),
    _rule("concern.stopped_working", "Product Concern", r"\bstopp?ed\s+working\b|\bdoes(?:n['’]t| not)\s+work\b", 3.0, "strong"),
    _rule("concern.poor_bad", "Product Concern", r"\b(?:poor|bad)\s+(?:quality|sound|bass|fit|build)\b", 2.5, "strong"),
    _rule("concern.rain", "Product Concern", r"\brain(?:y)?\b", 1.0, "weak"),
    _rule("concern.hoodie", "Product Concern", r"\bhoodie\b", 0.8, "weak"),
    _rule("concern.replacement", "Product Concern", r"\breplac(?:e|ed|ement|ing)\b", 1.2, "weak"),
    _rule("concern.generic", "Product Concern", r"\b(?:problem|issue|overheat(?:s|ing)?|hurt(?:s|ing)?|painful)\b", 1.4, "weak"),
    _rule("concern.cn", "Product Concern", r"(?:漏音|破裂|坏了|不舒服|失望|掉落|太贵|问题)", 2.5, "strong"),
    _rule("question.talk", "Product Question", r"\bcan\s+(?:you|i|we)\s+talk\s+(?:on|with|through)\s+(?:them|it|these)\b", 3.4, "strong"),
    _rule("question.microphone", "Product Question", r"\bdoes\s+(?:the\s+)?(?:mic|microphone)\s+work\b", 3.4, "strong"),
    _rule("question.earplugs", "Product Question", r"\bwork(?:s|ing)?\s+with\s+ear\s*plugs?\b", 3.4, "strong"),
    _rule("question.hearing_aid", "Product Question", r"\bcompatib(?:le|ility)\s+with\s+.+?hearing\s+aids?\b", 3.4, "strong"),
    _rule("question.charge_time", "Product Question", r"\bhow\s+long\s+does\s+it\s+take\s+to\s+(?:(?:get|reach)\s+a\s+full\s+charge|(?:fully\s+)?charge)\b", 3.4, "strong"),
    _rule("question.louder", "Product Question", r"\bare\s+(?:they|these|those)\s+(?:any\s+)?louder\b", 3.2, "strong"),
    _rule("question.leak_sound", "Product Question", r"\bdoes\s+(?:it|the\s+sound)\s+leak\b|\bhow\s+(?:much\s+)?sound\s+leak", 3.2, "strong"),
    _rule("question.product_updates", "Product Question", r"\bany\s+updates?\s+(?:for|on)\b", 3.0, "strong"),
    _rule(
        "question.feature",
        "Product Question",
        r"\b(?:does|do|can|could|will|is|are)\b.{0,45}\b(?:battery|charging?|waterproof|bluetooth|multipoint|connect(?:ion)?|compatib(?:le|ility)|microphone|mic|sound|volume|bass|fit|latency|power\s+off)\b",
        2.4,
        "strong",
    ),
    _rule("purchase.worth", "Purchase Question", r"\bworth\s+(?:it|buying|the\s+(?:price|money|investment))\b", 3.2, "strong"),
    _rule("purchase.price", "Purchase Question", r"\b(?:how\s+much|what(?:'s|\s+is)\s+the\s+price|where\s+(?:can\s+i|to)\s+buy)\b", 3.2, "strong"),
    _rule("purchase.availability_question", "Purchase Question", r"\b(?:is|are|will)\b.{0,35}\bavailable\b|\bwhen\s+will\b.{0,30}\b(?:launch|release)\b", 3.0, "strong"),
    _rule("purchase.considering_question", "Purchase Question", r"\bwonder(?:ing)?\s+(?:if|whether)\s+i\s+should\s+(?:buy|get|pick\s+up|choose)\b", 3.0, "strong"),
    _rule("purchase.question_cn", "Purchase Question", r"(?:值得买吗|多少钱|价格多少|哪里买|什么时候上市)", 3.0, "strong"),
    _rule("usage.running", "Usage Scenario", r"\b(?:run|runs|runner|runners|running)\b", 1.8, "strong"),
    _rule("usage.office", "Usage Scenario", r"\b(?:office|at\s+work|workplace|commut(?:e|ing))\b", 1.8, "strong"),
    _rule("usage.swimming", "Usage Scenario", r"\b(?:swim|swims|swimming|underwater|pool)\b", 1.8, "strong"),
    _rule("usage.gaming", "Usage Scenario", r"\b(?:gaming|gamer|gameplay)\b", 1.8, "strong"),
    _rule("usage.other", "Usage Scenario", r"\b(?:cycling|bike|gym|workout|travel|hiking)\b", 1.6, "strong"),
    _rule("usage.cn", "Usage Scenario", r"(?:跑步|骑行|通勤|办公室|健身|游泳|游戏)", 2.0, "strong"),
    _rule("intent.considering", "Purchase Intent", r"\b(?:thinking\s+(?:of|about)|considering)\s+(?:buying|getting|purchasing)\b", 3.0, "strong"),
    _rule("intent.cant_wait", "Purchase Intent", r"\bcan(?:'|’)t\s+wait\s+to\s+(?:buy|get|order)\b", 3.2, "strong"),
    _rule("intent.plan", "Purchase Intent", r"\b(?:plan(?:ning)?|intend(?:ing)?)\s+to\s+(?:buy|get|purchase)\b", 3.0, "strong"),
    _rule("intent.will_buy", "Purchase Intent", r"\b(?:i(?:'ll|\s+will)|we(?:'ll|\s+will))\s+(?:buy|get|order)\b", 2.8, "strong"),
    _rule("intent.hope_launch", "Purchase Intent", r"\b(?:hope|hoping)\b.{0,40}\b(?:comes?|releases?|launches?)\b.{0,25}\b(?:soon|usa|u\.s\.|uk|u\.k\.)\b", 2.8, "strong"),
    _rule("intent.cn", "Purchase Intent", r"(?:考虑购买|打算买|准备买|等不及要买)", 3.0, "strong"),
    _rule("feature.wish", "Feature Request", r"\bi\s+wish\b", 5.0, "strong"),
    _rule("feature.would_love", "Feature Request", r"\bwould\s+love\b", 2.8, "strong"),
    _rule("feature.please_add", "Feature Request", r"\bplease\s+add\b", 3.2, "strong"),
    _rule("feature.new_colour", "Feature Request", r"\bneeds?\s+(?:a\s+)?new\s+colou?r\b", 3.2, "strong"),
    _rule("feature.olive_green", "Feature Request", r"\bolive\s+green\b", 3.0, "strong"),
    _rule("feature.make_one", "Feature Request", r"\bmake\s+(?:one|a\s+version)\s+with\b", 3.0, "strong"),
    _rule("feature.should_have", "Feature Request", r"\bshould\s+have\b", 2.8, "strong"),
    _rule("feature.cn", "Feature Request", r"(?:希望增加|希望有|建议增加|能不能增加)", 3.0, "strong"),
    _rule("after_sales.warranty", "After-sales Issue", r"\b(?:warranty|customer\s+support|after[\s-]?sales)\b", 2.8, "strong"),
    _rule("after_sales.return", "After-sales Issue", r"\b(?:return(?:ed|ing)?|refund(?:ed|ing)?|sent\s+back)\b", 2.8, "strong"),
    _rule("after_sales.replacement", "After-sales Issue", r"\b(?:warranty\s+replacement|replacement\s+pair)\b", 3.0, "strong"),
    _rule("after_sales.cn", "After-sales Issue", r"(?:售后|退货|退款|保修|换货)", 3.0, "strong"),
)

COMPARISON_RULES: tuple[Rule, ...] = (
    _rule("compare.vs", "Competitor Comparison", r"\bvs\.?\b|\bversus\b", 3.2, "strong"),
    _rule("compare.better_worse", "Competitor Comparison", r"\b(?:better|worse|louder|quieter)\s+than\b", 3.2, "strong"),
    _rule("compare.compared", "Competitor Comparison", r"\bcompared?\s+(?:with|to)\b", 3.0, "strong"),
    _rule("compare.compare_with", "Competitor Comparison", r"\bcompare\b.{1,45}\bwith\b", 3.0, "strong"),
    _rule("compare.prefer_to", "Competitor Comparison", r"\bprefer\b.{1,45}\bto\b", 3.0, "strong"),
    _rule("compare.switched", "Competitor Comparison", r"\bswitch(?:ed|ing)?\s+from\b.{1,55}\bto\b", 3.2, "strong"),
    _rule("compare.previous_better", "Competitor Comparison", r"\b(?:had|used|owned)\b.{0,45}\bbefore\s+these\b.{0,40}\b(?:better|worse)\b", 3.0, "strong"),
    _rule("compare.symbol", "Competitor Comparison", r"\b[\w+-]+\s*>\s*[\w+-]+\b", 2.8, "strong"),
    _rule("compare.cn", "Competitor Comparison", r"(?:对比|相比|哪个好|孰优)", 3.0, "strong"),
)

LIFECYCLE_RULES: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("Waiting for Launch", _rx(r"\bwhen\s+will\b.{0,35}\b(?:launch|release)\b"), 4.0),
    ("Waiting for Launch", _rx(r"\b(?:available|coming|launching|releasing)\s+(?:in|to)\s+(?:the\s+)?(?:us|u\.s\.|usa|uk|u\.k\.)\b"), 4.0),
    ("Waiting for Launch", _rx(r"\bwaiting\s+for\b.{0,45}\b(?:launch|release|availability)\b"), 4.0),
    ("Waiting for Launch", _rx(r"\b(?:hope|hoping)\b.{0,45}\b(?:comes?|releases?|launches?)\b.{0,25}\bsoon\b"), 3.5),
    ("Waiting for Launch", _rx(r"\bany\s+updates?\s+(?:for|on)\b"), 3.0),
    ("Repurchasing", _rx(r"\b(?:buying|bought|get(?:ting)?)\s+(?:another|a\s+new\s+pair|new\s+ones?)\b|\bsecond\s+pair\b"), 3.5),
    ("Replacing", _rx(r"\b(?:replace|replacing|replacement)\b"), 3.0),
    ("Returned", _rx(r"\b(?:returned?|sent\s+back|refunded?)\b"), 3.0),
    ("Considering", _rx(r"\b(?:thinking\s+(?:of|about)|considering)\s+(?:buying|getting|purchasing)\b"), 3.5),
    ("Considering", _rx(r"\b(?:on\s+my\s+list|wondering\s+if\s+i\s+should|shopping\s+for)\b"), 2.5),
    ("Considering", _rx(r"\b(?:research(?:ing)?|look(?:ing)?\s+into)\b.{0,45}\b(?:ones?|pair|model)\b"), 2.5),
    ("Considering", _rx(r"\b(?:second[-\s]?guess(?:ing)?|in\s+doubt\s+if\s+i\s+try)\b"), 2.5),
    ("Purchased", _rx(r"\b(?:i|we)\s+(?:just\s+)?(?:bought|purchased|ordered|got|received)\b|\bjust\s+got\s+mine\b"), 3.0),
    ("Purchased", _rx(r"\bjust\s+bought\b"), 3.0),
    ("Purchased", _rx(r"\b(?:and\s+)?bought\s+(?:shokz|these|them|a\s+(?:pair|set))\b"), 3.0),
    ("Using", _rx(r"\b(?:i(?:'ve|\s+have)\s+(?:been\s+)?using|use\s+(?:them|these|it)\s+(?:daily|every|for)|enjoying\s+my|had\s+my\b|(?:after|for)\s+\d+\s+(?:years?|months?)\s+using)\b"), 2.5),
)

LIFECYCLE_PRIORITY = {
    "Waiting for Launch": 8,
    "Repurchasing": 7,
    "Replacing": 6,
    "Returned": 5,
    "Considering": 4,
    "Purchased": 3,
    "Using": 2,
    "Unknown": 1,
}

SUBTOPIC_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hearing aid compatibility", _rx(r"\bhearing\s+aids?\b")),
    ("earplug compatibility", _rx(r"\bear\s*plugs?\b")),
    ("sound leakage", _rx(r"\bsound\s+leak(?:age|ing)?\b|\bleak(?:s|ing)?\s+sound\b|\bleakage\b")),
    ("microphone/calls", _rx(r"\b(?:microphones?|mics?|calls?|talk(?:ing)?)\b")),
    ("fit/stability", _rx(r"\b(?:fit|fits|fitting|fall(?:s|ing)?\s+off|stay\s+on|secure)\b")),
    ("comfort", _rx(r"\b(?:comfort|comfortable|uncomfortable|hurt(?:s|ing)?|painful)\b")),
    ("volume", _rx(r"\b(?:volume|loud(?:er|ness)?|quiet(?:er)?)\b")),
    ("sound quality", _rx(r"\b(?:sound|audio)\s+quality\b|\b(?:clear|poor|great|bad)\s+sound\b")),
    ("bass", _rx(r"\bbass\b")),
    ("battery", _rx(r"\bbattery\b|\bbattery\s+life\b")),
    ("charging", _rx(r"\b(?:charge|charges|charged|charging)\b")),
    ("water/rain", _rx(r"\b(?:waterproof|water\s+resistant|rain|rainy|underwater)\b")),
    ("colour/design", _rx(r"\b(?:colou?r|olive\s+green|design|style)\b")),
    ("availability/launch", _rx(r"\b(?:available|availability|launch|release|coming\s+to|comes?\s+to|updates?)\b")),
    ("durability", _rx(r"\b(?:durability|durable|crack(?:ed|ing|s)?|brok(?:e|en)|build\s+quality|plastic|longevity)\b")),
    ("price", _rx(r"\b(?:price|cost|expensive|worth|investment|\$\s*\d+|\d+\s*(?:usd|nzd|gbp|eur))\b")),
    ("running", _rx(r"\b(?:run|runs|runner|runners|running)\b")),
    ("office", _rx(r"\b(?:office|at\s+work|workplace|commut(?:e|ing))\b")),
    ("swimming", _rx(r"\b(?:swim|swims|swimming|underwater|pool)\b")),
    ("gaming", _rx(r"\b(?:gaming|gamer|gameplay)\b")),
)

CONFLICT_PAIRS = {
    frozenset({"Product Praise", "Product Concern"}),
    frozenset({"Purchase Intent", "Product Concern"}),
    frozenset({"Product Praise", "After-sales Issue"}),
}


def detect_entities(text: str) -> tuple[tuple[EntityMention, ...], tuple[str, ...], tuple[str, ...]]:
    mentions: list[EntityMention] = []
    occupied: list[tuple[int, int]] = []
    implied_brands: list[str] = []
    for entity in ENTITY_PATTERNS:
        for match in entity.pattern.finditer(text):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            mentions.append(
                EntityMention(
                    canonical=entity.canonical,
                    entity_type=entity.entity_type,
                    matched_text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )
            occupied.append((match.start(), match.end()))
            if entity.implied_brand:
                implied_brands.append(entity.implied_brand)
    mentions.sort(key=lambda item: item.start)
    brands = _unique(
        [mention.canonical for mention in mentions if mention.entity_type == "brand"] + implied_brands
    )
    products = _unique(
        mention.canonical for mention in mentions if mention.entity_type == "product"
    )
    return tuple(mentions), tuple(brands), tuple(products)


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _mask_entities(text: str, mentions: Iterable[EntityMention]) -> str:
    characters = list(text)
    for mention in mentions:
        for index in range(mention.start, mention.end):
            if characters[index] not in "\r\n":
                characters[index] = " "
    return "".join(characters)


def _nearest_entity(
    mentions: tuple[EntityMention, ...],
    start: int,
    end: int,
    *,
    max_distance: int = 70,
) -> str:
    candidates: list[tuple[int, EntityMention]] = []
    for mention in mentions:
        distance = min(abs(start - mention.end), abs(mention.start - end))
        if mention.start <= end and mention.end >= start:
            distance = 0
        if distance <= max_distance:
            candidates.append((distance, mention))
    if not candidates:
        return "comment subject"
    candidates.sort(key=lambda item: (item[0], item[1].start))
    return candidates[0][1].canonical


def _comparison_target(
    mentions: tuple[EntityMention, ...],
    start: int,
    end: int,
) -> str:
    nearby = [
        mention.canonical
        for mention in mentions
        if mention.start <= end + 70 and mention.end >= start - 70
    ]
    unique = _unique(nearby)
    return " ↔ ".join(unique) if unique else "explicit comparison relation"


def _is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 36) : start]
    return bool(
        re.search(
            r"\b(?:not|no|never|hardly|isn['’]?t|wasn['’]?t|don['’]?t|didn['’]?t)\b(?:\W+\w+){0,3}\W*$",
            prefix,
            flags=re.IGNORECASE,
        )
    )


def _is_competitor_target(target: str) -> bool:
    return any(name in target for name in ("AirPods", "Apple", "Bose", "JBL", "Sony", "WH-1000XM6"))


def _collect_category_matches(
    text: str,
    masked_text: str,
    mentions: tuple[EntityMention, ...],
) -> tuple[list[RuleMatch], bool]:
    results: list[RuleMatch] = []
    competitor_after_sales = False

    for rule in CATEGORY_RULES:
        for match in rule.pattern.finditer(masked_text):
            phrase = text[match.start() : match.end()]
            target = _nearest_entity(mentions, match.start(), match.end())
            category = rule.category
            score = rule.score
            rule_id = rule.rule_id
            strength = rule.strength

            if category == "Product Praise" and _is_negated(masked_text, match.start()):
                category = "Product Concern"
                score = max(2.4, score)
                rule_id = f"negated.{rule.rule_id}"
                strength = "strong"

            if category == "Product Concern" and _is_competitor_target(target):
                future_targets = [
                    mention
                    for mention in mentions
                    if mention.canonical == target and mention.start >= match.end()
                ]
                bridge = (
                    text[match.end() : future_targets[0].start]
                    if future_targets
                    else ""
                )
                if future_targets and re.search(
                    r"\b(?:had|used|owned|before|previously)\b",
                    bridge,
                    flags=re.IGNORECASE,
                ):
                    target = "comment subject"
                else:
                    competitor_after_sales = True
                    continue

            if category == "After-sales Issue" and _is_competitor_target(target):
                competitor_after_sales = True
                continue

            results.append(
                RuleMatch(
                    matched_phrase=phrase,
                    matched_span=f"{match.start()}:{match.end()}",
                    rule_id=rule_id,
                    category=category,
                    score=score,
                    target_entity=target,
                    strength=strength,
                )
            )

    for rule in COMPARISON_RULES:
        for match in rule.pattern.finditer(text):
            results.append(
                RuleMatch(
                    matched_phrase=match.group(0),
                    matched_span=f"{match.start()}:{match.end()}",
                    rule_id=rule.rule_id,
                    category=rule.category,
                    score=rule.score,
                    target_entity=_comparison_target(mentions, match.start(), match.end()),
                    strength=rule.strength,
                )
            )

    product_question_matches = [
        match for match in results if match.category == "Product Question"
    ]
    question_mark = text.find("?")
    if product_question_matches and question_mark >= 0:
        question_start = max(
            text.rfind(".", 0, question_mark),
            text.rfind("!", 0, question_mark),
            text.rfind("\n", 0, question_mark),
        ) + 1
        results.append(
            RuleMatch(
                matched_phrase=text[question_start : question_mark + 1].strip(),
                matched_span=f"{question_start}:{question_mark + 1}",
                rule_id="intent.direct_product_question",
                category="Product Question",
                score=5.0,
                target_entity=_nearest_entity(
                    mentions,
                    question_start,
                    question_mark + 1,
                ),
                strength="strong",
            )
        )

    comparison_matches = [
        match for match in results if match.category == "Competitor Comparison"
    ]
    compared_entities = _unique(mention.canonical for mention in mentions)
    if comparison_matches and len(compared_entities) >= 2:
        anchor = comparison_matches[0]
        results.append(
            RuleMatch(
                matched_phrase=anchor.matched_phrase,
                matched_span=anchor.matched_span,
                rule_id="compare.multi_entity_relation",
                category="Competitor Comparison",
                score=1.8,
                target_entity=" ↔ ".join(compared_entities),
                strength="strong",
            )
        )
    return results, competitor_after_sales


def detect_subtopics(text: str, mentions: tuple[EntityMention, ...] | None = None) -> tuple[str, ...]:
    entity_mentions = mentions if mentions is not None else detect_entities(text)[0]
    masked = _mask_entities(text, entity_mentions)
    return tuple(
        name
        for name, pattern in SUBTOPIC_RULES
        if pattern.search(masked)
    )


def detect_lifecycle(
    text: str,
    mentions: tuple[EntityMention, ...] | None = None,
) -> tuple[str, bool]:
    entity_mentions = mentions if mentions is not None else detect_entities(text)[0]
    matches: list[tuple[float, int, str, str]] = []
    subject_ambiguity = False
    for stage, pattern, score in LIFECYCLE_RULES:
        for match in pattern.finditer(text):
            target = _nearest_entity(entity_mentions, match.start(), match.end())
            if stage in {"Returned", "Replacing"} and _is_competitor_target(target):
                subject_ambiguity = True
                continue
            matches.append((score, LIFECYCLE_PRIORITY[stage], stage, target))
    if not matches:
        return "Unknown", subject_ambiguity
    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return matches[0][2], subject_ambiguity


def _category_scores(matches: Iterable[RuleMatch]) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    rule_counts: Counter[str] = Counter()
    for match in matches:
        rule_counts[match.rule_id] += 1
        multiplier = 1.0 if rule_counts[match.rule_id] == 1 else 0.25
        scores[match.category] += match.score * multiplier
    return {category: round(score, 3) for category, score in scores.items()}


def _confidence(
    primary: str,
    ranked: list[tuple[str, float]],
    matches: tuple[RuleMatch, ...],
    *,
    long_multitopic: bool,
    subject_ambiguity: bool,
) -> float:
    if primary == "Other" or not ranked:
        return 0.30
    top_score = ranked[0][1]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = top_score - second_score
    primary_matches = [match for match in matches if match.category == primary]
    strong_count = sum(match.strength == "strong" for match in primary_matches)
    only_one_weak = len(primary_matches) == 1 and primary_matches[0].strength == "weak"

    value = 0.47
    value += min(0.22, top_score * 0.055)
    value += min(0.14, max(0.0, margin) * 0.04)
    value += min(0.10, strong_count * 0.05)
    if second_score >= 1.5:
        value -= 0.08
    if long_multitopic:
        value -= 0.08
    if subject_ambiguity:
        value -= 0.10
    if only_one_weak:
        value = min(value, 0.60)
    if strong_count == 0:
        value = min(value, 0.72)
    return round(max(0.25, min(0.95, value)), 2)


def _review_reasons(
    primary: str,
    ranked: list[tuple[str, float]],
    matches: tuple[RuleMatch, ...],
    confidence: float,
    *,
    long_multitopic: bool,
    subject_ambiguity: bool,
) -> list[str]:
    reasons: list[str] = []
    primary_matches = [match for match in matches if match.category == primary]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = ranked[0][1] - second_score if ranked else 0.0
    secondary = [category for category, score in ranked[1:] if score >= 1.5]

    if primary == "Other":
        reasons.append("No sufficiently strong category evidence.")
    if confidence < 0.65:
        reasons.append("Calibrated confidence is below 0.65.")
    if secondary:
        reasons.append("Multiple substantive categories were detected.")
    if second_score > 0 and margin < 1.0:
        reasons.append("The top two category scores are close.")
    if long_multitopic:
        reasons.append("Long multi-topic comment requires intent review.")
    if subject_ambiguity:
        reasons.append("The matched lifecycle or issue may refer to a competitor.")
    if len(primary_matches) == 1 and primary_matches[0].strength == "weak":
        reasons.append("Only one weak keyword supports the primary category.")
    if any(frozenset({primary, category}) in CONFLICT_PAIRS for category in secondary):
        reasons.append("Primary and secondary categories contain a conflicting signal.")
    return _unique(reasons)


def _evidence(
    primary: str,
    secondary: tuple[str, ...],
    matches: tuple[RuleMatch, ...],
) -> str:
    selected = [
        match
        for match in matches
        if match.category == primary or match.category in secondary
    ]
    selected.sort(
        key=lambda item: (
            item.category != primary,
            -item.score,
            int(item.matched_span.split(":", 1)[0]),
        )
    )
    if not selected:
        return "No deterministic category rule matched."
    parts = [
        f'{match.category}: "{match.matched_phrase}" '
        f"[{match.rule_id}, +{match.score:g}, target={match.target_entity}]"
        for match in selected[:6]
    ]
    return " | ".join(parts)


def classify_text(text: str) -> ClassificationResult:
    mentions, brands, products = detect_entities(text)
    masked_text = _mask_entities(text, mentions)
    collected, competitor_after_sales = _collect_category_matches(
        text,
        masked_text,
        mentions,
    )
    matches = tuple(collected)
    scores = _category_scores(matches)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    primary = ranked[0][0] if ranked else "Other"
    secondary = tuple(
        category
        for category, score in ranked[1:]
        if score >= 1.5
    )

    lifecycle, lifecycle_ambiguity = detect_lifecycle(text, mentions)
    subject_ambiguity = competitor_after_sales or lifecycle_ambiguity
    substantive_categories = [category for category, score in ranked if score >= 1.5]
    long_multitopic = (
        len(text) >= 280 and len(substantive_categories) >= 2
    ) or len(substantive_categories) >= 3
    confidence = _confidence(
        primary,
        ranked,
        matches,
        long_multitopic=long_multitopic,
        subject_ambiguity=subject_ambiguity,
    )
    reasons = _review_reasons(
        primary,
        ranked,
        matches,
        confidence,
        long_multitopic=long_multitopic,
        subject_ambiguity=subject_ambiguity,
    )

    return ClassificationResult(
        primary_category=primary,
        primary_category_cn=CATEGORY_CN[primary],
        secondary_categories=secondary,
        secondary_categories_cn=tuple(CATEGORY_CN[category] for category in secondary),
        subtopics=detect_subtopics(text, mentions),
        lifecycle_stage=lifecycle,
        mentioned_brands=brands,
        mentioned_products=products,
        category_confidence=confidence,
        category_evidence=_evidence(primary, secondary, matches),
        matched_rules=matches,
        manual_review=bool(reasons),
        review_reason=" ".join(reasons),
        category_scores=scores,
    )
