import asyncio
import json
import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from langchain_core.messages import HumanMessage

from main import get_graph



async def _run_pipeline(user_input: str, thread_id: str = "eval_inquiry_test") -> dict:
    """Run the full pipeline and return the final state."""
    graph = get_graph()
    input_state = {
        "user_input": user_input,
        "messages": [HumanMessage(content=user_input)],
    }
    config = {"configurable": {"thread_id": thread_id}}
    return await graph.ainvoke(input_state, config=config)


def test_inquiry_pipeline_relevancy(pipeline_dataset):
    """Test answer relevancy for inquiry pipeline flows."""
    inquiry_cases = [c for c in pipeline_dataset if c["pipeline"] == "inquiry"]

    for case in inquiry_cases:
        result = asyncio.get_event_loop().run_until_complete(
            _run_pipeline(case["input"], thread_id=f"eval_inq_{case['test_name']}")
        )

        response = result.get("response", "")

        test_case = LLMTestCase(
            input=case["input"],
            actual_output=response,
            retrieval_context=[case.get("description", "Product inquiry")],
        )

        relevancy = AnswerRelevancyMetric(threshold=0.5)
        assert_test(test_case, [relevancy])


def test_inquiry_pipeline_has_response(pipeline_dataset):
 
    inquiry_cases = [c for c in pipeline_dataset if c["pipeline"] == "inquiry"]
    for case in inquiry_cases:
        result = asyncio.get_event_loop().run_until_complete(
            _run_pipeline(case["input"], thread_id=f"eval_inq_resp_{case['test_name']}")
        )

        response = result.get("response", "")
        assert len(response.strip()) > 0, (
            f"Inquiry pipeline returned empty response for: {case['input']}"
        )


def test_general_question_no_products(pipeline_dataset):
    general_cases = [c for c in pipeline_dataset if c["pipeline"] == "general"]

    for case in general_cases:
        result = asyncio.get_event_loop().run_until_complete(
            _run_pipeline(case["input"], thread_id=f"eval_gen_{case['test_name']}")
        )

        top_products = result.get("top_products", [])
        assert len(top_products) == 0, (
            f"General question '{case['input']}' returned {len(top_products)} products"
        )

        response = result.get("response", "")
        assert len(response.strip()) > 0, (
            f"General question returned empty response: {case['input']}"
        )


def test_inquiry_keyword_presence(pipeline_dataset):

    inquiry_cases = [c for c in pipeline_dataset if c["pipeline"] == "inquiry" and c.get("expected_response_keywords")]
    for case in inquiry_cases:
        result = asyncio.get_event_loop().run_until_complete(
            _run_pipeline(case["input"], thread_id=f"eval_inq_kw_{case['test_name']}")
        )

        response = result.get("response", "").lower()
        keywords = case["expected_response_keywords"]

        found = sum(1 for kw in keywords if kw.lower() in response)
        coverage = found / len(keywords) if keywords else 1.0

        assert coverage >= 0.5, (
            f"Keyword coverage too low ({coverage:.0%}) for '{case['input']}'. "
            f"Expected keywords: {keywords}"
        )
