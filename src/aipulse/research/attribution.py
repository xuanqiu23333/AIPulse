"""Conservative, locatable subject-action rules, not general semantic inference.

Unknown subjects, pronouns, comparisons, negation and unsupported syntax leave
gaps. A background reference is not a statement of the target's capabilities.
"""
import re
from aipulse.research.state import CoverageSupport

_FAMILIES = {
    'persistence': r'persist\w*|checkpoint\w*|durab\w*|持久|检查点|保存状态|状态保存',
    'routing': r'conditional\s+(?:edges?|transitions?)|rout\w*|branch\w*|条件边|条件转换|条件路由|分支|路由',
    'tools': r'\btools?\b|工具',
    'workflow': r'workflow\w*|orchestrat\w*|state graph|directed state|工作流|编排|状态图|architecture|架构',
    'state': r'\bstate\b|状态',
}
_ACTION = r'(?:uses?|supports?|provides?|implements?|represents?|manages?|persists?|routes?|executes?|orchestrates?|stores?|saves?|通过|使用|支持|提供|实现|表示|管理|持久化|保存|调用|执行|编排)'
_NEGATIVE = re.compile(r"\b(?:no|not|never|cannot|can't|doesn't|without|lacks?|unlike|compar\w*|rather|only|may|might|could)\b|不支持|不能|无法|并非|不是|仅|比较|对比|相比|可能|尚未|未实现", re.I)

def _families(text):
    return {name for name, pattern in _FAMILIES.items() if re.search(pattern, text, re.I)}

def coverage_supports(content, questions, subjects):
    """Return exact subject-owned clauses only for fully supported capabilities.

    A question spanning multiple capability families requires all of them.
    This deliberately misses complex syntax rather than resolving pronouns or
    guessing ownership. No unnamed-topic fallback marks coverage.
    """
    owned = []
    for clause in re.split(r'(?<=[.!?。！？;；])\s*|\n+|\s+(?:while|whereas|but)\s+|[，,]\s*(?=[A-Z][A-Za-z0-9_-]+\s)|\s+and\s+(?=[A-Z][A-Za-z0-9_-]+\s)|而(?=[A-Z其他])', content):
        clause = clause.strip()
        if not 12 <= len(clause) <= 350 or _NEGATIVE.search(clause):
            continue
        for subject in sorted(subjects):
            match = re.search(r'(?<![A-Za-z0-9_])' + re.escape(subject) + r'(?![A-Za-z0-9_])\s*' + _ACTION + r'(?:\b|(?=[\u4e00-\u9fff]))', clause, re.I)
            if match is None:
                continue
            # Only the target predicate's suffix can supply capabilities; a
            # different framework's preceding clause cannot donate its words.
            quote = clause[match.start():]
            if len(quote) >= 12:
                owned.append((quote, _families(quote)))
            break
    result = []
    for question in questions:
        # Capability descriptions cannot answer evaluative/negative questions.
        # These intents need richer evidence interpretation than this bounded
        # rule can supply; leave the gap instead of treating 'state' as enough.
        if re.search(r'limitations?|drawbacks?|known issues|trade.?offs?|evaluation|benchmark|局限|已知问题|限制|不足|评估|适用范围',
                     question.question + ' ' + question.search_query, re.I):
            continue
        required = _families(question.question + ' ' + question.search_query)
        selected = [(quote, families) for quote, families in owned if families & required][:3]
        available = set().union(*(families for _, families in selected)) if selected else set()
        if required and required <= available:
            result.extend(CoverageSupport(question_id=question.question_id, quote=quote,
                                         capabilities=sorted(families & required)) for quote, families in selected)
    return result
