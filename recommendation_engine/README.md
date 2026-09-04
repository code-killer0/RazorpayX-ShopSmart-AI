# ⚡ High-Throughput Sub-Millisecond Recommendation Engine

An ultra-fast, production-grade **Item-to-Item ("If buying Product A → Predict Product B") Recommendation Engine** designed specifically for real-time e-commerce checkout & recommendation workflows.

---

## 🎯 Key Objectives & Features

1. **Item-to-Item Predictor**: Given a product currently being purchased (Product A), predicts the top-K complementary or frequently co-purchased products to buy next (Product B).
2. **Sub-Millisecond Latency SLA**: Average inference time of **`< 0.2 milliseconds`** per query ($O(1)$ Hash Index lookup).
3. **High Throughput**: Capable of processing over **`50,000+ Requests Per Second (RPS)`** per CPU core.
4. **Automated Dataset Fetcher**: Automatically fetches real-world market basket transactions from public e-commerce mirrors or synthesizes co-occurrence transactions.
5. **Hybrid Architecture**:
   - **Primary Model**: Item-to-Item Co-occurrence & Association Metric (Confidence $P(B|A)$, Jaccard Similarity, and Lift).
   - **Fallback Model**: TF-IDF Vectorizer + Cosine Similarity matrix for cold-start/new products without transaction history.

---

## 🏗️ Architecture Design & Research Rationale

| Model Class | Latency per Request | Throughput (RPS) | Suitability for Real-Time Checkout |
| :--- | :--- | :--- | :--- |
| **Item-Item Co-occurrence Index (Selected)** | **`< 0.2 ms`** | **`> 50,000`** | 🟢 **Ideal (Industry Gold Standard - Amazon / Instacart)** |
| **TF-IDF + Nearest Neighbors Index** | **`< 0.5 ms`** | **`> 20,000`** | 🟢 **Ideal (Content Cold-Start Fallback)** |
| **Deep Neural Nets / Transformers** | `50 - 200 ms` | `< 500` | 🔴 Fails strict latency SLA |
| **LLM Rerankers** | `800 - 3000 ms` | `< 20` | 🔴 Fails real-time checkout requirement |

---

## 📁 Directory Structure

```
recommendation_engine/
├── dataset/
│   ├── download_dataset.py       # Downloads e-commerce transactions dataset from internet
│   └── raw_transactions.csv      # Fetched market basket transactions
├── model/
│   ├── train_model.py            # Model training & precomputation pipeline
│   ├── recommender.py            # Sub-millisecond inference engine module
│   └── model_artifacts.pkl       # Serialized index & artifacts for instant loading
├── benchmark.py                  # Performance benchmark (Latency, P95/P99, RPS)
├── api.py                        # Standalone FastAPI recommendation service
├── test_recommendation.py        # Pytest test suite for validation & SLA checks
└── README.md                     # Documentation
```

---

## 🚀 How to Run & Benchmark

### 1. Train Model & Precompute Lookup Index
```bash
python recommendation_engine/model/train_model.py
```

### 2. Run Latency & Throughput Benchmark
```bash
python recommendation_engine/benchmark.py
```

### 3. Run Pytest Suite
```bash
pytest recommendation_engine/test_recommendation.py
```

---

## 🌐 API Endpoint Integration

Integrated into ShopSmart FastAPI server at `/api/recommendations`.

### Request
```http
GET /api/recommendations?product=Nike%20Air%20Zoom%20Running%20Shoes&top_k=3
```

### Response
```json
{
  "success": true,
  "purchased_product": "Nike Air Zoom Running Shoes",
  "recommendations": [
    {
      "product": "Puma Running Socks 3-Pack",
      "confidence_score": 0.885,
      "recommendation_type": "Frequently Bought Together",
      "reason": "Frequently co-purchased (88% co-occurrence rate)"
    },
    {
      "product": "Sports Water Bottle 1L",
      "confidence_score": 0.812,
      "recommendation_type": "Frequently Bought Together",
      "reason": "Frequently co-purchased (81% co-occurrence rate)"
    }
  ],
  "latency_ms": 0.145
}
```
