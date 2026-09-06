import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.cache import InMemoryCache
from langchain_core.globals import set_llm_cache
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GOOGLE_API_KEY")

set_llm_cache(InMemoryCache())

llm_lite = ChatGoogleGenerativeAI(
    model="gemini-3.5-flash-lite",
    google_api_key=api_key,
    temperature=0,
    max_retries=2,
    request_timeout=10,
)

llm = ChatGoogleGenerativeAI(
    model="gemini-3.6-flash",
    google_api_key=api_key,
    temperature=0,
    max_retries=2,
    request_timeout=10,
)
llm_pro = ChatGoogleGenerativeAI(
    model="gemini-3.7-flash",
    google_api_key=api_key,
    temperature=0,
    max_retries=2,
    request_timeout=10,
)


def get_text_content(content) -> str:
    """Extract string content safely from str or list (handles Gemini streaming chunks)."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") == "text" and "text" in part:
                    text_parts.append(part["text"])
                elif "text" in part:
                    text_parts.append(str(part["text"]))
        return "".join(text_parts)
    return str(content) if content is not None else ""

