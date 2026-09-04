from typing import TypedDict, Optional, Dict, Any, Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict):

    messages: Annotated[list[BaseMessage], add_messages]
    user_input: str
    intent: str 
    brand: Optional[str]
    budget: Optional[float]
    rating: Optional[float]
    category: Optional[str]
    additional_preferences: Optional[Dict[str, Any]]
    info_complete: bool
    mongo_query: Optional[Dict[str, Any]]
    raw_products: list[Dict[str, Any]]
    top_products: list[Dict[str, Any]]
    response: str
