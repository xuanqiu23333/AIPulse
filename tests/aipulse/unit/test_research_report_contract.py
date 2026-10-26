"""Fixed synthetic JSON only: schema stages, redaction and safe empty reports."""
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel, ValidationError, field_validator

from aipulse.llm.config import DeepSeekSettings
from aipulse.llm.gateway import DeepSeekGateway, GenerationRequest, LLMError, TokenUsage
from aipulse.research.graph import ModelBudget, ResearchWorkflow
from aipulse.research.state import ReportDraft, ResearchReport, ResearchRequest, ResearchState

SECRET='private-key-and-external-text-never-log'

def valid():
    return dict(overview=[],conclusions=[dict(text_zh='该来源描述明确的状态工作流机制。',supports=[dict(citation='E001',quote='LangGraph uses explicit state workflows.')])],
                analyses=[dict(question_id='Q1',claims=[])],trends=[],limitations=[])

async def generate(payload, schema=ReportDraft, example=None, bodies=None):
    def respond(request):
        if bodies is not None: bodies.append(json.loads(request.content))
        return httpx.Response(200,json=dict(id='offline',object='chat.completion',created=1,model='deepseek-v4-pro',
            choices=[dict(index=0,finish_reason='stop',message=dict(role='assistant',content=json.dumps(payload),reasoning_content=SECRET))],
            usage=dict(prompt_tokens=8,completion_tokens=12,total_tokens=20)))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        async with DeepSeekGateway(DeepSeekSettings(DEEPSEEK_API_KEY=SECRET,_env_file=None),http_client=client,max_retries=0) as gateway:
            return await gateway.generate_json(GenerationRequest(system_prompt='JSON',user_prompt=SECRET,max_tokens=512),schema,
                example=example or ReportDraft(**valid()))

@pytest.mark.asyncio
async def test_single_draft_schema_is_sent_with_all_nested_types():
    bodies=[]
    result=await generate(valid(),bodies=bodies)
    assert isinstance(result.value,ReportDraft)
    prompt=bodies[0]['messages'][0]['content']
    assert 'JSON SCHEMA' in prompt and 'AnalysisDraft' in prompt and 'question_id' in prompt
    assert 'additionalProperties' in prompt and 'minLength' in prompt
    assert bodies[0]['response_format']=={'type':'json_object'}

@pytest.mark.asyncio
@pytest.mark.parametrize('kind,loc,error_type',[
    ('missing',['conclusions'],'missing'),
    ('type',['conclusions'],'list_type'),
    ('nested',['conclusions',0,'supports',0,'quote'],'string_type'),
    ('pattern',['analyses',0,'question_id'],'string_pattern_mismatch'),
    ('extra',['[unknown_field]'],'extra_forbidden'),
])
async def test_provider_diagnostics_are_locatable_without_values(kind,loc,error_type,caplog):
    payload=valid()
    if kind=='missing': payload.pop('conclusions')
    elif kind=='type': payload['conclusions']=SECRET
    elif kind=='nested': payload['conclusions'][0]['supports'][0]['quote']={'private':SECRET}
    elif kind=='pattern': payload['analyses'][0]['question_id']=SECRET
    else: payload[SECRET]=SECRET
    with pytest.raises(LLMError) as caught: await generate(payload)
    error=caught.value
    assert error.code=='schema_mismatch' and error.usage.total_tokens==20
    diagnostic=error.diagnostic.model_dump()
    assert diagnostic['stage']=='provider_output'
    assert any(issue['loc']==loc and issue['type']==error_type for issue in diagnostic['issues'])
    assert all(issue['message'] for issue in diagnostic['issues'])
    assert SECRET not in json.dumps(diagnostic)+caplog.text+repr(vars(error))
    assert 'schema_validation_failed' in caplog.text

@pytest.mark.asyncio
async def test_invalid_enum_is_not_echoed(caplog):
    with pytest.raises(LLMError) as caught:
        await generate(dict(topic='LangGraph',status=SECRET),ResearchReport,ResearchReport(topic='LangGraph',status='partial'))
    assert caught.value.diagnostic.issues[0].type=='literal_error'
    assert SECRET not in caplog.text+caught.value.diagnostic.model_dump_json()

def test_only_empty_optional_sections_default_and_core_remains_required():
    draft=ReportDraft.model_validate({'conclusions':[]},strict=True)
    assert draft.overview==draft.analyses==draft.trends==draft.limitations==[]
    with pytest.raises(ValidationError): ReportDraft.model_validate({})
    with pytest.raises(ValidationError): ReportDraft.model_validate({'conclusions':None})
    with pytest.raises(ValidationError): ReportDraft.model_validate({'conclusions':[],'analyses':None})

