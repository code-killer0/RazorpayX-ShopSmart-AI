import os
import sys
import json
import re
import asyncio
import logging

logger = logging.getLogger(__name__)

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from agents.state import ChatState
from agents.llm import llm, llm_lite, get_text_content
from agents.utils import parse_json_from_llm as _parse_json_from_llm, normalize_filter as _normalize_filter

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "mcp", "database")
sys.path.insert(0, _DB_PATH)
from mongodb_connector import MongoDBMCPConnector  # noqa: E402


INFO_EXTRACTOR_PROMPT = """\
You are an expert shopping assistant preference extractor. Your goal is to accurately extract user purchase preferences from the conversation.

Conversation history:
{history}

Latest user message: {user_input}

Already known preferences (preserve these unless user explicitly changes or overrides them):
- brand: {existing_brand}
- budget: {existing_budget}
- rating: {existing_rating}
- category: {existing_category}

Extract and return ONLY a valid JSON object matching this exact schema:
{{
    "brand": "<brand name string or null>",
    "budget": <numeric max budget float or null>,
    "rating": <numeric min rating float or null>,
    "category": "<product category or type string or null>"
}}

Rules:
1. Extract numbers for budget (e.g. 5000 for "under 5000", "budget 5000") and rating (e.g. 4.5 for "above 4.5 stars").
2. Extract strings for brand (e.g. "Nike", "Adidas", "Samsung", "Swanson") and category (e.g. "shoes", "laptop bag", "headphones", "capsules", "phone").
3. Retain previously known values if the user hasn't changed them.
4. Respond with ONLY valid JSON — no markdown code blocks, no explanation."""


QUERY_GENERATOR_PROMPT = """\
You are a MongoDB query expert. Your task is to generate a MongoDB find() filter document to search the product catalog based on user criteria.

Real MongoDB Collection Sample Document Schema:
{schema_sample}

User Search Criteria:
- Category: {category}
- Brand: {brand}
- Max Budget: {budget}
- Min Rating: {rating}
- Additional Preferences: {additional}

Generate ONLY a valid JSON MongoDB filter document.
Rules:
1. Search the "categories" array using case-insensitive $regex. Example: {{"categories": {{"$regex": "shoes", "$options": "i"}}}}
2. Search the "brand" field using case-insensitive $regex if a brand is specified (and not "any" or "null").
3. Do NOT include price or rating in this MongoDB query filter (these are handled programmatically in Python).
4. Omit any criteria that is "any", "no limit", "no minimum", or "null".

Example valid output:
{{"categories": {{"$regex": "shoes", "$options": "i"}}, "brand": {{"$regex": "saucony", "$options": "i"}}}}

Respond with ONLY the JSON filter — no explanation, no markdown."""


RERANKER_PROMPT = """\
You are an expert product recommendation engine. The user is shopping and we retrieved candidate products from our catalog database.

User Requirements:
- Category: {category}
- Brand preference: {brand}
- Maximum Budget: {budget}
- Minimum Rating: {rating}

Candidate Products from Database:
{products}

Your task:
1. Analyze ALL candidate products above against the user's requirements.
2. Select the TOP 3 most relevant products. Prioritize: exact category/title match > brand match > best rating > price within budget.
3. Return a JSON object with exactly this structure:
{{
  "selected_indices": [1, 5, 3],
  "intro": "A short, friendly 1-2 sentence introduction for the recommendations."
}}

Rules:
- "selected_indices" must contain exactly 3 integers (1-indexed) from the candidate list above. If fewer than 3 candidates exist, return all available indices.
- "intro" should be warm and brief. Do NOT list product names, specs, or prices — the UI renders product cards separately.
- Respond with ONLY the JSON object. No markdown, no explanation."""




def _merge_preference(new_val, existing_val, null_strings=("null", "None", "", "none")):
    """Return new value if it's valid, otherwise fall back to existing."""
    if new_val is not None and str(new_val).strip() not in null_strings:
        return new_val
    return existing_val


