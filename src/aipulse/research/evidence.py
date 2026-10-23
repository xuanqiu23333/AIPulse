"""Canonical source identities, bounded relevance and traceable report claims."""

import re
from aipulse.research.attribution import coverage_supports
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aipulse.research.state import (
    MAX_EVIDENCE, AnalysisSection, ClaimDraft, ClaimValidationIssue, EvidenceCandidate, ReportSource,
    ResearchEvidence, ResearchQuestion, ResearchReport, ResearchState, VerifiedClaim,
)


_STOP = {"the", "and", "for", "with", "from", "what", "how", "does", "are", "its", "about", "into", "this", "that", "research"}
_ARXIV_ID = re.compile(r"(?:abs/|pdf/)?((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z-]+)?/\d{7}))(?:v\d+)?(?:\.pdf)?$", re.I)
_QUALITY = {"title_only": 0, "metadata": 1, "excerpt": 2, "abstract": 3}
_GENERIC_SUBJECT = {
    "ai", "agent", "agents", "llm", "llms", "research", "state", "workflow", "workflows",
    "architecture", "persistence", "persistent", "conditional", "routing", "tool", "tools",
    "calling", "multi-step", "management", "application", "applications", "limitations",
    "evaluation", "methods", "systems", "system", "evidence", "orchestration",
}
_SOURCE_WARNING = re.compile(r"^(?:local|arxiv|github|hn|provider|retrieval)_[a-z_]{1,40}$")


def tokens(value: str) -> set[str]:
    words = {word.casefold() for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,40}", value)} - _STOP
    for sequence in re.findall(r"[\u4e00-\u9fff]+", value):
        words.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return words


def _subject_anchors(topic: str) -> set[str]:
    # Named Latin subjects (e.g. LangGraph) must occur in the source. Generic
    # AI/tool vocabulary alone cannot establish a relationship to that subject.
    return {word.casefold() for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,40}", topic)} - _STOP - _GENERIC_SUBJECT


def source_identity(candidate: EvidenceCandidate) -> str:
    value = candidate.identity_url or candidate.source_url
    parts = urlsplit(value)
    host = (parts.hostname or "").casefold()
    if host in {"arxiv.org", "export.arxiv.org"}:
        match = _ARXIV_ID.search(parts.path.lstrip("/"))
        if match:
            return "arxiv:" + match.group(1).casefold()
    path = parts.path.rstrip("/") or "/"
    if host == "github.com":
        segments = path.split("/")
        path = "/".join(segment.casefold() if index < 3 else segment for index, segment in enumerate(segments))
    query = urlencode(sorted(
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in {"ref", "fbclid", "gclid"}
    ))
    return urlunsplit(("https", host, path, query, ""))


def evaluate_candidates(
    candidates: list[EvidenceCandidate], previous: list[ResearchEvidence],
    questions: list[ResearchQuestion], query: str, *, topic: str | None = None,
) -> list[ResearchEvidence]:
    result = list(previous)
    positions = {source_identity(item): index for index, item in enumerate(result)}
    query_terms = tokens(query)
    anchors = _subject_anchors(topic) if topic else set()
    for candidate in candidates:
        text_terms = tokens(candidate.title + " " + candidate.content)
        supports = coverage_supports(candidate.content, questions, anchors)
        matches = list(dict.fromkeys(support.question_id for support in supports))
        if not matches and not query_terms.intersection(text_terms):
            continue
        key = source_identity(candidate)
        position = positions.get(key)
        if position is None and len(result) >= MAX_EVIDENCE:
            continue
        if position is not None:
            old = result[position]
            if (_QUALITY[candidate.coverage], len(candidate.content)) <= (_QUALITY[old.coverage], len(old.content)):
                continue
            citation = old.citation
        else:
            citation = f"E{len(result) + 1:03d}"
        evidence = ResearchEvidence(
            **candidate.model_dump(), citation=citation,
            relevance_note="Related/background material; coverage requires explicit subject-action clauses, not general semantic verification.",
            question_ids=matches, coverage_supports=supports,
        )
        if position is None:
            positions[key] = len(result)
            result.append(evidence)
        else:
            result[position] = evidence
    return result


def evidence_gaps(questions: list[ResearchQuestion], evidence: list[ResearchEvidence]) -> list[str]:
    supported = {
        question_id for item in evidence
        if item.coverage in {"abstract", "excerpt"}
        for support in item.coverage_supports
        for question_id in [support.question_id]
        if support.quote in item.content
    }
    return [question.question_id for question in questions if question.question_id not in supported]


def _normalize(value: str) -> str:
    return " ".join(value.split())


def build_report_packet(state: ResearchState) -> dict:
    """Use the entire bounded saved excerpt, retaining IDs and provenance.

    <=18 records x <=2000 characters; no new retrieval or model call. Quote
    suggestions are contiguous source sentences, not synthesized conclusions.
    """
    usable = [item for item in state.evidence if item.coverage != "title_only" and len(item.content.strip()) >= 12]
    topic_terms = tokens(state.request.topic + " " + (state.request.focus or ""))
    rows = []
    for item in usable:
        sentences = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+|\n+", item.content)]
        suggestions = [part for part in sentences if 12 <= len(part) <= 350 and tokens(part).intersection(topic_terms)]
        rows.append({
            "citation": item.citation, "title": item.title, "text": item.content,
            "coverage": item.coverage, "quality_note": item.quality_note,
            "question_ids": item.question_ids, "quote_candidates": suggestions[:3],
            "coverage_supports": [support.model_dump() for support in item.coverage_supports],
        })
    return {
        "topic": state.request.topic, "focus": state.request.focus,
        "questions": [question.model_dump() for question in state.questions],
        "uncovered_questions": state.gaps,
        "question_evidence": [{"question_id": question.question_id,
                               "citations": [item.citation for item in usable if question.question_id in item.question_ids]}
                              for question in state.questions],
        "evidence": rows,
    }


