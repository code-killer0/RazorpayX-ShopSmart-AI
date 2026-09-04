import asyncio
import json
import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.metrics import BaseMetric

from agents.inquery import inquiry_resolver


class KeywordCoverageMetric(BaseMetric):

    def __init__(self, expected_keywords: list[str]):
        self.threshold = 0.5
        self.score = 0.0
        self.reason = ""
        self.expected_keywords = expected_keywords

    @property
    def __name__(self):
        return "KeywordCoverageMetric"

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        response_lower = test_case.actual_output.lower()
        hits = 0
        misses = []

        for kw in self.expected_keywords:
            if kw.lower() in response_lower:
                hits += 1
            else:
                misses.append(kw)

        total = len(self.expected_keywords)
        self.score = hits / total if total > 0 else 1.0
        if misses:
            self.reason = f"Missing keywords: {', '.join(misses)}"
        else:
            self.reason = "All expected keywords found"
        return self.score

    def is_successful(self) -> bool:
        return self.score >= self.threshold


answer_relevancy = AnswerRelevancyMetric(threshold=0.5)

def test_inquiry_keyword_coverage(inquiry_dataset):
    """Test that inquiry_resolver responses contain expected keywords."""
    for case in inquiry_dataset:
        state = {
            "user_input": case["input"],
            "messages": [],
            "intent": "inquiry",
            "brand": None,
            "budget": None,
            "rating": None,
            "category": None,
            "info_complete": False,
            "additional_preferences": None,
            "raw_products": [],
            "top_products": [],
            "response": "",
        }

        result = asyncio.get_event_loop().run_until_complete(inquiry_resolver(state))
        response = result.get("response", "")

        test_case = LLMTestCase(
            input=case["input"],
            actual_output=response,
            retrieval_context=[case["context"]],
        )

        metric = KeywordCoverageMetric(case["expected_answer_keywords"])
        assert_test(test_case, [metric])


def test_inquiry_answer_relevancy(inquiry_dataset):
    """Test answer relevancy of inquiry_resolver using DeepEval's built-in metric."""
    for case in inquiry_dataset:
        state = {
            "user_input": case["input"],
            "messages": [],
            "intent": "inquiry",
            "brand": None,
            "budget": None,
            "rating": None,
            "category": None,
            "info_complete": False,
            "additional_preferences": None,
            "raw_products": [],
            "top_products": [],
            "response": "",
        }

        result = asyncio.get_event_loop().run_until_complete(inquiry_resolver(state))
        response = result.get("response", "")

        test_case = LLMTestCase(
            input=case["input"],
            actual_output=response,
            retrieval_context=[case["context"]],
        )

        assert_test(test_case, [answer_relevancy])