def _extract_preferences_from_text(text: str) -> dict:
    """
    Keyword & regex-based preference extractor — works deterministically without LLM calls.
    Extracts brand, budget, rating, and category from natural language input.
    """
    text_lower = text.lower()
    result = {"brand": None, "budget": None, "rating": None, "category": None}

    brands = [
        "nike", "adidas", "puma", "reebok", "skechers", "new balance", "asics",
        "under armour", "converse", "vans", "fila", "samsung", "apple", "sony",
        "lg", "dell", "hp", "lenovo", "asus", "acer", "oneplus", "xiaomi",
        "boat", "jbl", "bose", "sennheiser", "logitech", "swanson", "philips",
        "panasonic", "whirlpool", "bosch", "dyson", "gucci", "zara", "levis",
        "saucony", "kishigo", "twinsluxes", "accutire", "saura",
    ]
    for b in brands:
        if re.search(r'\b' + re.escape(b) + r'\b', text_lower):
            result["brand"] = b.title()
            break

    categories_map = {
        "running shoe": "shoes", "running shoes": "shoes",
        "shoe": "shoes", "shoes": "shoes", "sneaker": "sneakers", "sneakers": "sneakers",
        "boot": "boots", "boots": "boots", "sandal": "sandals", "sandals": "sandals",
        "laptop bag": "laptop bag", "laptop bags": "laptop bag",
        "laptop": "laptops", "laptops": "laptops", "notebook": "laptops",
        "phone": "phone", "phones": "phone", "mobile": "phone", "smartphone": "phone",
        "headphone": "headphones", "headphones": "headphones",
        "earphone": "earphones", "earphones": "earphones", "earbuds": "earbuds",
        "watch": "watches", "watches": "watches", "smartwatch": "smartwatches",
        "tablet": "tablets", "camera": "cameras", "speaker": "speakers",
        "keyboard": "keyboards", "mouse": "mouse", "monitor": "monitors",
        "tv": "television", "television": "television",
        "shirt": "shirts", "shirts": "shirts", "tshirt": "tshirts", "t-shirt": "tshirts",
        "jeans": "jeans", "jacket": "jackets", "dress": "dresses", "vest": "vests",
        "bag": "bags", "bags": "bags", "backpack": "backpacks",
        "capsule": "capsules", "capsules": "capsules", "supplement": "supplements",
        "hair oil": "hair oil", "oil": "hair oil",
        "cream": "creams", "lotion": "lotions", "perfume": "perfumes",
    }
    
    best_match_idx = float('inf')
    best_category = None
    
    for keyword, cat in categories_map.items():
        m = re.search(r'\b' + re.escape(keyword) + r'\b', text_lower)
        if m:
            start_pos = m.start()
            if start_pos < best_match_idx:
                best_match_idx = start_pos
                best_category = cat

    if not best_category:
        cleaned = re.sub(r'^(?:ok|okay|please|can you|could you|hi|hello|hey|show me|show|find me|find|get me|get|search for|search|suggest|recommend|looking for|i want to buy|i want|i need)\s+', '', text_lower, flags=re.I).strip()
        cleaned = re.sub(r'\s*(?:under|below|within|budget|max|upto|up to|less than|at most|around|about|above|min|minimum)\s*(?:rs\.?|₹|inr|\$)?\s*\d[\d,]*.*$', '', cleaned, flags=re.I).strip()
        cleaned = re.sub(r'^\b(?:some|any|a|an|the)\b\s*', '', cleaned, flags=re.I).strip()
        cleaned = re.sub(r'[^\w\s-]', '', cleaned).strip()
        if cleaned and len(cleaned) > 2 and cleaned not in ["item", "items", "product", "products", "thing", "things"]:
            cleaned_cat = re.sub(r'\s+(?:item|items|product|products|thing|things)$', '', cleaned, flags=re.I).strip()
            best_category = cleaned_cat if cleaned_cat else cleaned

    result["category"] = best_category

    # Budget extraction
    budget_patterns = [
        r'(?:under|below|within|budget|max|upto|up to|less than|at most|around|about)\s*(?:rs\.?|₹|inr|\$)?\s*(\d[\d,]*)',
        r'(?:rs\.?|₹|inr|\$)\s*(\d[\d,]*)',
        r'(\d[\d,]*)\s*(?:rs|rupees|inr|bucks|dollars|budget)',
    ]
    for pattern in budget_patterns:
        match = re.search(pattern, text_lower)
        if match:
            try:
                result["budget"] = float(match.group(1).replace(",", ""))
                break
            except ValueError:
                pass

    # Rating extraction
    rating_patterns = [
        r'(?:above|min|minimum|at least|over|rating)\s*(?:of|:)?\s*(\d\.?\d*)\s*(?:star|stars|rating)?',
        r'(\d\.?\d*)\+?\s*(?:star|stars|rating)',
    ]
    for pattern in rating_patterns:
        match = re.search(pattern, text_lower)
        if match:
            try:
                val = float(match.group(1))
                if 1.0 <= val <= 5.0:
                    result["rating"] = val
                    break
            except ValueError:
                pass

    return result

