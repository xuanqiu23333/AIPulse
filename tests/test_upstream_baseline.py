"""Offline smoke tests for the imported upstream research graph."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ollama_deep_researcher import graph as graph_module
from ollama_deep_researcher.configuration import Configuration
from ollama_deep_researcher.state import SummaryStateInput, SummaryStateOutput


class UpstreamBaselineTests(unittest.TestCase):
    def test_graph_entry_state_and_configuration_load(self):
        self.assertIn("generate_query", graph_module.graph.nodes)
        self.assertIn("finalize_summary", graph_module.graph.nodes)
        self.assertEqual(SummaryStateInput.__name__, "SummaryStateInput")
        self.assertEqual(SummaryStateOutput.__name__, "SummaryStateOutput")
        self.assertEqual(Configuration().llm_provider, "ollama")

    def test_graph_completes_one_offline_research_cycle(self):
        query_model = Mock()
        query_model.invoke.side_effect = [
            SimpleNamespace(content='{"query": "AI research update"}'),
            SimpleNamespace(content='{"follow_up_query": "AI follow-up"}'),
        ]
        summary_model = Mock()
        summary_model.invoke.return_value = SimpleNamespace(
            content="A concise summary grounded in the mocked source."
        )
        search_result = {
            "results": [
                {
                    "title": "Example AI Release",
                    "url": "https://example.org/ai-release",
                    "content": "A test-only release note.",
                    "raw_content": None,
                }
            ]
        }

        with (
            patch.object(graph_module, "get_llm", return_value=query_model),
            patch.object(graph_module, "ChatOllama", return_value=summary_model),
            patch.object(
                graph_module, "duckduckgo_search", return_value=search_result
            ) as search_mock,
        ):
            result = graph_module.graph.invoke(
                {"research_topic": "AI research"},
                config={
                    "configurable": {
                        "search_api": "duckduckgo",
                        "max_web_research_loops": 0,
                        "fetch_full_page": False,
                    }
                },
            )

        self.assertIn("## Summary", result["running_summary"])
        self.assertIn("A concise summary", result["running_summary"])
        self.assertIn("https://example.org/ai-release", result["running_summary"])
        search_mock.assert_called_once_with(
            "AI research update", max_results=3, fetch_full_page=False
        )
        self.assertEqual(query_model.invoke.call_count, 2)
        summary_model.invoke.assert_called_once()


if __name__ == "__main__":
    unittest.main()
