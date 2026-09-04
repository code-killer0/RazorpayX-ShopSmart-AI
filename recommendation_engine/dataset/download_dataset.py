import os

DATASET_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_CSV = os.path.join(DATASET_DIR, "raw_transactions.csv")

def fetch_dataset() -> str:
    """Return path to existing CSV dataset."""
    return OUTPUT_CSV
