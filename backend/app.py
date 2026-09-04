import os
import sys
import re
import time
import json
import asyncio
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from pymongo import MongoClient
import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger("ShopSmartApp")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import get_graph, retrieve_all_threads
from agents.llm import llm_lite
from langchain_core.output_parsers import StrOutputParser

_PAYMENT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp", "payment")
sys.path.insert(0, _PAYMENT_PATH)
from razorpay_mcp import RazorpayMCPConnector, get_razorpay_credentials

_REC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recommendation_engine")
sys.path.insert(0, _REC_PATH)
from model.recommender import get_recommender


def init_merchant_info():
    try:
        creds = get_razorpay_credentials()
        client = get_shared_mongo_client()
        if client:
            db_name = os.getenv("MONGODB_DATABASE", "Merchant_1")
            db = client[db_name]
            col = db["Merchant_Info"]
            col.update_one(
                {},
                {
                    "$set": {
                        "merchant_name": "ShopSmart Demo Merchant",
                        "razorpay_key_id": creds["key_id"],
                        "razorpay_key_secret": creds["key_secret"],
                        "account_number": creds["account_number"],
                        "currency": "INR"
                    }
                },
                upsert=True
            )
            logger.info("Synchronized Merchant_Info document in database with resolved keys.")
    except Exception as e:
        logger.warning(f"Error initializing Merchant_Info: {e}")

app = FastAPI(title="ShopSmart AI Chatbot")

@app.on_event("startup")
def startup_event():
    init_merchant_info()
    try:
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, get_cached_mongo_products)
    except Exception as e:
        logger.warning(f"Error pre-warming products cache: {e}")

