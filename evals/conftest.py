import os
import sys
import json
import pytest

# Opt out of DeepEval telemetry
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"

# Inject backend path so we can import agents directly
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND_DIR = os.path.join(_PROJECT_ROOT, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# Load .env for API keys
from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

import csv

_GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden_dataset")


def _load_dataset(filename: str) -> list[dict]:
    """Load a golden dataset CSV file and parse JSON fields."""
    path = os.path.join(_GOLDEN_DIR, filename)
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parsed_row = {}
            for k, v in row.items():
                if v is None or v == "":
                    parsed_row[k] = None
                    continue
                # Try parsing JSON strings for complex nested fields
                if isinstance(v, str) and ((v.startswith("{") and v.endswith("}")) or (v.startswith("[") and v.endswith("]"))):
                    try:
                        parsed_row[k] = json.loads(v)
                        continue
                    except json.JSONDecodeError:
                        pass
                # Convert boolean strings
                if v.lower() == "true":
                    parsed_row[k] = True
                elif v.lower() == "false":
                    parsed_row[k] = False
                # Convert numeric strings if applicable
                else:
                    try:
                        if "." in v:
                            parsed_row[k] = float(v)
                        else:
                            parsed_row[k] = int(v)
                    except ValueError:
                        parsed_row[k] = v
            rows.append(parsed_row)
    return rows


@pytest.fixture
def intent_dataset():
    return _load_dataset("intent_dataset.csv")


@pytest.fixture
def info_extractor_dataset():
    return _load_dataset("info_extractor_dataset.csv")


@pytest.fixture
def query_generator_dataset():
    return _load_dataset("query_generator_dataset.csv")


@pytest.fixture
def inquiry_dataset():
    return _load_dataset("inquiry_dataset.csv")


@pytest.fixture
def pipeline_dataset():
    return _load_dataset("pipeline_dataset.csv")
