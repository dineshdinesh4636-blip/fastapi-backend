from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URL = os.getenv("MONGO_URL")

if not MONGO_URL:
    raise RuntimeError("❌ MONGO_URL environment variable is not set!")

try:
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    # Force a connection test immediately
    client.admin.command("ping")
    db = client["holi_event"]
    print("🔥 MongoDB Connected Successfully")
except Exception as e:
    raise RuntimeError(f"❌ MongoDB connection failed: {e}")