@pytest.mark.asyncio
async def test_backend_revalidation_failure_is_distinct_and_counted_once(caplog):
    class BrokenGateway:
        async def generate_json(self,*args,**kwargs):
            return SimpleNamespace(value=ReportDraft.model_construct(**{**valid(),'conclusions':SECRET}),
                usage=TokenUsage(prompt_tokens=8,completion_tokens=12,total_tokens=20),model='offline')
    budget=ModelBudget(BrokenGateway())
    with pytest.raises(LLMError) as caught:
        await budget.generate(GenerationRequest(system_prompt='JSON',user_prompt='offline'),ReportDraft,ReportDraft(**valid()))
    assert caught.value.code=='schema_mismatch'
    assert caught.value.diagnostic.stage=='backend_revalidation'
    assert budget.calls==1 and budget.usage.total_tokens==20 and budget.missing_usage==0
    assert SECRET not in caplog.text+caught.value.diagnostic.model_dump_json()

@pytest.mark.asyncio
async def test_legal_draft_but_bad_report_conversion_is_failed_not_citation_failure(monkeypatch):
    import aipulse.research.graph as graph_module
    def broken(*args,**kwargs):
        ResearchReport.model_validate({'topic':'LangGraph','status':SECRET})
    monkeypatch.setattr(graph_module,'validate_research_report',broken)
    workflow=ResearchWorkflow(None,None)
    result=await workflow.validate_report(ResearchState(request=ResearchRequest(topic='LangGraph'),draft=ReportDraft(**valid()),errors=['github_rate_limited']))
    report=result['report']
    assert report.status=='failed' and report.error_code=='report_schema_mismatch'
    assert report.schema_diagnostics[0].stage=='report_conversion'
    assert report.validation_issues==[] and report.conclusions==[]
    assert report.source_warnings==['github_rate_limited']
    assert SECRET not in report.model_dump_json()

@pytest.mark.asyncio
async def test_provider_failure_diagnostic_survives_graph_and_report():
    class BadGateway:
        async def generate_json(self,*args,**kwargs): return await generate({'conclusions':SECRET})
    from aipulse.research.state import ResearchEvidence
    evidence=ResearchEvidence(source_type='arxiv',source_id='offline',source_url='https://example.org/offline',title='LangGraph',
        content='LangGraph uses explicit state workflows.',coverage='abstract',quality_note='Offline',citation='E001',relevance_note='Offline',question_ids=[])
    state=ResearchState(request=ResearchRequest(topic='LangGraph'),evidence=[evidence])
    workflow=ResearchWorkflow(BadGateway(),None)
    update=await workflow.synthesize_report(state)
    report=(await workflow.validate_report(state.model_copy(update=update)))['report']
    assert report.error_code=='report_schema_mismatch' and report.status=='failed'
    assert report.schema_diagnostics[0].issues[0].loc==['conclusions']
    assert report.validation_issues==[]

@pytest.mark.asyncio
async def test_custom_validator_message_and_context_are_not_logged(caplog):
    class SensitiveModel(BaseModel):
        value:str
        @field_validator('value')
        @classmethod
        def check(cls,value): raise ValueError('unsafe input '+value)
    # Construct the example without running this deliberately unsafe validator.
    with pytest.raises(LLMError) as caught:
        await generate({'value':SECRET},SensitiveModel,SensitiveModel.model_construct(value='schema-only'))
    assert caught.value.diagnostic.issues[0].type=='value_error'
    assert SECRET not in caplog.text+caught.value.diagnostic.model_dump_json()

@pytest.mark.asyncio
async def test_gateway_returning_a_dict_is_a_backend_contract_error():
    class WrongType:
        async def generate_json(self,*args,**kwargs):
            return SimpleNamespace(value=valid(),usage=None,model='offline')
    with pytest.raises(LLMError) as caught:
        await ModelBudget(WrongType()).generate(GenerationRequest(system_prompt='JSON',user_prompt='offline'),ReportDraft,ReportDraft(**valid()))
    assert caught.value.code=='schema_mismatch'
    assert caught.value.diagnostic.stage=='backend_revalidation'
    assert caught.value.diagnostic.issues[0].type=='model_type'

@pytest.mark.asyncio
async def test_diagnostics_bound_count_and_hide_arbitrary_extra_keys(caplog):
    payload=valid(); payload.update({SECRET+str(i):SECRET for i in range(24)})
    with pytest.raises(LLMError) as caught: await generate(payload)
    d=caught.value.diagnostic
    assert len(d.issues)==10 and d.total_errors==24 and d.truncated
    assert all(i.loc==['[unknown_field]'] for i in d.issues)
    assert SECRET not in caplog.text+d.model_dump_json()

@pytest.mark.asyncio
async def test_explicit_empty_core_is_partial_without_fabricated_claims():
    result=await generate({'conclusions':[]})
    report=(await ResearchWorkflow(None,None).validate_report(ResearchState(request=ResearchRequest(topic='LangGraph'),draft=result.value)))['report']
    # No evidence and no output is failed; an empty draft never means completed.
    assert report.status=='failed' and not report.conclusions
    assert report.error_code=='no_usable_evidence'
