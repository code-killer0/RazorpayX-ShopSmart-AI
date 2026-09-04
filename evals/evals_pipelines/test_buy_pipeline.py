import asyncio
import json
import time
import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import AnswerRelevancyMetric, HallucinationMetric
from deepeval.metrics import BaseMetric
from langchain_core.messages import HumanMessage

from main import get_graph



class LatencyMetric(BaseMetric):

    def __init__(self, max_seconds: float = 30.0):
        self.threshold = 1.0
        self.score = 0.0
        self.reason = ""
        self.max_seconds = max_seconds

    @property
    def __name__(self):
        return "LatencyMetric"

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        try:
            elapsed = float(test_case.additional_metadata.get("elapsed_seconds", 999))
        except (AttributeError, TypeError, ValueError):
            self.score = 0.0
            self.reason = "No timing data found"
            return self.score

        if elapsed <= self.max_seconds:
            self.score = 1.0
            self.reason = f"Completed in {elapsed:.2f}s (limit: {self.max_seconds}s)"
        else:
            self.score = 0.0
            self.reason = f"Too slow: {elapsed:.2f}s (limit: {self.max_seconds}s)"
        return self.score

    def is_successful(self) -> bool:
        return self.score >= self.threshold



async def _run_pipeline(user_input: str, thread_id: str = "eval_buy_test") -> tuple[dict, float]:
    graph = get_graph()
    input_state = {
        "user_input": user_input,
        "messages": [HumanMessage(content=user_input)],
    }
    config = {"configurable": {"thread_id": thread_id}}

    start = time.time()
    result = await graph.ainvoke(input_state, config=config)
    elapsed = time.time() - start

    return result, elapsed



def test_buy_pipeline_full_flow(pipeline_dataset):

    buy_cases = [c for c in pipeline_dataset if c["pipeline"] == "buy"]
    for case in buy_cases:
        result, elapsed = asyncio.get_event_loop().run_until_complete(
            _run_pipeline(case["input"], thread_id=f"eval_{case['test_name']}")
        )

        response = result.get("response", "")
        top_products = result.get("top_products", [])
        products_context = json.dumps(top_products, default=str) if top_products else "No products"

        test_case = LLMTestCase(
            input=case["input"],
            actual_output=response,
            retrieval_context=[products_context],
            additional_metadata={"elapsed_seconds": elapsed},
        )

        #check
        relevancy = AnswerRelevancyMetric(threshold=0.5)
        assert_test(test_case, [relevancy])


def test_buy_pipeline_latency(pipeline_dataset):
    buy_cases = [c for c in pipeline_dataset if c["pipeline"] == "buy"]

    for case in buy_cases:
        result, elapsed = asyncio.get_event_loop().run_until_complete(
            _run_pipeline(case["input"], thread_id=f"eval_latency_{case['test_name']}")
        )

        response = result.get("response", "")

        test_case = LLMTestCase(
            input=case["input"],
            actual_output=response,
            additional_metadata={"elapsed_seconds": elapsed},
        )

        latency = LatencyMetric(max_seconds=30.0)
        assert_test(test_case, [latency])


def test_buy_pipeline_hallucination(pipeline_dataset):
    buy_cases = [c for c in pipeline_dataset if c.get("expected_has_products")]

    for case in buy_cases:
        result, elapsed = asyncio.get_event_loop().run_until_complete(
            _run_pipeline(case["input"], thread_id=f"eval_halluc_{case['test_name']}")
        )

        response = result.get("response", "")
        top_products = result.get("top_products", [])

        if top_products:
            products_context = [json.dumps(p, default=str) for p in top_products]
        else:
            products_context = ["No products found in database"]

        test_case = LLMTestCase(
            input=case["input"],
            actual_output=response,
            context=products_context,
        )

        hallucination = HallucinationMetric(threshold=0.5)
        assert_test(test_case, [hallucination])


def test_checkout_interception_pipeline(pipeline_dataset):
    checkout_cases = [c for c in pipeline_dataset if "checkout" in c["test_name"]]

    for case in checkout_cases:
        result, _ = asyncio.get_event_loop().run_until_complete(
            _run_pipeline(case["input"], thread_id=f"eval_checkout_{case['test_name']}")
        )

        # Should NOT return product recommendations
        top_products = result.get("top_products", [])
        assert len(top_products) == 0, (
            f"Checkout message '{case['input']}' triggered product recommendations"
        )
