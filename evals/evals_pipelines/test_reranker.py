import asyncio
import json
import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import AnswerRelevancyMetric
from deepeval.metrics import BaseMetric

from agents.buy import reranker



class ConcisenessMetric(BaseMetric):

    def __init__(self):
        self.threshold = 0.5
        self.score = 0.0
        self.reason = ""

    @property
    def __name__(self):
        return "ConcisenessMetric"

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        response = test_case.actual_output.strip()
        # Count sentences (simple heuristic: split by ., !, ?)
        import re
        sentences = [s.strip() for s in re.split(r'[.!?]+', response) if s.strip()]
        count = len(sentences)

        if count <= 3:
            self.score = 1.0
            self.reason = f"Concise: {count} sentence(s)"
        elif count <= 5:
            self.score = 0.5
            self.reason = f"Slightly verbose: {count} sentences"
        else:
            self.score = 0.0
            self.reason = f"Too verbose: {count} sentences (expected ≤3)"

        return self.score

    def is_successful(self) -> bool:
        return self.score >= self.threshold


SAMPLE_PRODUCTS = [
    {
        "title": "Nike Air Max 270",
        "brand": "Nike",
        "categories": ["Shoes", "Running"],
        "final_price": "89.99",
        "rating": 4.5,
    },
    {
        "title": "Adidas Ultraboost 22",
        "brand": "Adidas",
        "categories": ["Shoes", "Running"],
        "final_price": "120.00",
        "rating": 4.7,
    },
    {
        "title": "Puma RS-X",
        "brand": "Puma",
        "categories": ["Shoes", "Casual"],
        "final_price": "75.00",
        "rating": 4.2,
    },
    {
        "title": "New Balance 990v5",
        "brand": "New Balance",
        "categories": ["Shoes", "Running"],
        "final_price": "185.00",
        "rating": 4.8,
    },
]


def test_reranker_conciseness():
    """Test that reranker produces brief, concise recommendations."""
    state = {
        "user_input": "Find me running shoes",
        "messages": [],
        "intent": "buy",
        "brand": None,
        "budget": None,
        "rating": None,
        "category": "shoes",
        "info_complete": True,
        "raw_products": SAMPLE_PRODUCTS,
        "top_products": [],
        "response": "",
    }

    result = asyncio.get_event_loop().run_until_complete(reranker(state))
    response = result.get("response", "")

    test_case = LLMTestCase(
        input="Find me running shoes under budget with good rating",
        actual_output=response,
    )

    metric = ConcisenessMetric()
    assert_test(test_case, [metric])


def test_reranker_returns_top_products():
    """Test that reranker returns at most 3 products."""
    state = {
        "user_input": "Find me running shoes",
        "messages": [],
        "intent": "buy",
        "brand": None,
        "budget": None,
        "rating": None,
        "category": "shoes",
        "info_complete": True,
        "raw_products": SAMPLE_PRODUCTS,
        "top_products": [],
        "response": "",
    }

    result = asyncio.get_event_loop().run_until_complete(reranker(state))
    top_products = result.get("top_products", [])

    assert len(top_products) <= 3, f"Reranker returned {len(top_products)} products (expected ≤3)"
    assert len(top_products) > 0, "Reranker returned no products"


def test_reranker_answer_relevancy():
    """Test answer relevancy of reranker using DeepEval's built-in metric."""
    state = {
        "user_input": "Find me running shoes",
        "messages": [],
        "intent": "buy",
        "brand": None,
        "budget": "100",
        "rating": "4",
        "category": "shoes",
        "info_complete": True,
        "raw_products": SAMPLE_PRODUCTS,
        "top_products": [],
        "response": "",
    }

    result = asyncio.get_event_loop().run_until_complete(reranker(state))
    response = result.get("response", "")

    products_context = json.dumps(SAMPLE_PRODUCTS, default=str)

    test_case = LLMTestCase(
        input="Find me running shoes, budget 100, min rating 4",
        actual_output=response,
        retrieval_context=[products_context],
    )

    relevancy = AnswerRelevancyMetric(threshold=0.5)
    assert_test(test_case, [relevancy])
