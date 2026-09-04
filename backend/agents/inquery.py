import os
import sys
import json
import re
import logging

logger = logging.getLogger(__name__)

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from agents.state import ChatState
from agents.llm import llm_lite, llm_pro, get_text_content
from agents.buy import get_schema_sample, _parse_price, _parse_rating
from agents.utils import parse_json_from_llm as _parse_json_from_llm, normalize_filter as _normalize_filter

# ── MongoDB connector import ──
_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "mcp", "database")
sys.path.insert(0, _DB_PATH)
from mongodb_connector import MongoDBMCPConnector 

INQUIRY_QUERY_PROMPT = """\
You are a MongoDB query expert. The user has a specific product question or inquiry.
Generate a MongoDB find() filter to search the product catalog and answer their question accurately.

Real MongoDB Collection Sample Document Schema:
{schema_sample}

User Query & Criteria:
- User question: {user_input}
- Brand filter (if specified): {brand}
- Max Budget (if specified): {budget}
- Min Rating (if specified): {rating}

Generate ONLY a valid JSON MongoDB filter document.
Rules:
1. Search "categories" or "title" using case-insensitive $regex matching key product terms from user question.
2. Search "brand" using case-insensitive $regex if a brand is specified (and not "any" or "null").
3. Do NOT include price or rating in this MongoDB query filter (these are handled in Python).

Example output:
{{"categories": {{"$regex": "swanson", "$options": "i"}}}}

Respond with ONLY the JSON filter — no explanation, no markdown."""

ANSWER_PROMPT = """\
You are a helpful, expert product shopping assistant. The user asked a question about our catalog products:

User's question: {user_input}

Products found in catalog database:
{products}

Instructions:
1. Provide a direct, clear, and informative answer to the user's question using the product details above.
2. Mention specific key details like title, brand, price, rating, or availability when relevant.
3. Be friendly, concise, and helpful."""






def _fallback_product_classification(user_input: str) -> bool:
    """Deterministic check if query relates to shopping/products."""
    lower = user_input.lower()
    keywords = [
        "price", "cost", "swanson", "nike", "adidas", "samsung", "apple", "dell",
        "laptop", "phone", "shoe", "shoes", "specs", "stock", "available", "capsule",
        "headphone", "bag", "brand", "product", "rating", "compare", "how much",
    ]
    return any(k in lower for k in keywords)


