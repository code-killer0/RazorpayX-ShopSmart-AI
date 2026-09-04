import json
from typing import Optional, Dict, Any


def parse_json_from_llm(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    content = text.strip()
    if "```" in content:
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def normalize_filter(filter_dict: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {}
    for k, v in filter_dict.items():
        if k == "name":
            normalized["title"] = v
        elif k == "category":
            normalized["categories"] = v
        elif k in ("price", "final_price"):
            pass
        elif k == "stock":
            normalized["availability"] = v
        else:
            normalized[k] = v
    return normalized
