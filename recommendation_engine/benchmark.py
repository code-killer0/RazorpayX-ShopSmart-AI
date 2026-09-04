"""
Benchmark Script for Recommendation Engine.
Measures latency (average, P95, P99 in microseconds/milliseconds) and throughput (RPS).
"""

import os
import sys
import time
import numpy as np

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE_DIR)

from model.train_model import train_and_save_model
from model.recommender import get_recommender, ItemToItemRecommender


def run_benchmark(num_iterations: int = 10000) -> dict:
    """Run performance benchmark measuring latency and throughput."""
    print("=" * 60)
    print(f" ⚡ Running Recommendation Engine Benchmark ({num_iterations} queries)")
    print("=" * 60)

    # Train model if not trained
    recommender = ItemToItemRecommender()
    if not recommender.is_loaded:
        train_and_save_model()
        recommender = ItemToItemRecommender()

    products = recommender.all_products if recommender.all_products else [
        "Nike Air Zoom Running Shoes",
        "Adidas Ultraboost Sneakers",
        "Swanson Vitamin C Capsules",
        "Ergonomic Laptop Backpack",
        "Wireless Noise Cancelling Headphones",
    ]

    # Warmup phase
    for _ in range(100):
        recommender.predict(products[0], top_k=3)

    latencies_us = []

    start_total = time.perf_counter()

    for i in range(num_iterations):
        target_product = products[i % len(products)]

        t0 = time.perf_counter()
        recs = recommender.predict(target_product, top_k=3)
        t1 = time.perf_counter()

        latencies_us.append((t1 - t0) * 1e6)  # microseconds

    total_time_sec = time.perf_counter() - start_total

    avg_latency_us = np.mean(latencies_us)
    p50_us = np.percentile(latencies_us, 50)
    p95_us = np.percentile(latencies_us, 95)
    p99_us = np.percentile(latencies_us, 99)

    avg_latency_ms = avg_latency_us / 1000.0
    throughput_rps = num_iterations / total_time_sec

    print(f"📊 Benchmark Results:")
    print(f"   • Total Queries Processed : {num_iterations:,}")
    print(f"   • Total Time Taken        : {total_time_sec:.4f} seconds")
    print(f"   • Average Latency per Query: {avg_latency_us:.2f} µs ({avg_latency_ms:.4f} ms)")
    print(f"   • P50 Latency             : {p50_us:.2f} µs")
    print(f"   • P95 Latency             : {p95_us:.2f} µs")
    print(f"   • P99 Latency             : {p99_us:.2f} µs")
    print(f"   • Throughput (RPS)        : {throughput_rps:,.2f} requests/sec")
    print("=" * 60)

    # Verify sample prediction
    sample_prod = products[0]
    sample_recs = recommender.predict(sample_prod, top_k=3)
    print(f"\n🔍 Sample Prediction for: '{sample_prod}'")
    for idx, r in enumerate(sample_recs, 1):
        print(f"   {idx}. {r['product']} (Score: {r['confidence_score']}) -> Reason: {r.get('reason')}")

    return {
        "num_queries": num_iterations,
        "total_time_sec": total_time_sec,
        "avg_latency_ms": avg_latency_ms,
        "p95_latency_ms": p95_us / 1000.0,
        "p99_latency_ms": p99_us / 1000.0,
        "throughput_rps": throughput_rps,
    }


if __name__ == "__main__":
    run_benchmark(10000)