@app.middleware("http")
async def add_no_cache_header(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith(".js") or path.endswith(".css") or path == "/":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response

class ChatRequest(BaseModel):
    message: str
    thread_id: str = "default"

class TitleRequest(BaseModel):
    message: str

class CreateOrderRequest(BaseModel):
    product_title: str
    price: float
    merchant_id: Optional[str] = None


@app.get("/api/threads")
async def get_threads():
    try:
        threads = retrieve_all_threads()
        return {"threads": threads}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/generate-title")
async def generate_title(request: TitleRequest):
    msg = request.message.strip()
    if not msg:
        return {"title": "New Chat"}
    
    def get_fallback_title(text: str) -> str:
        words = [w for w in text.split() if len(w) > 2]
        return " ".join(words[:3]).title() if words else (text[:22] + "...")

    try:
        prompt = (
            f"Generate a short 2 to 3 word title for chat: '{msg}'. "
            "ONLY the title, no quotes or punctuation."
        )
        chain = llm_lite | StrOutputParser()
        
        # Execute LLM call in worker thread with 0.8s timeout to avoid blocking event loop
        raw_title = await asyncio.wait_for(
            asyncio.to_thread(chain.invoke, [HumanMessage(content=prompt)]),
            timeout=0.8
        )
        raw_title = raw_title.strip().strip('"').strip("'")
        title = raw_title if raw_title and len(raw_title) <= 35 else get_fallback_title(msg)
        return {"title": title}
    except Exception as e:
        logger.info(f"[TITLE API] Using fast fallback title: {e}")
        return {"title": get_fallback_title(msg)}

class SaveMessageRequest(BaseModel):
    role: str
    text: str
    products: Optional[list] = []

def save_message_to_mongo(thread_id: str, role: str, text: str, products: list = None):
    try:
        client = get_shared_mongo_client()
        if client:
            db_name = os.getenv("MONGODB_DATABASE", "Merchant_1")
            db = client[db_name]
            col = db["Chat_History"]
            
            # Deduplication: Avoid duplicate consecutive insertion of the exact same message
            doc = col.find_one({"thread_id": thread_id})
            if doc and "messages" in doc and len(doc["messages"]) > 0:
                last_msg = doc["messages"][-1]
                if last_msg.get("role") == role and last_msg.get("text") == text:
                    logger.info(f"Skipping duplicate message insert for thread '{thread_id}'")
                    return
            
            msg_doc = {
                "role": role,
                "text": text,
                "products": products or [],
                "timestamp": datetime.utcnow().isoformat()
            }
            
            col.update_one(
                {"thread_id": thread_id},
                {
                    "$push": {"messages": msg_doc},
                    "$set": {"updated_at": datetime.utcnow().isoformat()}
                },
                upsert=True
            )
    except Exception as e:
        logger.error(f"Failed to save message to MongoDB Chat_History: {e}")

@app.post("/api/history/{thread_id}/save")
async def save_history_message(thread_id: str, req: SaveMessageRequest):
    save_message_to_mongo(thread_id, req.role, req.text, req.products or [])
    return {"success": True}

@app.get("/api/history/{thread_id}")
async def get_history(thread_id: str):
    try:
        client = get_shared_mongo_client()
        if client:
            db_name = os.getenv("MONGODB_DATABASE", "Merchant_1")
            db = client[db_name]
            doc = db["Chat_History"].find_one({"thread_id": thread_id})
            if doc and "messages" in doc and len(doc["messages"]) > 0:
                # Deduplicate consecutive identical messages if present from past duplicates
                deduped = []
                for m in doc["messages"]:
                    if not deduped or (deduped[-1].get("role") != m.get("role") or deduped[-1].get("text") != m.get("text")):
                        deduped.append(m)
                return {
                    "messages": deduped,
                    "top_products": []
                }
        
        # Fallback to LangGraph checkpoint state
        graph = get_graph()
        config = {"configurable": {"thread_id": thread_id}}
        state = await graph.aget_state(config)
        
        messages = state.values.get("messages", [])
        top_products = state.values.get("top_products", [])
        serialized = []
        for idx, msg in enumerate(messages):
            role = "user" if isinstance(msg, HumanMessage) else "assistant"
            is_last_assistant = (role == "assistant" and idx == len(messages) - 1)
            serialized.append({
                "role": role,
                "text": msg.content,
                "products": top_products if is_last_assistant else []
            })
            
        return {
            "messages": serialized,
            "top_products": top_products or []
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/history/{thread_id}")
async def delete_history(thread_id: str):
    try:
        client = get_shared_mongo_client()
        if client:
            db_name = os.getenv("MONGODB_DATABASE", "Merchant_1")
            db = client[db_name]
            db["checkpoints"].delete_many({"thread_id": thread_id})
            db["checkpoint_writes"].delete_many({"thread_id": thread_id})
            db["Chat_History"].delete_one({"thread_id": thread_id})
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    queue = asyncio.Queue()
    
    # Save user message to MongoDB Chat_History
    save_message_to_mongo(request.thread_id, "user", request.message, [])
    
    # Setup configuration with stream queue
    config = {
        "configurable": {
            "thread_id": request.thread_id,
            "stream_queue": queue
        }
    }
    
    async def event_generator():
        input_state = {
            "user_input": request.message,
            "messages": [HumanMessage(content=request.message)],
        }
        
        task = asyncio.create_task(get_graph().ainvoke(input_state, config=config))
        full_tokens = []
        
        while not task.done() or not queue.empty():
            try:
                token = await asyncio.wait_for(queue.get(), timeout=0.05)
                full_tokens.append(token)
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
                queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
                break
                
        # Get final state updates
        try:
            result = await task
            top_products = result.get("top_products", [])
            assistant_response = "".join(full_tokens).strip() or result.get("response", "")
            
            # Save assistant response & top_products to MongoDB Chat_History
            save_message_to_mongo(request.thread_id, "assistant", assistant_response, top_products or [])
            
            yield f"data: {json.dumps({'type': 'products', 'products': top_products or []})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/payment/create-order")
async def create_order_endpoint(request: CreateOrderRequest):
    try:
        logger.info(f"[PAYMENT API] Received order creation request for product='{request.product_title}', price={request.price}")
        parsed_price = float(request.price) if request.price else 0.0
        inr_price = parsed_price * 83.0 if parsed_price < 1000 else parsed_price
        amount_in_paise = int(round(inr_price * 100))
        
        creds = get_razorpay_credentials()
        key_id = creds["key_id"]
        key_secret = creds["key_secret"]
        
        order_id = None
        try:
            url = "https://api.razorpay.com/v1/orders"
            payload = {
                "amount": amount_in_paise,
                "currency": "INR",
                "receipt": f"rcpt_{int(datetime.utcnow().timestamp())}",
                "notes": {
                    "product_title": request.product_title[:40] if request.product_title else "Product"
                }
            }
            def _call_razorpay_api():
                return requests.post(url, auth=HTTPBasicAuth(key_id, key_secret), json=payload, timeout=5)
            resp = await asyncio.to_thread(_call_razorpay_api)
            if resp.status_code in (200, 201):
                order_data = resp.json()
                order_id = order_data.get("id")
                logger.info(f"[PAYMENT API] Created official Razorpay order_id '{order_id}' on merchant account")
            else:
                logger.warning(f"[PAYMENT API] Razorpay Order API status {resp.status_code}: {resp.text}")
        except Exception as api_err:
            logger.error(f"[PAYMENT API] Failed to create order via Razorpay API: {api_err}")
            
        return {
            "success": True,
            "order_id": order_id,
            "amount": amount_in_paise,
            "currency": "INR",
            "key_id": key_id
        }
    except Exception as e:
        logger.error(f"[PAYMENT API] Error creating order: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/merchant/info")
async def get_merchant_info(merchant_id: Optional[str] = None):
    try:
        connector = RazorpayMCPConnector()
        details = await asyncio.to_thread(connector.get_merchant_details, merchant_id)
        if not details or details.get("ifsc") == "HDFC0001234":
            details = {
                "account_number": "7878780080316316",
                "ifsc": "UTIB0000229",
                "name": "ShopSmart Verified Vendor",
                "email": "vendor@shopsmart.ai",
                "phone": "9876543210"
            }
        return {"success": True, "merchant": details}
    except Exception as e:
        return {"success": False, "error": str(e)}

class VerifyPaymentRequest(BaseModel):
    order_id: str
    payment_id: str
    signature: str
    product_title: str
    price: Optional[float] = 0.0
    merchant_id: Optional[str] = None

@app.post("/api/payment/verify")
async def verify_payment_endpoint(request: VerifyPaymentRequest):
    try:
        logger.info(f"[PAYMENT API] Verifying payment & initiating RazorpayX payout for order_id='{request.order_id}', payment_id='{request.payment_id}'")
        is_valid = request.signature != "simulated_fail" and request.payment_id != "simulated_fail"
        
        parsed_price = float(request.price) if request.price else 0.0
        inr_price = parsed_price * 83.0 if (parsed_price > 0 and parsed_price < 1000) else (parsed_price if parsed_price >= 1000 else 100.0)

        payout_res = {}
        if is_valid:
            try:
                connector = RazorpayMCPConnector()
                payout_res = await asyncio.to_thread(connector.create_payout, inr_price, merchant_id=request.merchant_id)
                logger.info(f"[PAYMENT API] RazorpayX Payout response: {payout_res}")
            except Exception as payout_err:
                logger.error(f"[PAYMENT API] Error creating RazorpayX payout: {payout_err}")
                payout_res = {"success": False, "error": str(payout_err)}

        # Save transaction status and payout result to MongoDB
        client = get_shared_mongo_client()
        if client:
            db_name = os.getenv("MONGODB_DATABASE", "Merchant_1")
            db = client[db_name]
            col = db["Transactions"]
            col.insert_one({
                "order_id": request.order_id,
                "payment_id": request.payment_id,
                "signature": request.signature,
                "product_title": request.product_title,
                "price": request.price,
                "status": "success" if is_valid else "failed",
                "payout_id": payout_res.get("payout_id"),
                "payout_status": payout_res.get("status"),
                "payout_utr": payout_res.get("utr"),
                "payout_error": payout_res.get("error"),
                "timestamp": datetime.utcnow()
            })
            logger.info(f"[PAYMENT API] Transaction saved to MongoDB 'Transactions' collection")
            
        return {
            "success": is_valid,
            "payout": payout_res
        }
    except Exception as e:
        logger.error(f"[PAYMENT API] Error verifying payment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


_GLOBAL_MONGO_CLIENT: Optional[MongoClient] = None
_MONGO_PRODUCTS_CACHE: List[Dict[str, Any]] = []
_CACHE_LAST_UPDATED: float = 0

def get_shared_mongo_client() -> Optional[MongoClient]:
    global _GLOBAL_MONGO_CLIENT
    if _GLOBAL_MONGO_CLIENT is None:
        conn_str = os.getenv("MDB_MCP_CONNECTION_STRING") or os.getenv("MONGODB_URI")
        if conn_str:
            try:
                _GLOBAL_MONGO_CLIENT = MongoClient(
                    conn_str,
                    maxPoolSize=20,
                    connectTimeoutMS=15000,
                    serverSelectionTimeoutMS=10000,
                    connect=False
                )
            except Exception as e:
                logger.warning(f"Failed to create shared MongoClient: {e}")
    return _GLOBAL_MONGO_CLIENT

_DEFAULT_CATALOG_FALLBACK = [
    {
        "title": "Organic Omega-3 Fish Oil 1000mg",
        "brand": "Swanson Health",
        "price": 18.99,
        "rating": 4.9,
        "categories": "Health & Supplements",
        "image": "",
        "merchant_id": ""
    },
    {
        "title": "Vitamin D3 + K2 Health Complex Capsules",
        "brand": "Swanson Health",
        "price": 14.50,
        "rating": 4.8,
        "categories": "Health & Supplements",
        "image": "",
        "merchant_id": ""
    },
    {
        "title": "Puma Moisture-Wicking Athletic Socks 3-Pack",
        "brand": "Puma",
        "price": 12.99,
        "rating": 4.7,
        "categories": "Footwear & Activewear",
        "image": "",
        "merchant_id": ""
    },
    {
        "title": "Insulated Stainless Steel Water Bottle 750ml",
        "brand": "ShopSmart",
        "price": 16.99,
        "rating": 4.6,
        "categories": "Fitness Accessories",
        "image": "",
        "merchant_id": ""
    }
]

def get_cached_mongo_products() -> List[Dict[str, Any]]:
    global _MONGO_PRODUCTS_CACHE, _CACHE_LAST_UPDATED
    now = time.time()
    if _MONGO_PRODUCTS_CACHE and (now - _CACHE_LAST_UPDATED < 300):
        return _MONGO_PRODUCTS_CACHE

    client = get_shared_mongo_client()
    if client:
        try:
            db_name = os.getenv("MONGODB_DATABASE", "Merchant_1")
            db = client[db_name]
            col = db["Products"]
            docs = list(col.find(
                {},
                {
                    "title": 1,
                    "brand": 1,
                    "categories": 1,
                    "final_price": 1,
                    "initial_price": 1,
                    "price": 1,
                    "rating": 1,
                    "image": 1,
                    "image_url": 1,
                    "merchant_id": 1,
                    "account_number": 1
                }
            ).limit(5000))
            if docs:
                _MONGO_PRODUCTS_CACHE = docs
                _CACHE_LAST_UPDATED = now
                logger.info(f"[CATALOG CACHE] Refreshed {len(_MONGO_PRODUCTS_CACHE)} products from MongoDB.")
                return _MONGO_PRODUCTS_CACHE
        except Exception as e:
            logger.warning(f"Error refreshing products cache: {e}")

    if not _MONGO_PRODUCTS_CACHE:
        _MONGO_PRODUCTS_CACHE = _DEFAULT_CATALOG_FALLBACK
        _CACHE_LAST_UPDATED = now

    return _MONGO_PRODUCTS_CACHE


def _match_product_in_catalog(rec_product_name: str, db_products: List[Dict[str, Any]], purchased_product: str = "") -> Optional[Dict[str, Any]]:
    """
    Find best matching MongoDB catalog document for a recommended candidate product name.
    """
    if not rec_product_name or not db_products:
        return None

    purchased_clean = (purchased_product or "").strip().lower()
    rec_clean = re.sub(r'[^\w\s]', ' ', rec_product_name).strip().lower()
    rec_words = set(w for w in rec_clean.split() if len(w) > 2 and w not in {"the", "and", "for", "with", "pack", "item", "product"})

    if not rec_words:
        return None

    best_match = None
    best_score = 0.0

    for p in db_products:
        p_title = str(p.get("title") or "").strip()
        p_title_clean = re.sub(r'[^\w\s]', ' ', p_title).strip().lower()
        if not p_title_clean:
            continue

        # Skip if this database product is the item being purchased
        if purchased_clean and (purchased_clean in p_title_clean or p_title_clean in purchased_clean):
            continue

        # 1. Substring match
        if rec_clean in p_title_clean or p_title_clean in rec_clean:
            return p

        # 2. Word overlap matching
        p_words = set(w for w in p_title_clean.split() if len(w) > 2)
        overlap = len(rec_words & p_words)
        if overlap > 0:
            jaccard = overlap / float(len(rec_words | p_words))
            ratio = overlap / float(len(rec_words))
            score = ratio * 0.7 + jaccard * 0.3
            if score > best_score and (overlap >= 2 or ratio >= 0.5):
                best_score = score
                best_match = p

    if best_score >= 0.4:
        return best_match

    return None


@app.get("/api/recommendations")
async def get_product_recommendations(product: str = "", top_k: int = 3):
    t0 = time.perf_counter()

    try:
        enriched_recs = []
        seen_titles = set()
        seen_ids = set()

        purchased_lower = (product or "").strip().lower()

        # 1. Ask recommendation model for a candidate pool (up to 12 candidate items)
        recommender = get_recommender()
        candidate_pool = recommender.predict(product, top_k=top_k, candidate_k=max(top_k * 4, 12))

        # 2. Get cached MongoDB product catalog
        db_products = get_cached_mongo_products() or []

        # 3. Iterate candidates ONE BY ONE, check MongoDB database, and STOP as soon as top_k matches are found!
        for candidate in candidate_pool:
            cand_name = candidate.get("product") if isinstance(candidate, dict) else str(candidate)
            if not cand_name:
                continue

            db_match = _match_product_in_catalog(cand_name, db_products, purchased_product=product)

            # ONLY add candidate if it was found as a real product in the database!
            if db_match:
                m_id = str(db_match.get("_id") or db_match.get("title"))
                p_title = str(db_match.get("title") or cand_name).strip()

                if m_id in seen_ids or p_title.lower() in seen_titles:
                    continue

                seen_ids.add(m_id)
                seen_titles.add(p_title.lower())

                p_price_raw = db_match.get("final_price") or db_match.get("initial_price") or db_match.get("price") or 19.99
                p_rating_raw = db_match.get("rating") or 4.5
                p_brand = db_match.get("brand") or "ShopSmart"
                img = db_match.get("image") or db_match.get("image_url") or ""
                if isinstance(img, list) and img:
                    img = img[0]

                try:
                    clean_price_str = re.sub(r'[^\d.]', '', str(p_price_raw))
                    parsed_price = float(clean_price_str) if clean_price_str else 19.99
                except (ValueError, TypeError):
                    parsed_price = 19.99

                try:
                    clean_rating_str = re.sub(r'[^\d.]', '', str(p_rating_raw))
                    parsed_rating = float(clean_rating_str) if clean_rating_str else 4.5
                except (ValueError, TypeError):
                    parsed_rating = 4.5

                merchant_id_val = str(db_match.get("merchant_id") or db_match.get("account_number") or "")

                conf_score = float(candidate.get("confidence_score", 0.85)) if isinstance(candidate, dict) else 0.85
                rec_type = str(candidate.get("recommendation_type", "Frequently Bought Together")) if isinstance(candidate, dict) else "Frequently Bought Together"
                rec_reason = str(candidate.get("reason", f"Customers buying '{product}' frequently purchase this.")) if isinstance(candidate, dict) else f"Customers buying '{product}' frequently purchase this."

                enriched_recs.append({
                    "product": p_title,
                    "confidence_score": float(round(conf_score, 4)),
                    "recommendation_type": rec_type,
                    "reason": rec_reason,
                    "mongo_product": {
                        "title": p_title,
                        "brand": str(p_brand),
                        "price": float(round(parsed_price, 2)),
                        "rating": float(round(parsed_rating, 1)),
                        "image": str(img),
                        "merchant_id": str(merchant_id_val)
                    }
                })

                # EARLY STOPPING: Stop checking database once we found top_k valid items!
                if len(enriched_recs) == top_k:
                    break

        # 4. Fallback if candidate pool yielded fewer than top_k database matches:
        # Search MongoDB catalog for category/keyword complementary items
        if len(enriched_recs) < top_k and db_products:
            query_words = set(w for w in re.sub(r'[^\w\s]', ' ', product).lower().split() if len(w) > 2)
            for p in db_products:
                p_title = str(p.get("title") or "").strip()
                p_cat = str(p.get("categories") or "").lower()
                p_title_lower = p_title.lower()

                if not p_title:
                    continue
                if purchased_lower and (purchased_lower in p_title_lower or p_title_lower in purchased_lower):
                    continue
                if p_title_lower in seen_titles:
                    continue

                # Check category relevance or word overlap
                p_words = set(w for w in re.sub(r'[^\w\s]', ' ', p_title_lower).split() if len(w) > 2)
                is_relevant = bool(query_words & p_words) or any(qw in p_cat for qw in query_words) if query_words else True

                if is_relevant:
                    seen_titles.add(p_title_lower)
                    p_price_raw = p.get("final_price") or p.get("initial_price") or p.get("price") or 19.99
                    p_rating_raw = p.get("rating") or 4.5
                    p_brand = p.get("brand") or "ShopSmart"
                    img = p.get("image") or p.get("image_url") or ""
                    if isinstance(img, list) and img:
                        img = img[0]

                    try:
                        parsed_price = float(re.sub(r'[^\d.]', '', str(p_price_raw)))
                    except Exception:
                        parsed_price = 19.99

                    try:
                        parsed_rating = float(re.sub(r'[^\d.]', '', str(p_rating_raw)))
                    except Exception:
                        parsed_rating = 4.5

                    merchant_id_val = str(p.get("merchant_id") or p.get("account_number") or "")

                    enriched_recs.append({
                        "product": p_title,
                        "confidence_score": 0.82,
                        "recommendation_type": "Frequently Bought Together",
                        "reason": f"Top complementary product co-purchased with '{product}'",
                        "mongo_product": {
                            "title": p_title,
                            "brand": str(p_brand),
                            "price": float(round(parsed_price, 2)),
                            "rating": float(round(parsed_rating, 1)),
                            "image": str(img),
                            "merchant_id": str(merchant_id_val)
                        }
                    })

                    if len(enriched_recs) == top_k:
                        break

    except Exception as e:
        logger.error(f"[RECOMMENDATIONS API] Exception generating recommendations for '{product}': {e}")
        enriched_recs = []

    t1 = time.perf_counter()
    latency_ms = float((t1 - t0) * 1000.0)
    logger.info(f"[RECOMMENDATIONS API] Generated {len(enriched_recs)} recommendations for product '{product}' in {latency_ms:.4f} ms")
    return {
        "success": True,
        "purchased_product": str(product),
        "recommendations": enriched_recs,
        "latency_ms": float(round(latency_ms, 4))
    }

# Locate the frontend directory
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

# Ensure frontend directory exists
os.makedirs(FRONTEND_DIR, exist_ok=True)

# Mount static files (HTML, CSS, JS) at root
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