async def inquiry_resolver(state: ChatState, config = None) -> dict:
    """
    Handle product information queries and general chat:
    - If product-related: generate MongoDB query, execute via MCP, format answer.
    - If general chat: reply directly as a helpful assistant.
    """
    user_input = state["user_input"]
    logger.info(f"[NODE: inquiry_resolver] Processing user_input: '{user_input}'")

    queue = None
    if config and "configurable" in config:
        queue = config["configurable"].get("stream_queue")

    # Fast check: system/checkout messages
    lower_input = user_input.lower()
    if "confirm successful checkout" in lower_input or "payout failed" in lower_input or "payment failed" in lower_input:
        answer = "Thank you for the update! Your transaction state has been recorded. Let me know if you need help with anything else!"
        logger.info("[NODE: inquiry_resolver] System message intercepted -> return confirmation")
        if queue:
            await queue.put(answer)
        return {
            "response": answer,
            "messages": [AIMessage(content=answer)],
            "top_products": []
        }

    # Step 1: Fast deterministic product classification (no LLM needed — intent already classified)
    # The intent classifier already routed this query as "inquiry", so we skip the redundant LLM call.
    is_product_related = _fallback_product_classification(user_input)
    logger.info(f"[NODE: inquiry_resolver] Fast keyword classification: is_product_related={is_product_related}")

    if not is_product_related:
        # Fallback: treat as product inquiry anyway since intent classifier already validated this
        is_product_related = True
        logger.info("[NODE: inquiry_resolver] Keyword check returned False but intent='inquiry' — proceeding as product query")

    # Step 2: Product inquiry path — generate search query with schema sample
    schema_sample = await get_schema_sample()
    brand_req = state.get("brand") or "any"
    budget_req = state.get("budget")
    rating_req = state.get("rating")

    mongo_filter = None
    try:
        query_prompt = INQUIRY_QUERY_PROMPT.format(
            schema_sample=schema_sample,
            user_input=user_input,
            brand=brand_req,
            budget=budget_req or "no limit",
            rating=rating_req or "no minimum",
        )
        query_chain = llm_lite | StrOutputParser()
        raw_filter = query_chain.invoke([HumanMessage(content=query_prompt)])
        mongo_filter = _parse_json_from_llm(raw_filter)
    except Exception as e:
        logger.warning(f"[NODE: inquiry_resolver] Filter generation LLM failed ({e})")

    if mongo_filter is None:
        mongo_filter = {
            "$or": [
                {"categories": {"$regex": user_input, "$options": "i"}},
                {"title": {"$regex": user_input, "$options": "i"}},
                {"description": {"$regex": user_input, "$options": "i"}},
            ]
        }
    else:
        mongo_filter = _normalize_filter(mongo_filter)

    logger.info(f"[NODE: inquiry_resolver] Generated MongoDB filter: {mongo_filter}")

    # Step 3: Execute against MongoDB & apply secondary filtering in Python
    products_text = "No products found."
    raw_results = []

    # Currency normalization for budget
    norm_budget = None
    if budget_req is not None:
        try:
            norm_budget = float(budget_req)
            if norm_budget > 500:
                norm_budget = norm_budget / 83.0
        except (ValueError, TypeError):
            norm_budget = None

    norm_rating = None
    if rating_req is not None:
        try:
            norm_rating = float(rating_req)
        except (ValueError, TypeError):
            norm_rating = None

    try:
        async with MongoDBMCPConnector() as conn:
            results = await conn.read(
                collection_name="Products",
                filter_query=mongo_filter,
                limit=30,
            )
            # Fallback 1: Broad regex search on user_input keywords if strict query returned 0 docs
            if not results and mongo_filter:
                logger.info("[NODE: inquiry_resolver] Strict filter returned 0 docs — retrying with user_input regex")
                broad_filter = {"$or": [
                    {"categories": {"$regex": user_input, "$options": "i"}},
                    {"title": {"$regex": user_input, "$options": "i"}},
                    {"description": {"$regex": user_input, "$options": "i"}},
                ]}
                results = await conn.read(
                    collection_name="Products",
                    filter_query=broad_filter,
                    limit=30,
                )
            # Fallback 2: General catalog fallback
            if not results:
                logger.info("[NODE: inquiry_resolver] Broad search returned 0 docs — fetching catalog fallback")
                results = await conn.read(
                    collection_name="Products",
                    filter_query={},
                    limit=15,
                )

            if isinstance(results, list) and results:
                filtered = []
                for p in results:
                    if norm_budget is not None:
                        p_price = _parse_price(p.get("final_price") or p.get("price"))
                        if p_price is not None:
                            comp_price = p_price / 83.0 if p_price > 500 else p_price
                            if comp_price > norm_budget:
                                continue
                    if norm_rating is not None:
                        p_rating = _parse_rating(p.get("rating"))
                        if p_rating is not None and p_rating < norm_rating:
                            continue
                    filtered.append(p)

                raw_results = filtered
                logger.info(f"[NODE: inquiry_resolver] Fetched {len(results)} raw, {len(filtered)} filtered products")

                slim_list = []
                for p in filtered[:5]:
                    img = p.get("image") or p.get("images") or p.get("img_url") or p.get("image_url")
                    if isinstance(img, list) and img:
                        img = img[0]
                    p["image"] = img or ""
                    p["image_url"] = img or ""
                    slim_list.append({
                        "title": p.get("title"),
                        "brand": p.get("brand"),
                        "price": p.get("final_price") or p.get("price"),
                        "rating": p.get("rating"),
                        "image": p.get("image"),
                        "description": str(p.get("description", ""))[:150]
                    })

                if slim_list:
                    products_text = ""
                    for i, sp in enumerate(slim_list, 1):
                        products_text += f"\n{i}. {json.dumps(sp, default=str)}"
    except Exception as exc:
        logger.error(f"[NODE: inquiry_resolver] Database query exception: {exc}")
        products_text = f"(Database query note: {exc})"

    # Step 4: Formulate final answer using LLM with deterministic fallback
    answer_prompt = ANSWER_PROMPT.format(
        user_input=user_input,
        products=products_text,
    )
    answer = ""
    try:
        if queue:
            async for chunk in llm_pro.astream([HumanMessage(content=answer_prompt)]):
                token = get_text_content(chunk.content)
                answer += token
                await queue.put(token)
        else:
            answer_response = llm_pro.invoke([HumanMessage(content=answer_prompt)])
            answer = get_text_content(answer_response.content).strip()
    except Exception as exc:
        logger.warning(f"[NODE: inquiry_resolver] Answer generation LLM failed ({exc}). Formatting fallback answer.")
        if raw_results:
            details = []
            for p in raw_results[:3]:
                title = p.get("title", "Product")
                price = p.get("final_price") or p.get("price") or "N/A"
                rating = p.get("rating") or "N/A"
                details.append(f"• **{title}** — Price: ${price} | Rating: {rating}")
            answer = "Here are the product details matching your query:\n\n" + "\n".join(details)
        else:
            answer = "I searched our product catalog for your query. Please let me know if you need specific product recommendations!"

        if queue and not answer:
            await queue.put(answer)

    logger.info(f"[NODE: inquiry_resolver] Completed inquiry response (length={len(answer)})")
    return {
        "response": answer,
        "messages": [AIMessage(content=answer)],
        "top_products": raw_results[:3],
    }