# Node 1 — Info Gatherer (Always proceeds — never blocks for follow-up)

async def info_gatherer(state: ChatState, config = None) -> dict:
    """
    Extract purchase preferences, prioritizing the CURRENT user input as the primary authority.
    Resets stale category/brand state whenever the user requests a new topic or category.
    """
    user_input = state["user_input"]
    logger.info(f"[NODE: info_gatherer] Gathering preferences for user_input: '{user_input}'")

    existing_brand = state.get("brand") or None
    existing_budget = state.get("budget") or None
    existing_rating = state.get("rating") or None
    existing_category = state.get("category") or None

    # Step 1: Extract preferences directly from CURRENT user message (highest priority)
    current_prefs = await asyncio.to_thread(_extract_preferences_from_text, user_input)

    # Check if current input specifies a clear new product category or item request
    if current_prefs["category"]:
        category = current_prefs["category"]
        # Topic switch or new product query: RESET old brand unless specified in current message
        brand = current_prefs["brand"] # Resets stale brand from previous turns (e.g. Skechers)
        budget = current_prefs["budget"] if current_prefs["budget"] is not None else existing_budget
        rating = current_prefs["rating"] if current_prefs["rating"] is not None else existing_rating
    elif current_prefs["budget"] is not None or current_prefs["rating"] is not None or current_prefs["brand"] is not None:
        # Refinement on existing category
        category = existing_category or user_input.strip()
        brand = current_prefs["brand"] if current_prefs["brand"] is not None else existing_brand
        budget = current_prefs["budget"] if current_prefs["budget"] is not None else existing_budget
        rating = current_prefs["rating"] if current_prefs["rating"] is not None else existing_rating
    else:
        # Default: Use current user input as the category/query and clear stale brand filter
        category = user_input.strip()
        brand = current_prefs["brand"]
        budget = existing_budget
        rating = existing_rating

    updates = {
        "brand": brand,
        "budget": budget,
        "rating": rating,
        "category": category,
        "info_complete": True,
    }

    logger.info(f"[NODE: info_gatherer] Final preferences -> category={category}, brand={brand}, budget={budget}, rating={rating}, info_complete=True")
    return updates


# Node 2 — Query Generator

_SCHEMA_CACHE = None

async def get_schema_sample() -> str:
    """Fetch sample document schema from MongoDB collection to anchor LLM query generation."""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:
        return _SCHEMA_CACHE

    try:
        async with MongoDBMCPConnector() as conn:
            results = await conn.read(collection_name="Products", filter_query={}, limit=2)
            if isinstance(results, list) and results:
                samples_text = []
                for p in results[:2]:
                    slim_p = {
                        k: (v[:60] + "..." if isinstance(v, str) and len(v) > 60 else v)
                        for k, v in p.items()
                        if k not in ("_id", "images")
                    }
                    samples_text.append(json.dumps(slim_p, default=str))
                _SCHEMA_CACHE = "\n".join(samples_text)
                return _SCHEMA_CACHE
    except Exception as exc:
        logger.warning(f"Failed to fetch schema sample: {exc}")

    _SCHEMA_CACHE = "Fields: title, brand, categories, description, final_price, rating"
    return _SCHEMA_CACHE


async def query_generator(state: ChatState) -> dict:
    """Use LLM to translate user criteria into a MongoDB filter document."""
    logger.info(f"[NODE: query_generator] Generating MQL for category={state.get('category')}, brand={state.get('brand')}, budget={state.get('budget')}")
    schema_sample = await get_schema_sample()

    prompt = QUERY_GENERATOR_PROMPT.format(
        schema_sample=schema_sample,
        category=state.get("category") or "any",
        brand=state.get("brand") or "any",
        budget=state.get("budget") or "no limit",
        rating=state.get("rating") or "no minimum",
        additional=json.dumps(state.get("additional_preferences") or {}),
    )

    mongo_query = None
    try:
        chain = llm_lite | StrOutputParser()
        raw_query = chain.invoke([HumanMessage(content=prompt)])
        mongo_query = _parse_json_from_llm(raw_query)
    except Exception as e:
        logger.warning(f"[NODE: query_generator] LLM MQL generation failed ({e})")

    if mongo_query is None:
        mongo_query = {}
        cat = state.get("category")
        brand_val = state.get("brand")
        if cat and str(cat).lower() not in ("any", "none", "null"):
            mongo_query["categories"] = {"$regex": cat, "$options": "i"}
        if brand_val and str(brand_val).lower() not in ("any", "none", "null"):
            mongo_query["brand"] = {"$regex": brand_val, "$options": "i"}
    else:
        mongo_query = _normalize_filter(mongo_query)

    logger.info(f"[NODE: query_generator] Generated MongoDB filter: {mongo_query}")
    return {"mongo_query": mongo_query}

