import logging

logger = logging.getLogger(__name__)

from langchain_core.messages import AIMessage
from agents.state import ChatState

REJECTION_MESSAGE = (
    "I appreciate your message! However, I'm a product shopping assistant — "
    "I can help you find, compare, or purchase products from our catalog. \n\n"
    "Here's what I can do for you:\n"
    "• **Find products** — Tell me what you're looking for and I'll search our catalog\n"
    "• **Compare products** — Ask me about specs, prices, or availability\n"
    "• **Get recommendations** — Share your budget and preferences for personalised picks\n\n"
    "What product can I help you with today?"
)


async def polite_reject(state: ChatState, config = None) -> dict:
    """Return a friendly message redirecting the user or acknowledging order/payout updates."""
    user_input = state.get("user_input", "")
    logger.info(f"[NODE: polite_reject] Returning response for user_input: '{user_input}'")

    lower_input = user_input.lower()
    if any(k in lower_input for k in ["confirm successful checkout", "payout", "checkout"]):
        if "fail" in lower_input:
            msg = "I noticed the transaction encountered an issue. Please feel free to retry the payment or let me know if you need assistance with other products! 🛍️"
        else:
            msg = "Thank you! Your order has been placed and processed via Razorpay. 🎉 Is there anything else I can help you find today?"
    else:
        msg = REJECTION_MESSAGE

    if config and "configurable" in config:
        queue = config["configurable"].get("stream_queue")
        if queue:
            await queue.put(msg)

    return {
        "response": msg,
        "messages": [AIMessage(content=msg)],
    }
