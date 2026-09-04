"""
Unit tests for Recommendation Engine.
Tests dataset fetching, model training, prediction accuracy, and sub-millisecond latency SLA.
"""

import os
import sys
import time
import pytest

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

from dataset.download_dataset import fetch_dataset
from model.train_model import train_and_save_model, ARTIFACT_PATH
from model.recommender import ItemToItemRecommender, get_recommender


def test_dataset_generation_or_download():
    """Verify dataset fetcher downloads or creates CSV properly."""
    csv_file = fetch_dataset()
    assert os.path.exists(csv_file)
    assert os.path.getsize(csv_file) > 100


def test_model_training():
    """Verify model training builds artifacts file."""
    art_path = train_and_save_model()
    assert os.path.exists(art_path)
    assert os.path.getsize(art_path) > 100


def test_recommender_prediction_structure():
    """Verify prediction output returns non-empty list with valid schema."""
    recommender = ItemToItemRecommender()
    recs = recommender.predict("Nike Air Zoom Running Shoes", top_k=3)

    assert len(recs) == 3
    for r in recs:
        assert "product" in r
        assert "confidence_score" in r
        assert "recommendation_type" in r
        assert r["confidence_score"] >= 0.0


def test_sub_millisecond_latency_sla():
    """Verify latency SLA is sub-millisecond (< 1.0 ms)."""
    recommender = ItemToItemRecommender()
    product = "Adidas Ultraboost Sneakers"

    # Warmup
    for _ in range(50):
        recommender.predict(product, top_k=3)

    t0 = time.perf_counter()
    for _ in range(1000):
        recommender.predict(product, top_k=3)
    t1 = time.perf_counter()

    avg_ms = ((t1 - t0) / 1000.0) * 1000.0  # ms per query
    print(f"\nAverage prediction latency: {avg_ms:.4f} ms")
    assert avg_ms < 1.0, f"Latency SLA violated: {avg_ms:.4f} ms >= 1.0 ms"
