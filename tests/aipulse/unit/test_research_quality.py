"""Synthetic evidence replay; no historical payloads, network or database."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from aipulse.llm.gateway import TokenUsage
from aipulse.research.evidence import validate_research_report
from aipulse.research.graph import ResearchWorkflow
from aipulse.research.state import (
    EvidenceCandidate, ResearchEvidence, ResearchQuestion, ResearchRequest,
    ResearchState, PlanDraft, ReportDraft, ResearchReport,
)
from aipulse.research.tools import PublicAPISearch, ResearchRetriever, SearchOutcome

FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'research_quality_evidence.json'
REQUEST = ResearchRequest(topic='LangGraph 在 AI Agent 状态管理与多步骤研究工作流中的应用')

def evidence():
    return [ResearchEvidence.model_validate(item) for item in json.loads(FIXTURE.read_text(encoding='utf-8'))['evidence']]

def questions():
    return [ResearchQuestion(question_id=f'Q{i+1}', question=q, search_query=s) for i,(q,s) in enumerate([
        ('LangGraph 状态持久化与条件路由的技术实现', 'LangGraph state persistence conditional routing best'),
        ('LangGraph 多步骤研究中的工具调用与流程编排局限', 'LangGraph tool calling multi-step research workflow'),
        ('LangGraph 状态管理与路由的实际应用案例', 'LangGraph state orchestration use cases examples'),
    ])]

def good_claim():
    text = evidence()[2].content
    start = text.index('LangGraph represents')
    quote = text[start:text.index('mechanisms.', start)+len('mechanisms.')]
    return {'text_zh':'该合成片段描述以有向状态图表示状态更新，并使用条件转换与持久状态。',
            'supports':[{'citation':'E003','quote':quote}]}

def state_with(draft=None, **changes):
    base=dict(request=REQUEST, questions=questions()[:1], evidence=evidence(), draft=draft)
    base.update(changes)
    return ResearchState(**base)

class CaptureLLM:
    def __init__(self): self.request=None
    async def generate_json(self, request, schema, *, example):
        self.request=request
        value=ReportDraft(overview=[],conclusions=[good_claim()],analyses=[],trends=[],limitations=['仅依据摘要，不代表全文核验。'])
        return SimpleNamespace(value=value,model='offline-model',usage=TokenUsage(prompt_tokens=2,completion_tokens=3,total_tokens=5))

def test_saved_fragments_reach_report_with_tail_and_stable_ids():
    llm=CaptureLLM()
    workflow=ResearchWorkflow(llm, None)
    asyncio.run(workflow.synthesize_report(state_with()))
    packet=json.loads(llm.request.user_prompt)
    by_id={row['citation']:row for row in packet['evidence']}
    assert set(by_id)=={item.citation for item in evidence()}
    assert by_id['E003']['text']==evidence()[2].content
    assert 'integrating with LangGraph' in by_id['E005']['text']
    assert len(packet['question_evidence'])==1
    for row in packet['evidence']:
        assert len(row['text'])<=2000
        assert all(quote in row['text'] for quote in row['quote_candidates'])
    assert llm.request.reasoning_effort=='high' and llm.request.max_tokens==6144

def test_unrelated_tool_papers_cannot_cover_a_langgraph_question():
    rows=evidence()
    candidates=[EvidenceCandidate.model_validate({k:v for k,v in row.model_dump().items() if k not in {'citation','question_ids','relevance_note','coverage_supports'}}) for row in rows[5:]]
    workflow=ResearchWorkflow(CaptureLLM(),None)
    result=asyncio.run(workflow.evaluate_evidence(state_with(evidence=[],questions=questions(),candidates=candidates,current_query='LangGraph tool calling multi-step research workflow')))
    assert all(not row.question_ids and not row.coverage_supports for row in result['evidence'])

def test_synthetic_material_keeps_tool_calling_question_as_a_gap():
    candidates=[EvidenceCandidate.model_validate({k:v for k,v in row.model_dump().items() if k not in {'citation','question_ids','relevance_note','coverage_supports'}}) for row in evidence()]
    workflow=ResearchWorkflow(CaptureLLM(),None)
    state=state_with(evidence=[],questions=questions(),candidates=candidates,current_query='LangGraph tool calling multi-step research workflow')
    update=asyncio.run(workflow.evaluate_evidence(state))
    gaps=asyncio.run(workflow.identify_gaps(state.model_copy(update=update)))
    assert 'Q2' in gaps['gaps']
    assert gaps['stop_reason']!='coverage_sufficient'

def test_invalid_candidates_have_reasons_and_do_not_destroy_independent_valid_claim():
    valid=good_claim()
    bad_id={'text_zh':'这是一项没有真实证据编号支持的候选表述。','supports':[{'citation':'E999','quote':valid['supports'][0]['quote']}]}
    bad_quote={'text_zh':'此候选表述引用了不存在的原文片段。','supports':[{'citation':'E003','quote':'This quote does not exist in the supplied abstract.'}]}
    bad_number={'text_zh':'该方法使任务性能提高 999 倍。','supports':valid['supports']}
    draft=ReportDraft(overview=[],conclusions=[valid,bad_id,bad_quote,bad_number],analyses=[],trends=[],limitations=[])
    report=validate_research_report(state_with(draft),models=['offline-model'])
    assert len(report.conclusions)==1 and report.conclusions[0].citations==['E003']
    assert report.status=='partial'
    assert [issue.reasons for issue in report.validation_issues]==[['unknown_evidence'],['quote_not_found'],['number_not_supported']]
    assert [issue.index for issue in report.validation_issues]==[1,2,3]
    assert report.validation_issues[0].text_zh==bad_id['text_zh']

def test_optional_github_warning_does_not_prevent_valid_complete_report():
    draft=ReportDraft(overview=[],conclusions=[good_claim()],analyses=[],trends=[],limitations=[])
    report=validate_research_report(state_with(draft,errors=['github_rate_limited']),models=['offline-model'])
    assert report.status=='completed'
    assert report.source_warnings==['github_rate_limited']
    assert 'github_rate_limited' in '\n'.join(report.limitations)

def test_github_warning_with_no_core_conclusion_remains_partial():
    draft=ReportDraft(overview=[good_claim()],conclusions=[],analyses=[],trends=[],limitations=[])
    report=validate_research_report(state_with(draft,errors=['github_rate_limited']),models=[])
    assert report.status=='partial' and report.overview and not report.conclusions
    assert report.source_warnings==['github_rate_limited']

def test_all_unreliable_claims_remain_partial_with_explicit_rejections():
    claim=good_claim();claim['supports'][0]['citation']='E999'
    draft=ReportDraft(overview=[],conclusions=[claim],analyses=[],trends=[],limitations=[])
    report=validate_research_report(state_with(draft),models=[])
    assert report.status=='partial' and not report.conclusions
    assert report.validation_issues[0].reasons==['unknown_evidence']

def test_empty_result_differs_from_partial_evidence_and_model_failure():
    assert validate_research_report(state_with(evidence=[]),models=[]).status=='failed'
    assert validate_research_report(state_with(),models=[]).status=='partial'
    assert validate_research_report(state_with(errors=['report_output_truncated']),models=[]).status=='failed'

@pytest.mark.parametrize('status,headers',[(403,{'X-RateLimit-Remaining':'0'}),(429,{})])
def test_rate_limited_source_is_not_requested_again_but_other_sources_continue(status,headers):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(status,headers=headers)
    class Other:
        async def search(self,*args): return SearchOutcome(candidates=[EvidenceCandidate.model_validate({k:v for k,v in evidence()[2].model_dump().items() if k not in {'citation','question_ids','relevance_note','coverage_supports'}})])
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            retriever=ResearchRetriever([PublicAPISearch(client,'github'),Other()])
            first=await retriever.search('LangGraph state',REQUEST)
            second=await retriever.search('LangGraph tools',REQUEST)
            assert first.candidates and second.candidates
            assert second.errors==['github_rate_limited']
    asyncio.run(run())
    assert len(calls)==1

def test_permission_denied_403_is_not_mislabelled_as_rate_limit():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403,json={'message':'Resource not accessible by integration'}))) as client:
            return await PublicAPISearch(client,'github').search('LangGraph',REQUEST)
    assert asyncio.run(run()).errors==['github_unavailable']

def test_saved_legacy_failed_and_partial_reports_remain_readable():
    fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
    for payload in fixture['legacy_reports']:
        report=ResearchReport.model_validate(payload)
        assert report.status==payload['status']
        assert report.validation_issues==[] and report.source_warnings==[]
        assert report.prompt_version==payload['prompt_version']

def test_full_graph_replays_synthetic_material_without_inventing_missing_tool_conclusions():
    rows=evidence()
    candidates=[EvidenceCandidate.model_validate({k:v for k,v in row.model_dump().items() if k not in {'citation','question_ids','relevance_note','coverage_supports'}}) for row in rows]
    class ReplayLLM(CaptureLLM):
        def __init__(self): super().__init__(); self.calls=[]
        async def generate_json(self,request,schema,*,example):
            self.calls.append(request)
            if schema is PlanDraft:
                return SimpleNamespace(value=PlanDraft(questions=[{'question':q.question,'search_query':q.search_query} for q in questions()]),model='offline-model',usage=TokenUsage(prompt_tokens=2,completion_tokens=3,total_tokens=5))
            return await super().generate_json(request,schema,example=example)
    class ReplayRetriever:
        async def search(self,*args): return SearchOutcome(candidates=candidates,errors=['github_rate_limited'])
    llm=ReplayLLM();saved=[]
    async def persist(state): saved.append(state)
    state=asyncio.run(ResearchWorkflow(llm,ReplayRetriever(),persist=persist).run(REQUEST))
    assert state.report.status=='partial' and state.report.conclusions
    assert 'Q2' in state.gaps and state.rounds==2 and state.stop_reason=='no_new_query'
    assert len(llm.calls)==2 and [r.max_tokens for r in llm.calls]==[1000,6144]
    assert len(saved)==1 and state.report.source_warnings==['github_rate_limited']
    assert {s.citation for s in state.report.sources}=={e.citation for e in saved[0].evidence}
    assert all(not e.question_ids for e in state.evidence if e.title in {rows[5].title,rows[6].title})

def test_title_only_source_cannot_support_a_technical_claim():
    rows=evidence();rows[2]=rows[2].model_copy(update={'coverage':'title_only'})
    draft=ReportDraft(overview=[],conclusions=[good_claim()],analyses=[],trends=[],limitations=[])
    result=validate_research_report(state_with(draft,evidence=rows),models=[])
    assert not result.conclusions and result.validation_issues[0].reasons==['title_only']