def claim_rejection_reasons(claim: ClaimDraft, evidence: dict[str, ResearchEvidence]) -> list[str]:
    reasons = []
    for support in claim.supports:
        item = evidence.get(support.citation)
        if item is None:
            reasons.append("unknown_evidence")
        elif item.coverage == "title_only":
            reasons.append("title_only")
        elif _normalize(support.quote) not in _normalize(item.content):
            reasons.append("quote_not_found")
    quoted_numbers = set(re.findall(r"\d+(?:\.\d+)?", " ".join(item.quote for item in claim.supports)))
    if not set(re.findall(r"\d+(?:\.\d+)?", claim.text_zh)) <= quoted_numbers:
        reasons.append("number_not_supported")
    return list(dict.fromkeys(reasons))


def validate_claim(claim: ClaimDraft, evidence: dict[str, ResearchEvidence]) -> VerifiedClaim | None:
    """Drop unsupported citations/quotes/numbers; this is not semantic fact verification."""
    if not claim.supports or claim_rejection_reasons(claim, evidence):
        return None
    return VerifiedClaim(
        text_zh="来源材料称：" + claim.text_zh,
        citations=list(dict.fromkeys(item.citation for item in claim.supports)),
        support_quotes=claim.supports,
    )


def validate_research_report(state: ResearchState, *, models: list[str]) -> ResearchReport:
    evidence = {item.citation: item for item in state.evidence}
    issues = []

    def checked(claims, section, question_id=None, forced_reason=None):
        result = []
        for index, claim in enumerate(claims):
            reasons = [forced_reason] if forced_reason else claim_rejection_reasons(claim, evidence)
            if reasons:
                issues.append(ClaimValidationIssue(section=section,index=index,question_id=question_id,
                                                   text_zh=claim.text_zh,supports=claim.supports,reasons=reasons))
            else:
                validated = validate_claim(claim, evidence)
                if validated is not None:
                    result.append(validated)
        return result

    draft = state.draft
    overview = checked(draft.overview, "overview") if draft else []
    conclusions = checked(draft.conclusions, "conclusions") if draft else []
    trends = checked(draft.trends, "trends") if draft else []
    questions = {question.question_id: question.question for question in state.questions}
    analyses = []
    if draft:
        seen_questions = set()
        for section in draft.analyses:
            if section.question_id not in questions or section.question_id in seen_questions:
                checked(section.claims, "analyses", section.question_id,
                        "unknown_question" if section.question_id not in questions else "duplicate_question")
                continue
            seen_questions.add(section.question_id)
            analyses.append(AnalysisSection(
                question_id=section.question_id, question=questions[section.question_id],
                claims=checked(section.claims, "analyses", section.question_id),
            ))
    present = {section.question_id for section in analyses}
    for question_id, question in questions.items():
        if question_id not in present:
            analyses.append(AnalysisSection(question_id=question_id, question=question, claims=[]))
    claim_count = len(overview) + len(conclusions) + len(trends) + sum(len(section.claims) for section in analyses)
    limitations = list(draft.limitations) if draft else []
    limitations.append("仅检索本地新闻与配置启用的公开 API；未抓取或核验网页全文，预印本、仓库自述及社区讨论不等同于独立事实验证。")
    limitations.append("引文校验只确认编号、原文片段及数字可追溯，不能证明中文转述完全被原文蕴含，具体结论仍需人工核对。")
    supported_questions = {support.question_id
                           for claim in conclusions + [c for section in analyses for c in section.claims]
                           for quote in claim.support_quotes
                           for support in coverage_supports(quote.quote, state.questions, _subject_anchors(state.request.topic))}
    missing_questions = list(dict.fromkeys(state.gaps + [key for key in questions if key not in supported_questions]))
    if missing_questions:
        limitations.append("仍缺少充分原始摘录或有效结论的子问题：" + "；".join(questions.get(key, key) for key in missing_questions))
    if state.errors:
        limitations.append("部分步骤降级或失败：" + ", ".join(sorted(set(state.errors))))
    if issues:
        limitations.append(f"已剔除 {len(issues)} 项未通过引用、原文片段或数字校验的具体表述，逐项原因见 validation_issues。")
    if claim_count == 0:
        limitations.append("没有形成通过证据引用校验的具体研究结论。")
    warnings = report_source_warnings(state)
    blocking_errors = [code for code in state.errors if code not in warnings]
    status = "completed" if conclusions and not missing_questions and not blocking_errors and not issues else "partial"
    if draft is None and any(code.startswith("report_") for code in state.errors):
        status = "failed"
    elif not state.evidence and claim_count == 0:
        status = "failed"
    return ResearchReport(
        topic=state.request.topic, status=status, overview=overview,
        error_code=(next((code for code in state.errors if code.startswith("report_")),
                         "no_usable_evidence" if not state.evidence else "research_failed")
                    if status == "failed" else None),
        conclusions=conclusions, analyses=analyses, trends=trends,
        limitations=limitations,
        sources=report_sources(state),
        models=models, metrics=state.metrics, source_warnings=warnings,
        validation_issues=issues, schema_diagnostics=state.schema_diagnostics,
    )


def report_sources(state: ResearchState) -> list[ReportSource]:
    return [ReportSource(
        citation=item.citation, source_type=item.source_type,
        source_url=item.source_url, title=item.title, coverage=item.coverage,
        quality_note=item.quality_note, excerpt=item.content[:400],
    ) for item in state.evidence]


def report_source_warnings(state: ResearchState) -> list[str]:
    return [code for code in state.errors if _SOURCE_WARNING.fullmatch(code)]
