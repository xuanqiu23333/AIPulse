"""Conservative AI-topic gate for daily briefs, separate from importance scoring."""

import re

from aipulse.news.repository import SummaryInputRecord


_AI_TOPIC = re.compile(
    r"\b(?:AI|A\.I\.|artificial intelligence|generative AI|GenAI|LLM|"
    r"large language models?|foundation models?|machine learning|deep learning|"
    r"neural networks?|transformers?|diffusion models?|GPT(?:-[0-9A-Za-z]+)?|"
    r"ChatGPT|DeepSeek|OpenAI|Hugging Face|LangChain|LangGraph|PyTorch|"
    r"TensorFlow|model context protocol)\b|"
    r"人工智能|生成式人工智能|大语言模型|机器学习|深度学习|神经网络|"
    r"多模态模型|扩散模型|智能体框架",
    re.IGNORECASE,
)


def ai_relevance(record: SummaryInputRecord) -> bool:
    """Require an explicit AI topic or a known AI-specific configured source."""
    if any(
        (item.provider == "arxiv" and item.source_name.casefold() == "cat:cs.ai")
        or (item.provider == "github" and item.source_name.casefold() == "langchain-ai/langgraph")
        for item in record.items
    ):
        return True
    text = " ".join([record.title] + [
        f"{item.title} {item.excerpt or ''}" for item in record.items
    ])
    return _AI_TOPIC.search(text) is not None
