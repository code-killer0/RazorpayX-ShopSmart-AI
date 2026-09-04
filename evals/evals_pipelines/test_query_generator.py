import json
import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import GEval
from deepeval.metrics import BaseMetric

from agents.buy import query_generator


class QueryValidationMetric(BaseMetric):

    def __init__(self, expected_keys: list, expected_operators: dict):
        self.threshold = 0.5
        self.score = 0.0
        self.reason = ""
        self.expected_keys = expected_keys
        self.expected_operators = expected_operators

    @property
    def __name__(self):
        return "QueryValidationMetric"

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        try:
            query = json.loads(test_case.actual_output)
        except (json.JSONDecodeError, TypeError):
            self.score = 0.0
            self.reason = "Output is not valid JSON"
            return self.score

        if not isinstance(query, dict):
            self.score = 0.0
            self.reason = "Output is not a JSON object"
            return self.score

        checks_passed = 0
        total_checks = len(self.expected_keys) + len(self.expected_operators)
        issues = []

        for key in self.expected_keys:
            # Accept both the exact key and common aliases
            aliases = [key]
            if key == "categories":
                aliases.extend(["categories", "category", "title"])
            if key == "brand":
                aliases.extend(["brand", "title"])

            if any(alias in query for alias in aliases):
                checks_passed += 1
            else:
                issues.append(f"Missing key: {key}")

        # Check operators
        for key, operator in self.expected_operators.items():
            found = False
            for alias in [key, "title", "categories", "category"]:
                if alias in query and isinstance(query[alias], dict):
                    if operator in query[alias]:
                        found = True
                        break
            if found:
                checks_passed += 1
            else:
                issues.append(f"Missing operator {operator} for {key}")

        self.score = checks_passed / total_checks if total_checks > 0 else 1.0
        self.reason = "; ".join(issues) if issues else "All checks passed"
        return self.score

    def is_successful(self) -> bool:
        return self.score >= self.threshold



query_correctness = GEval(
    name="Query Generator Correctness",
    criteria=(
        "The query generator must produce a valid MongoDB find() filter document. "
        "It should use $regex with $options 'i' for text matching on categories, "
        "brand, or title fields. It should NOT include price or rating filters. "
        "The output must be valid JSON with no extra text."
    ),
    evaluation_params=["input", "actual_output"],
    threshold=0.6,
)


def test_query_generator_structure(query_generator_dataset):
    """Test that query_generator produces valid MongoDB filters."""
    for case in query_generator_dataset:
        state = {
            "user_input": f"Find {case['category']}",
            "messages": [],
            "intent": "buy",
            "brand": case.get("brand"),
            "budget": case.get("budget"),
            "rating": case.get("rating"),
            "category": case["category"],
            "info_complete": True,
            "additional_preferences": None,
        }

        result = query_generator(state)
        mongo_query = result.get("mongo_query", {})

        test_case = LLMTestCase(
            input=f"Category: {case['category']}, Brand: {case.get('brand', 'any')}",
            actual_output=json.dumps(mongo_query),
        )

        metric = QueryValidationMetric(
            expected_keys=case["expected_keys"],
            expected_operators=case["expected_operators"],
        )
        assert_test(test_case, [metric])


def test_query_generator_json_validity(query_generator_dataset):
    """Test that query_generator always produces valid JSON."""
    for case in query_generator_dataset:
        state = {
            "user_input": f"Find {case['category']}",
            "messages": [],
            "intent": "buy",
            "brand": case.get("brand"),
            "budget": case.get("budget"),
            "rating": case.get("rating"),
            "category": case["category"],
            "info_complete": True,
            "additional_preferences": None,
        }

        result = query_generator(state)
        mongo_query = result.get("mongo_query", {})

        assert isinstance(mongo_query, dict), (
            f"query_generator returned non-dict type: {type(mongo_query)}"
        )

        try:
            json.dumps(mongo_query)
        except (TypeError, ValueError) as e:
            pytest.fail(f"query_generator output not JSON-serializable: {e}")
