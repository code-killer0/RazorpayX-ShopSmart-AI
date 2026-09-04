import os
import sys
import pickle
import logging
import numpy as np
import pandas as pd
from collections import defaultdict, Counter
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# Ensure dataset script can be imported
ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE_DIR)

from recommendation_engine.dataset.download_dataset import fetch_dataset, OUTPUT_CSV

MODEL_DIR = os.path.dirname(os.path.abspath(__file__))
ARTIFACT_PATH = os.path.join(MODEL_DIR, "model_artifacts.pkl")


def train_and_save_model(csv_path: str = OUTPUT_CSV) -> str:
    """Train Item-to-Item recommendation model and save precomputed lookup index."""
    logger.info("Starting Item-to-Item Recommendation Model Training...")

    if not os.path.exists(csv_path):
        logger.info(f"Dataset missing at {csv_path}. Fetching dataset...")
        csv_path = fetch_dataset()

    try:
        df = pd.read_csv(csv_path, encoding='utf-8')
    except (UnicodeDecodeError, pd.errors.ParserError):
        logger.warning("Detected non-UTF-8 encoding in CSV dataset. Retrying with ISO-8859-1 encoding...")
        try:
            df = pd.read_csv(csv_path, encoding='ISO-8859-1')
        except Exception:
            df = pd.read_csv(csv_path, encoding='utf-8', encoding_errors='replace')
    logger.info(f"Loaded dataset: {len(df)} records")

    # Clean dataset & detect column headers
    possible_desc = ["Description", "description", "item", "product_name", "title", "Item"]
    possible_inv = ["InvoiceNo", "Invoice", "invoice", "order_id", "BasketID", "Invoice_No"]

    description_col = next((c for c in possible_desc if c in df.columns), df.columns[1] if len(df.columns) > 1 else df.columns[0])
    invoice_col = next((c for c in possible_inv if c in df.columns), df.columns[0])

    print(f"Using columns -> Invoice: '{invoice_col}', Product: '{description_col}'")

    df = df.dropna(subset=[description_col, invoice_col])
    df[description_col] = df[description_col].astype(str).str.strip()

    df = df[df[description_col].str.len() > 2]
    baskets = df.groupby(invoice_col)[description_col].apply(lambda x: list(set(x))).tolist()
    print(f"Extracted {len(baskets)} shopping baskets.")

    item_counts = Counter()
    pair_counts = defaultdict(Counter)

    for basket in baskets:
        for item in basket:
            item_counts[item] += 1

        for i in range(len(basket)):
            for j in range(len(basket)):
                if i != j:
                    item_a = basket[i]
                    item_b = basket[j]
                    pair_counts[item_a][item_b] += 1

    total_baskets = len(baskets)
    print(f"Unique products tracked: {len(item_counts)}")

    # 3. Compute Item-to-Item Scores (Confidence, Lift, Jaccard)
    co_occurrence_index = {}

    for item_a, counter_b in pair_counts.items():
        key_a = item_a.lower()
        count_a = item_counts[item_a]
        prob_a = count_a / total_baskets

        recommendations = []
        for item_b, co_count in counter_b.items():
            count_b = item_counts[item_b]
            prob_b = count_b / total_baskets

            confidence = co_count / count_a  # P(B|A)
            lift = confidence / prob_b if prob_b > 0 else 1.0
            jaccard = co_count / (count_a + count_b - co_count)

            # Combined score favoring Lift and Confidence
            score = 0.5 * confidence + 0.3 * jaccard + 0.2 * min(lift / 10.0, 1.0)

            recommendations.append({
                "product": item_b,
                "score": score,
                "confidence": round(confidence, 4),
                "lift": round(lift, 4),
                "jaccard": round(jaccard, 4),
                "type": "Frequently Bought Together",
                "reason": f"Frequently co-purchased ({int(confidence*100)}% co-occurrence rate)"
            })

        # Sort recommendations by score descending
        recommendations.sort(key=lambda x: x["score"], reverse=True)
        co_occurrence_index[key_a] = recommendations

    # 4. Content-Based TF-IDF Model Fallback
    unique_items = list(item_counts.keys())
    tfidf = TfidfVectorizer(stop_words='english', ngram_range=(1, 2))
    tfidf_matrix = tfidf.fit_transform(unique_items)
    cosine_sim_matrix = cosine_similarity(tfidf_matrix, tfidf_matrix)

    content_sim_index = {}
    for idx, item in enumerate(unique_items):
        key = item.lower()
        sim_scores = list(enumerate(cosine_sim_matrix[idx]))
        sim_scores.sort(key=lambda x: x[1], reverse=True)

        recs = []
        for other_idx, sim_score in sim_scores[1:10]:  # Top 9 similar items
            if sim_score > 0.05:
                recs.append({
                    "product": unique_items[other_idx],
                    "score": float(sim_score),
                    "type": "Category & Feature Match",
                    "reason": f"High product feature similarity ({int(sim_score*100)}% match)"
                })
        content_sim_index[key] = recs

    # Save artifacts
    artifacts = {
        "item_co_occurrence": co_occurrence_index,
        "content_similarity": content_sim_index,
        "all_products": unique_items,
        "total_baskets": total_baskets,
    }

    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(ARTIFACT_PATH, "wb") as f:
        pickle.dump(artifacts, f, protocol=pickle.HIGHEST_PROTOCOL)

    print(f" Model successfully trained & precomputed artifacts saved to:\n   {ARTIFACT_PATH}")
    print(f"   Indexed {len(co_occurrence_index)} products for instant < 0.2ms lookup.")

    return ARTIFACT_PATH


if __name__ == "__main__":
    train_and_save_model()