# Node 3 — Query Executor

def _parse_price(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    match = re.search(r'(\d[\d,]*\.?\d*)', str(val))
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            pass
    return None

def _parse_rating(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    match = re.search(r'(\d+\.?\d*)', str(val))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None




_EXCHANGE_RATE_CACHE = {"rate": 83.0}

def get_usd_to_inr_rate() -> float:
    """
    Returns USD to INR exchange rate with default fallback.
    """
    return _EXCHANGE_RATE_CACHE.get("rate", 83.0)

def convert_currency(amount, from_curr: str = "INR", to_curr: str = "USD") -> float | None:
    """
    Dynamically converts currency amounts between INR and USD based on exchange rate.
    """
    if amount is None:
        return None
    try:
        val = float(amount)
        rate = get_usd_to_inr_rate()
        if from_curr.upper() == "INR" and to_curr.upper() == "USD":
            return val / rate if val > 500 else val
        elif from_curr.upper() == "USD" and to_curr.upper() == "INR":
            return val * rate if val < 1000 else val
        return val
    except (ValueError, TypeError):
        return None


async def query_executor(state: ChatState, config = None) -> dict:
    """Execute MongoDB query via MCP connector, then perform secondary filtering and scoring in Python."""
    mongo_query = state.get("mongo_query", {})
    logger.info(f"[NODE: query_executor] Executing MongoDB query: {mongo_query}")
    queue = None
    if config and "configurable" in config:
        queue = config["configurable"].get("stream_queue")

    try:
        async with MongoDBMCPConnector() as conn:
            results = await conn.read(
                collection_name="Products",
                filter_query=mongo_query,
                limit=50,
            )
            results = results if isinstance(results, list) else []

            # Fallback 1: Broad regex query if strict filter returned 0 documents
            if not results and mongo_query:
                logger.info("[NODE: query_executor] Strict query returned 0 docs — retrying with broad category/title filter")
                cat = state.get("category")
                if cat and str(cat).lower() not in ("any", "none", "null"):
                    clean_cat = re.sub(r'\b(?:items|item|products|product|stuff|things)\b', '', str(cat), flags=re.I).strip()
                    term = clean_cat if clean_cat else str(cat)
                    broad_filter = {"$or": [
                        {"categories": {"$regex": term, "$options": "i"}},
                        {"title": {"$regex": term, "$options": "i"}},
                        {"description": {"$regex": term, "$options": "i"}}
                    ]}
                    results = await conn.read(
                        collection_name="Products",
                        filter_query=broad_filter,
                        limit=50,
                    )
                    results = results if isinstance(results, list) else []

            # Fallback 2: Catalog sample ONLY if no specific category was requested
            if not results and not state.get("category"):
                logger.info("[NODE: query_executor] No specific category requested and 0 docs — fetching general catalog sample")
                results = await conn.read(
                    collection_name="Products",
                    filter_query={},
                    limit=30,
                )
                results = results if isinstance(results, list) else []

            logger.info(f"[NODE: query_executor] MongoDB returned {len(results)} raw document candidates")
    except Exception as exc:
        error_msg = "I had trouble searching our product database. Please try again in a moment."
        logger.error(f"[NODE: query_executor] DB query exception: {exc}")
        if queue:
            await queue.put(error_msg)
        return {
            "raw_products": [],
            "response": error_msg,
            "messages": [AIMessage(content=f"{error_msg} (Error: {exc})")],
        }

    # Filter & rank products in Python
    filtered = []
    budget = state.get("budget")
    rating = state.get("rating")
    brand = state.get("brand")
    category = state.get("category")

    # Currency conversion: if budget is in INR (> 500), convert to USD dynamically
    if budget is not None:
        budget = convert_currency(budget, from_curr="INR", to_curr="USD")

    if rating is not None:
        try:
            rating = float(rating)
        except (ValueError, TypeError):
            rating = None

    for p in results:
        # Check price
        if budget is not None:
            p_price = _parse_price(p.get("final_price") or p.get("price"))
            if p_price is not None:
                comp_price = p_price / 83.0 if p_price > 500 else p_price
                if comp_price > budget:
                    continue
        # Check rating
        if rating is not None:
            p_rating = _parse_rating(p.get("rating"))
            if p_rating is not None and p_rating < rating:
                continue
        filtered.append(p)

    logger.info(f"[NODE: query_executor] Post-filtering matched {len(filtered)} candidate products")

    if not filtered:
        no_results_msg = (
            "I couldn't find any products matching your exact criteria. "
            "Could you try adjusting your budget, brand preference, or category?"
        )
        if queue:
            await queue.put(no_results_msg)
        return {
            "raw_products": [],
            "top_products": [],
            "response": no_results_msg,
            "messages": [AIMessage(content=no_results_msg)],
        }

    return {"raw_products": filtered}

# Node 4 — Reranker

async def reranker(state: ChatState, config = None) -> dict:
    """LLM-powered reranker: sends all filtered candidates to the LLM for intelligent top-3 selection."""
    products = state.get("raw_products", [])
    logger.info(f"[NODE: reranker] Processing {len(products)} candidate products via LLM reranking")

    queue = None
    if config and "configurable" in config:
        queue = config["configurable"].get("stream_queue")

    if not products:
        msg = "No products were found to recommend. Try broadening your search."
        if queue:
            await queue.put(msg)
        return {
            "top_products": [],
            "response": msg,
            "messages": [AIMessage(content=msg)],
        }

    # Build slim candidate list for LLM (cap at 15 to control token usage)
    candidates = products[:15]
    products_text = ""
    for i, p in enumerate(candidates, 1):
        slim = {
            "title": p.get("title"),
            "brand": p.get("brand"),
            "price": p.get("final_price") or p.get("price"),
            "rating": p.get("rating"),
            "categories": str(p.get("categories", ""))[:80],
        }
        products_text += f"\n{i}. {json.dumps(slim, default=str)}"

    prompt = RERANKER_PROMPT.format(
        category=state.get("category") or "any",
        brand=state.get("brand") or "any",
        budget=state.get("budget") or "no limit",
        rating=state.get("rating") or "no minimum",
        products=products_text,
    )

    # LLM-based selection and intro generation
    selected_indices = [0, 1, 2]  # fallback: first 3
    recommendation = "Here are the top product recommendations matching your criteria:"

    try:
        chain = llm_lite | StrOutputParser()
        raw_response = chain.invoke([HumanMessage(content=prompt)])
        llm_result = _parse_json_from_llm(raw_response)

        if llm_result and "selected_indices" in llm_result:
            raw_indices = llm_result["selected_indices"]
            # Convert 1-indexed LLM output to 0-indexed, validate bounds
            selected_indices = []
            for idx in raw_indices:
                try:
                    zero_idx = int(idx) - 1
                    if 0 <= zero_idx < len(candidates):
                        selected_indices.append(zero_idx)
                except (ValueError, TypeError):
                    continue
            if not selected_indices:
                selected_indices = [0, 1, 2]  # fallback
            logger.info(f"[NODE: reranker] LLM selected indices (0-based): {selected_indices}")

        if llm_result and llm_result.get("intro"):
            recommendation = llm_result["intro"]

    except Exception as exc:
        logger.warning(f"[NODE: reranker] LLM reranking failed ({exc}). Falling back to first 3 candidates.")

    # Build top products from LLM-selected indices
    top_products = []
    for idx in selected_indices[:3]:
        if idx < len(candidates):
            top_products.append(candidates[idx])

    # Ensure at least some products if LLM returned weird indices
    if not top_products:
        top_products = candidates[:3]

    # Clean product image & merchant fields for UI cards
    for p in top_products:
        img = p.get("image") or p.get("img_url") or p.get("images") or p.get("image_url")
        if isinstance(img, list) and img:
            img = img[0]
        p["image"] = img or ""
        p["image_url"] = img or ""
        p["merchant_id"] = p.get("merchant_id") or p.get("account_number") or p.get("merchant") or ""

    # Stream intro to UI if queue is available
    if queue and recommendation:
        await queue.put(recommendation)

    logger.info(f"[NODE: reranker] LLM selected Top {len(top_products)} products for UI")

    return {
        "top_products": top_products,
        "response": recommendation,
        "messages": [AIMessage(content=recommendation)],
        "info_complete": False,
        "intent": "",
        "brand": None,
        "budget": None,
        "rating": None,
        "category": None,
        "mongo_query": None,
        "raw_products": [],
    }
