import asyncio
import json
import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import GEval
from deepeval.metrics import BaseMetric
from langchain_core.messages import HumanMessage, AIMessage

from agents.buy import info_gatherer




class InfoExtractorAccuracyMetric(BaseMetric):

    def __init__(self):
        self.threshold = 0.6
        self.score = 0.0
        self.reason = ""

    @property
    def __name__(self):
        return "InfoExtractorAccuracyMetric"

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        try:
            actual = json.loads(test_case.actual_output)
            expected = json.loads(test_case.expected_output)
        except (json.JSONDecodeError, TypeError):
            self.score = 0.0
            self.reason = "Failed to parse actual or expected output as JSON"
            return self.score

        fields = ["brand", "budget", "rating", "category", "has_enough_info"]
        matches = 0
        total = len(fields)
        mismatches = []

        for field in fields:
            actual_val = actual.get(field)
            expected_val = expected.get(field)

            if actual_val is None and expected_val is None:
                matches += 1
            elif isinstance(expected_val, (int, float)) and isinstance(actual_val, (int, float)):
                if abs(actual_val - expected_val) < 0.01:
                    matches += 1
                else:
                    mismatches.append(f"{field}: got {actual_val}, expected {expected_val}")
            elif isinstance(expected_val, str) and isinstance(actual_val, str):
                if expected_val.lower() in actual_val.lower() or actual_val.lower() in expected_val.lower():
                    matches += 1
                else:
                    mismatches.append(f"{field}: got '{actual_val}', expected '{expected_val}'")
            elif isinstance(expected_val, bool) and isinstance(actual_val, bool):
                if actual_val == expected_val:
                    matches += 1
                else:
                    mismatches.append(f"{field}: got {actual_val}, expected {expected_val}")
            elif actual_val is None and expected_val is not None:
                mismatches.append(f"{field}: got None, expected {expected_val}")
            elif actual_val == expected_val:
                matches += 1
            else:
                mismatches.append(f"{field}: got {actual_val}, expected {expected_val}")
        self.score = matches / total
        if mismatches:
            self.reason = "Mismatches: " + "; ".join(mismatches)
        else:
            self.reason = "All fields match"
        return self.score

    def is_successful(self) -> bool:
        return self.score >= self.threshold



info_extractor_correctness = GEval(
    name="Info Extractor Correctness",
    criteria=(
        "The info extractor must correctly identify brand, budget, rating, and category "
        "from the user's message and conversation history. "
        "has_enough_info should be true when at least a category/product type is present. "
        "Fields not mentioned should be null."
    ),
    evaluation_params=["input", "actual_output", "expected_output"],
    threshold=0.6,
)


def test_info_extractor_field_accuracy(info_extractor_dataset):
    """Test that info_gatherer correctly extracts structured fields."""
    for case in info_extractor_dataset:
        # Build conversation history messages
        messages = []
        if case.get("history"):
            for line in case["history"].split("\n"):
                if line.startswith("User:"):
                    messages.append(HumanMessage(content=line[5:].strip()))
                elif line.startswith("Assistant:"):
                    messages.append(AIMessage(content=line[10:].strip()))

        state = {
            "user_input": case["input"],
            "messages": messages,
            "intent": "buy",
            "brand": None,
            "budget": None,
            "rating": None,
            "category": None,
            "info_complete": False,
            "additional_preferences": None,
        }

        result = asyncio.get_event_loop().run_until_complete(info_gatherer(state))

        actual = {
            "brand": result.get("brand"),
            "budget": result.get("budget"),
            "rating": result.get("rating"),
            "category": result.get("category"),
            "has_enough_info": result.get("info_complete", False),
        }

        test_case = LLMTestCase(
            input=case["input"],
            actual_output=json.dumps(actual),
            expected_output=json.dumps(case["expected"]),
        )

        metric = InfoExtractorAccuracyMetric()
        assert_test(test_case, [metric])


def test_info_extractor_geval(info_extractor_dataset):
    """Test info extraction quality using GEval."""
    for case in info_extractor_dataset:
        messages = []
        if case.get("history"):
            for line in case["history"].split("\n"):
                if line.startswith("User:"):
                    messages.append(HumanMessage(content=line[5:].strip()))
                elif line.startswith("Assistant:"):
                    messages.append(AIMessage(content=line[10:].strip()))

        state = {
            "user_input": case["input"],
            "messages": messages,
            "intent": "buy",
            "brand": None,
            "budget": None,
            "rating": None,
            "category": None,
            "info_complete": False,
            "additional_preferences": None,
        }

        result = asyncio.get_event_loop().run_until_complete(info_gatherer(state))

        actual = {
            "brand": result.get("brand"),
            "budget": result.get("budget"),
            "rating": result.get("rating"),
            "category": result.get("category"),
            "has_enough_info": result.get("info_complete", False),
        }

        test_case = LLMTestCase(
            input=case["input"],
            actual_output=json.dumps(actual),
            expected_output=json.dumps(case["expected"]),
        )

        assert_test(test_case, [info_extractor_correctness])
