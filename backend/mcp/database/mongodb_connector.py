import os
import json
import logging
import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from pymongo import MongoClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

_DB_THREAD_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="mongo_mcp_worker")

_CONNECTOR_CLIENT_POOL = None

def _get_pooled_client(conn_str: str) -> MongoClient:
    global _CONNECTOR_CLIENT_POOL
    if _CONNECTOR_CLIENT_POOL is None:
        _CONNECTOR_CLIENT_POOL = MongoClient(
            conn_str,
            maxPoolSize=20,
            connectTimeoutMS=15000,
            serverSelectionTimeoutMS=10000,
            connect=False
        )
    return _CONNECTOR_CLIENT_POOL

class MongoDBMCPConnector:

    def __init__(self, connection_string: Optional[str] = None):
        load_dotenv()
        self.connection_string = (
            connection_string 
            or os.getenv("MONGODB_URI") 
            or os.getenv("MDB_MCP_CONNECTION_STRING") 
            or "mongodb://localhost:27017"
        )
        self._client_ctx = None
        self._session_ctx = None
        self.session = None

    def _sync_pymongo_read(
        self,
        collection_name: str,
        filter_query: Dict[str, Any],
        projection: Dict[str, Any],
        limit: Optional[int]
    ) -> Optional[List[Dict[str, Any]]]:
        
        try:
            db_name = os.getenv("MONGODB_DATABASE") or "Merchant_1"
            client = _get_pooled_client(self.connection_string)
            db = client[db_name]
            col = db[collection_name]

            cursor = col.find(filter_query or {}, projection or None)
            if limit:
                cursor = cursor.limit(limit)

            docs = []
            for doc in cursor:
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
                docs.append(doc)

            logger.info(f"[MONGODB FAST-THREAD] Query succeeded -> returned {len(docs)} docs from '{db_name}.{collection_name}' in thread pool")
            return docs
        except Exception as err:
            logger.debug(f"[MONGODB FAST-THREAD] Threaded PyMongo query skipped: {err}")
            return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session_ctx:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception:
                pass
        if self._client_ctx:
            try:
                await self._client_ctx.__aexit__(None, None, None)
            except Exception:
                pass

    async def _ensure_stdio_session(self):
        if self.session:
            return

        logger.info("[MONGODB MCP] Initializing stdio connection to mongodb-mcp-server...")
        env = os.environ.copy()
        env["MDB_MCP_CONNECTION_STRING"] = self.connection_string
        env["MDB_MCP_READ_ONLY"] = "true"
        env["npm_config_loglevel"] = "silent"

        self._client_ctx = stdio_client(StdioServerParameters(
            command="npx",
            args=["-y", "--quiet", "mongodb-mcp-server"],
            env=env
        ))

        read, write = await self._client_ctx.__aenter__()
        self._session_ctx = ClientSession(read, write)
        self.session = await self._session_ctx.__aenter__()
        await self.session.initialize()

        self.connection_id = None
        try:
            tools_list = await self.session.list_tools()
            tool_names = [t.name for t in tools_list.tools]

            list_conn_tool = next((name for name in ["list-connections", "list_connections"] if name in tool_names), None)
            if list_conn_tool:
                try:
                    res = await self.session.call_tool(list_conn_tool, arguments={})
                    content_text = "\n".join([c.text for c in res.content if hasattr(c, "text")])
                    match = re.search(r'"([^"]+)"\s*\(connected\)', content_text)
                    if match:
                        self.connection_id = match.group(1)
                except Exception:
                    pass

            if not self.connection_id:
                connect_tool = next((name for name in ["connect", "connect-cluster", "connect_cluster"] if name in tool_names), None)
                if connect_tool:
                    try:
                        res = await self.session.call_tool(connect_tool, arguments={"connectionString": self.connection_string})
                        content_text = "\n".join([c.text for c in res.content if hasattr(c, "text")])
                        match = re.search(r'connectionId is "([^"]+)"', content_text)
                        if match:
                            self.connection_id = match.group(1)
                    except Exception:
                        pass
        except Exception as err:
            logger.warning(f"[MONGODB MCP] Exception resolving connectionId: {err}")

        logger.info(f"[MONGODB MCP] Connected successfully via stdio. connection_id='{self.connection_id}'")

    async def read(
        self,
        collection_name: str = "Products",
        filter_query: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        
        # Step 1: Execute fast-path PyMongo query in dedicated ThreadPoolExecutor
        loop = asyncio.get_running_loop()
        fast_docs = await loop.run_in_executor(
            _DB_THREAD_POOL,
            self._sync_pymongo_read,
            collection_name,
            filter_query or {},
            projection or {},
            limit
        )

        if fast_docs is not None:
            return fast_docs

        # Step 2: Fallback to official stdio MCP protocol server if fast-path is unreachable
        await self._ensure_stdio_session()

        db_name = os.getenv("MONGODB_DATABASE") or "Merchant_1"
        args = {
            "database": db_name,
            "collection": collection_name,
            "filter": filter_query or {},
            "projection": projection or {}
        }
        if self.connection_id:
            args["connectionId"] = self.connection_id
        if limit is not None:
            args["limit"] = limit

        tools_list = await self.session.list_tools()
        tool_names = [t.name for t in tools_list.tools]
        tool_name = next((name for name in ["find", "find_documents", "find-documents"] if name in tool_names), "find")

        logger.info(f"[MONGODB MCP] Executing '{tool_name}' via stdio on '{db_name}.{collection_name}' with filter: {filter_query} (limit={limit})")
        result = await self.session.call_tool(tool_name, arguments=args)

        if hasattr(result, "isError") and result.isError:
            error_msg = "\n".join([c.text for c in result.content if hasattr(c, "text")])
            logger.error(f"[MONGODB MCP] Query error: {error_msg}")
            raise RuntimeError(f"Query failed: {error_msg}")

        content_text = "\n".join([c.text for c in result.content if hasattr(c, "text")])
        parsed_data = []
        try:
            parsed_data = json.loads(content_text)
        except json.JSONDecodeError:
            if "[" in content_text and "]" in content_text:
                try:
                    start_idx = content_text.index("[")
                    end_idx = content_text.rindex("]") + 1
                    parsed_data = json.loads(content_text[start_idx:end_idx])
                except Exception:
                    pass

        count = len(parsed_data) if isinstance(parsed_data, list) else (1 if parsed_data else 0)
        logger.info(f"[MONGODB MCP] Stdio query succeeded -> returned {count} documents from '{collection_name}'")
        return parsed_data if isinstance(parsed_data, list) else ([] if not parsed_data else [parsed_data])

