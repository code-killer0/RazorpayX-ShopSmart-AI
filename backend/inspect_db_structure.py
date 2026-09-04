import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
connection_string = os.getenv("MDB_MCP_CONNECTION_STRING")
db_name = os.getenv("MONGODB_DATABASE", "Merchant_1")

client = MongoClient(connection_string)
db = client[db_name]

print("Collections in DB:", db.list_collection_names())
for col_name in db.list_collection_names():
    print(f"\nCollection: {col_name}")
    docs = list(db[col_name].find().limit(2))
    for doc in docs:
        print({k: str(v) for k, v in doc.items()})
