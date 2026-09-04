"""
FastAPI REST API wrapper for Recommendation Engine.
Provides high-throughput endpoints for item-to-item recommendations.
"""

import os
import sys
import time
from typing import List, Optional
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE_DIR)

from model.recommender import get_recommender

app = FastAPI(
    title="ShopSmart Ultra-Fast Recommendation API",
    description="Sub-millisecond Item-to-Item Recommendation Engine",
    version="1.0.0"
)

class RecommendationRequest(BaseModel):
    product_name: str
    top_k: Optional[int] = 3

class RecommendationItem(BaseModel):
    product: str
    confidence_score: float
    recommendation_type: str
    reason: Optional[str] = None

class RecommendationResponse(BaseModel):
    purchased_product: str
    recommendations: List[RecommendationItem]
    latency_ms: float

@app.get("/recommend", response_model=RecommendationResponse)
@app.post("/recommend", response_model=RecommendationResponse)
async def recommend_endpoint(request: Optional[RecommendationRequest] = None, product: Optional[str] = Query(None), top_k: int = 3):
    t0 = time.perf_counter()

    prod_name = request.product_name if request else product
    k = request.top_k if request and request.top_k else top_k

    if not prod_name:
        raise HTTPException(status_code=400, detail="Missing product name (pass 'product_name' in JSON body or 'product' query param)")

    recommender = get_recommender()
    recs = recommender.predict(prod_name, top_k=k)
    t1 = time.perf_counter()

    latency_ms = (t1 - t0) * 1000.0

    return {
        "purchased_product": prod_name,
        "recommendations": recs,
        "latency_ms": round(latency_ms, 4)
    }

@app.get("/health")
async def health_check():
    recommender = get_recommender()
    return {
        "status": "healthy",
        "model_loaded": recommender.is_loaded,
        "total_indexed_products": len(recommender.all_products)
    }
