from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()

# Get the MongoDB Atlas connection string from environment variable
MONGO_URL = os.getenv("MONGO_URL")  # No default

if not MONGO_URL:
    raise RuntimeError("❌ MONGO_URL environment variable is not set!")

# Connect to MongoDB
client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)

# Access the database
db = client["holi_event"]

# ✅ FIX: Connection is tested lazily on first real DB call.
# Removed client.admin.command("ping") from module-level —
# it ran synchronously at import time and crashed the entire
# FastAPI server before it could start if MongoDB was unreachable.