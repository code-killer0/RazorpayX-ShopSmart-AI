import logging
import re
from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser
from agents.state import ChatState
from agents.llm import llm_lite

logger = logging.getLogger(__name__)

INTENT_PROMPT = """You are an expert intent classifier for a product shopping assistant chatbot.

Conversation history:
{history}

User's latest message: {user_input}

Classify the user's latest message into exactly ONE of the following three categories:

1. "buy":
   - The user explicitly wants to buy, purchase, find, shop for, or get recommendations/suggestions for products (e.g. "I want to buy shoes", "find me a laptop under 50000", "suggest high rated headphones").
   - OR the user is providing additional purchase criteria (like brand, budget, category, size, rating) for a product search initiated previously.
   - OR the user says phrases like "shoes under 5000", "budget 5000", "Nike laptop bag", "something with high rating".

2. "inquiry":
   - The user is asking a specific factual question about product details, specs, price, stock, availability, or product comparison (e.g. "What is the price of Swanson capsules?", "Is the Nike Air Max available?", "Compare iPhone 15 and Samsung S24", "What are the specs of Dell XPS 15?", "How much does JBL speaker cost?").
   - The user wants information about a product rather than requesting to start a purchase recommendations flow.

3. "general":
   - Anything NOT related to products, shopping, or buying (e.g. greetings like "Hello", questions about AI, weather, programming, general chitchat).
   - System/transaction notification messages (e.g. "Confirm successful checkout...", "Payout failed...", "Payment failed...").

CRITICAL RULES:
- Respond with ONLY one single lowercased word: buy, inquiry, or general.
- Do NOT include quotes, explanation, or extra punctuation."""


def _fallback_intent_classification(user_input: str, history_str: str) -> str:
    lower = user_input.lower()

    # System messages check
    if any(k in lower for k in ["checkout", "payout", "payment failed", "confirm successful"]):
        return "general"

    # Inquiry keywords
    inquiry_patterns = [
        r'\b(what is the price|how much|what cost|is .* available|specs of|compare|difference between|in stock|does .* have|tell me about)\b'
    ]
    for pat in inquiry_patterns:
        if re.search(pat, lower):
            return "inquiry"

    # Buy keywords
    buy_patterns = [
        r'\b(buy|purchase|shop|find|looking for|recommend|suggest|under \d+|budget|shoes|laptop|phone|headphones|bag|sneakers|watch|shirts|capsules|oil)\b'
    ]
    for pat in buy_patterns:
        if re.search(pat, lower):
            return "buy"

    # Greetings / General
    if lower in ["hi", "hello", "hey", "good morning", "good evening", "how are you", "who are you"]:
        return "general"

    return "general"


def classify_intent(state: ChatState) -> dict:
    user_input = state["user_input"]
    logger.info(f"[NODE: intent_classifier] Processing input: '{user_input}'")

    lower_input = user_input.lower()
    if "confirm successful checkout" in lower_input or "payout failed" in lower_input or "payment failed" in lower_input:
        logger.info("[NODE: intent_classifier] Intercepted checkout/system message -> intent='general'")
        return {"intent": "general"}

    # Build recent conversation context
    history_lines = []
    for msg in state.get("messages", []):
        content = msg.content if hasattr(msg, "content") else str(msg)
        if "payout" in content.lower() or "checkout" in content.lower():
            continue
        role = "User" if isinstance(msg, HumanMessage) else "Assistant"
        history_lines.append(f"{role}: {content}")
    history = "\n".join(history_lines[-8:])

    try:
        chain = llm_lite | StrOutputParser()
        raw_response = chain.invoke(
            [HumanMessage(content=INTENT_PROMPT.format(history=history, user_input=user_input))]
        ).strip().lower()
        logger.info(f"[NODE: intent_classifier] Raw LLM response: '{raw_response}'")

        if "buy" in raw_response:
            intent = "buy"
        elif "inquiry" in raw_response or "enquiry" in raw_response:
            intent = "inquiry"
        else:
            intent = "general"
    except Exception as e:
        logger.warning(f"[NODE: intent_classifier] LLM call failed ({e}). Using deterministic fallback.")
        intent = _fallback_intent_classification(user_input, history)

    logger.info(f"[NODE: intent_classifier] Final Resulting Intent: '{intent}'")

    return {"intent": intent}