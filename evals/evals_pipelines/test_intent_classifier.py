import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import GEval
from deepeval.metrics import BaseMetric
from langchain_core.messages import HumanMessage

from agents.intent import classify_intent, INTENT_PROMPT
from agents.llm import llm



class IntentAccuracyMetric(BaseMetric):

    def __init__(self):
        self.threshold = 1.0
        self.score = 0.0
        self.reason = ""

    @property
    def __name__(self):
        return "IntentAccuracyMetric"

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        predicted = test_case.actual_output.strip().lower()
        expected = test_case.expected_output.strip().lower()
        if predicted == expected:
            self.score = 1.0
            self.reason = f"Correct: predicted '{predicted}' matches expected '{expected}'"
        else:
            self.score = 0.0
            self.reason = f"Mismatch: predicted '{predicted}', expected '{expected}'"
        return self.score

    def is_successful(self) -> bool:
        return self.score >= self.threshold



intent_correctness = GEval(
    name="Intent Classification Correctness",
    criteria=(
        "The intent classifier must correctly classify the user input into "
        "exactly one of: 'buy', 'inquiry', or 'general'. "
        "'buy' = user wants to purchase/find/shop for products. "
        "'inquiry' = user wants info about a product (price, specs, availability). "
        "'general' = anything not related to products/shopping."
    ),
    evaluation_params=["input", "actual_output", "expected_output"],
    threshold=0.7,
)


def test_intent_classifier_accuracy(intent_dataset):
    for case in intent_dataset:
        # Build minimal state
        state = {
            "user_input": case["input"],
            "messages": [],
            "intent": "",
            "brand": None,
            "budget": None,
            "rating": None,
            "category": None,
            "info_complete": False,
        }

        result = classify_intent(state)
        predicted_intent = result.get("intent", "")

        test_case = LLMTestCase(
            input=case["input"],
            actual_output=predicted_intent,
            expected_output=case["expected_intent"],
            context=[case["context"]],
        )

        accuracy_metric = IntentAccuracyMetric()
        assert_test(test_case, [accuracy_metric])


def test_intent_classifier_geval(intent_dataset):
    for case in intent_dataset:
        state = {
            "user_input": case["input"],
            "messages": [],
            "intent": "",
            "brand": None,
            "budget": None,
            "rating": None,
            "category": None,
            "info_complete": False,
        }

        result = classify_intent(state)
        predicted_intent = result.get("intent", "")

        test_case = LLMTestCase(
            input=case["input"],
            actual_output=predicted_intent,
            expected_output=case["expected_intent"],
            context=[case["context"]],
        )

        assert_test(test_case, [intent_correctness])


def test_checkout_interception():
    
    system_messages = [
        "Confirm successful checkout for order pout_abc123",
        "Payout failed for transaction xyz",
        "Payment failed due to insufficient balance",
    ]

    for msg in system_messages:
        state = {
            "user_input": msg,
            "messages": [],
            "intent": "",
            "brand": None,
            "budget": None,
            "rating": None,
            "category": None,
            "info_complete": False,
        }

        result = classify_intent(state)
        assert result["intent"] == "general", (
            f"System message '{msg}' was classified as '{result['intent']}' instead of 'general'"
        )
