import asyncio
import os
from dotenv import load_dotenv
from pymongo import MongoClient

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.mongodb import MongoDBSaver
from langchain_core.messages import HumanMessage

from agents.state import ChatState
from agents.intent import classify_intent
from agents.buy import info_gatherer, query_generator, query_executor, reranker
from agents.inquery import inquiry_resolver
from agents.reject import polite_reject

load_dotenv()
connection_string = os.getenv("MDB_MCP_CONNECTION_STRING")
db_name = os.getenv("MONGODB_DATABASE", "Merchant_1")

if connection_string:
    try:
        mongodb_client = MongoClient(
            connection_string,
            maxPoolSize=10,
            connectTimeoutMS=15000,
            serverSelectionTimeoutMS=10000,
            connect=False
        )
        checkpointer = MongoDBSaver(mongodb_client, db_name=db_name)
    except Exception as err:
        print(f"Warning: Failed to connect MongoDBSaver ({err}). Falling back to InMemorySaver.")
        checkpointer = InMemorySaver()
else:
    checkpointer = InMemorySaver()


import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ShopSmartPipeline")

# Routing functions
def route_by_intent(state: ChatState) -> str:
    """Route to the correct pipeline based on classified intent."""
    intent = state.get("intent", "general")
    if intent == "buy":
        target = "info_gatherer"
    elif intent == "inquiry":
        target = "inquiry_resolver"
    else:
        target = "polite_reject"
    logger.info(f"[GRAPH ROUTER] Intent '{intent}' -> Routing to node: '{target}'")
    return target


def route_by_info(state: ChatState) -> str:
    """After info gathering, continue to query generation or stop to ask user."""
    is_complete = state.get("info_complete", False)
    target = "query_generator" if is_complete else END
    logger.info(f"[GRAPH ROUTER] info_complete={is_complete} -> Routing to node: '{target}'")
    return target


# Graph

def build_graph() -> StateGraph:
    """Construct and compile the chatbot LangGraph."""
    graph = StateGraph(ChatState)

    #nodes
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("info_gatherer", info_gatherer)
    graph.add_node("query_generator", query_generator)
    graph.add_node("query_executor", query_executor)      
    graph.add_node("reranker", reranker)
    graph.add_node("inquiry_resolver", inquiry_resolver)   
    graph.add_node("polite_reject", polite_reject)

    # edges
    graph.add_edge(START, "classify_intent")

    graph.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "info_gatherer": "info_gatherer",
            "inquiry_resolver": "inquiry_resolver",
            "polite_reject": "polite_reject",
        },
    )

    graph.add_conditional_edges(
        "info_gatherer",
        route_by_info,
        {
            "query_generator": "query_generator",
            END: END,
        },
    )

    graph.add_edge("query_generator", "query_executor")
    graph.add_edge("query_executor", "reranker")
    graph.add_edge("reranker", END)

    graph.add_edge("inquiry_resolver", END)
    graph.add_edge("polite_reject", END)

    return graph.compile(checkpointer=checkpointer)


# Public API
_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


async def run_chat(user_input: str, thread_id: str = "default") -> str:
    graph = get_graph()

    input_state = {
        "user_input": user_input,
        "messages": [HumanMessage(content=user_input)],
    }

    config = {"configurable": {"thread_id": thread_id}}

    result = await graph.ainvoke(input_state, config=config)

    return result.get("response", "I'm not sure how to respond to that.")


# CLI entry point for quick testing

async def _cli_loop():
    print("=" * 60)
    print(" Product Shopping Assistant")
    print("  Type your message or 'quit' to exit.")
    print("=" * 60)

    thread_id = "cli-session"

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye! 👋")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye! 👋")
            break

        response = await run_chat(user_input, thread_id=thread_id)
        print(f"\nAssistant: {response}")


def retrieve_all_threads() -> list[str]:
    if not connection_string:
        return []
    try:
        db = mongodb_client[db_name]
        t1 = set(db["checkpoints"].distinct("thread_id"))
        t2 = set(db["Chat_History"].distinct("thread_id"))
        all_threads = list(t1.union(t2))
        return [str(tid) for tid in all_threads if tid]
    except Exception as e:
        print(f"Error retrieving threads: {e}")
        return []


if __name__ == "__main__":
    asyncio.run(_cli_loop())