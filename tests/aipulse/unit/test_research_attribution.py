"""Conservative subject ownership checks; synthetic examples, no network."""
import pytest
from aipulse.research.evidence import evaluate_candidates, evidence_gaps, build_report_packet
from aipulse.research.state import EvidenceCandidate, ResearchQuestion, ResearchRequest, ResearchState

TOPIC = 'LangGraph 在 AI Agent 状态管理与多步骤研究工作流中的应用'
QUESTIONS = [
    ResearchQuestion(question_id='Q1', question='LangGraph 的状态持久化', search_query='LangGraph state persistence'),
    ResearchQuestion(question_id='Q2', question='LangGraph 的条件路由', search_query='LangGraph conditional routing'),
    ResearchQuestion(question_id='Q3', question='LangGraph 的工具调用与工作流编排', search_query='LangGraph tool calling workflow orchestration'),
]

def evaluate(body, questions=QUESTIONS):
    item = EvidenceCandidate(source_type='arxiv', source_id='offline', source_url='https://example.org/offline',
        title='LangGraph comparison and technical background', content=body, coverage='abstract', quality_note='Synthetic fixture')
    return evaluate_candidates([item], [], questions, 'LangGraph workflow', topic=TOPIC)

@pytest.mark.parametrize('body,expected', [
    ('LangGraph persists state through checkpoints. LangGraph uses conditional edges for routing. LangGraph executes tools within a workflow.', ['Q1','Q2','Q3']),
    ('LangGraph 通过检查点保存状态。LangGraph 使用条件边进行路由。LangGraph 调用工具来编排工作流。', ['Q1','Q2','Q3']),
    ('LangGraph is mentioned for comparison. OtherHarness handles tool execution and research orchestration.', []),
    ('LangGraph 仅作为比较名称。OtherHarness 实现工具调用和研究流程编排。', []),
    ('LangGraph persists state through checkpoints; OtherHarness uses conditional routing and tool calling workflow orchestration.', ['Q1']),
    ('LangGraph 使用检查点持久化状态；OtherHarness 通过条件边实现工具调用与工作流编排。', ['Q1']),
    ('LangGraph is an orchestration framework. It persists state and uses conditional routing with tools.', []),
    ('LangGraph 是一个框架。该框架支持状态持久化和条件路由，调用工具编排工作流。', []),
    ('Unlike LangGraph, OtherHarness supports state persistence, conditional routing and tool workflow orchestration.', []),
    ('LangGraph does not support state persistence or conditional routing or tool workflow orchestration.', []),
    ('LangGraph 不支持状态持久化、条件路由、工具调用和工作流编排。', []),
    ('LangGraph and OtherHarness support state persistence, conditional routing and tool workflow orchestration.', []),
    ('LangGraph persists state through checkpoints and OtherHarness handles tool calling workflow orchestration.', ['Q1']),
    ('LangGraph 使用检查点持久化状态，OtherHarness 负责条件路由、工具调用和工作流编排。', ['Q1']),
    ('LangGraph uses no state persistence, conditional routing or tool workflow orchestration.', []),
])
def test_explicit_ownership_is_required_and_locatable(body, expected):
    rows=evaluate(body)
    assert len(rows)==1  # relevant comparisons stay available as background
    assert rows[0].question_ids==expected
    assert {s.question_id for s in rows[0].coverage_supports}==set(expected)
    assert all(s.quote in body for s in rows[0].coverage_supports)
    packet=build_report_packet(ResearchState(request=ResearchRequest(topic=TOPIC),questions=QUESTIONS,evidence=rows))
    assert packet['evidence'][0]['coverage_supports']==[s.model_dump() for s in rows[0].coverage_supports]
    assert evidence_gaps(QUESTIONS, rows)==[q.question_id for q in QUESTIONS if q.question_id not in expected]

def test_no_named_subject_means_no_coverage():
    rows=evaluate('Agent tools support state persistence, conditional routing and research workflow orchestration.')
    assert not rows or not rows[0].question_ids

def test_original_blocking_counterexample_keeps_gap():
    q=ResearchQuestion(question_id='Q1',question='LangGraph 如何实现工具调用和多步骤研究流程编排？',search_query='LangGraph tool calling multi-step research workflow orchestration')
    rows=evaluate('LangGraph is mentioned only as a comparison label, and this excerpt does not describe its implementation. Tool calling and multi-step research workflow orchestration described here are features of OtherHarness, not LangGraph.', [q])
    assert len(rows)==1 and rows[0].question_ids==[]
    assert evidence_gaps([q],rows)==['Q1']

@pytest.mark.parametrize('question', ['LangGraph 状态管理有哪些适用局限或已知问题？', 'What are known limitations of LangGraph state persistence?'])
def test_capability_description_does_not_answer_limitations(question):
    q=ResearchQuestion(question_id='Q1',question=question,search_query='LangGraph state persistence limitations')
    rows=evaluate('LangGraph persists state through checkpoints and uses conditional routing for workflow orchestration.',[q])
    assert len(rows)==1 and rows[0].question_ids==[]
    assert evidence_gaps([q],rows)==['Q1']
