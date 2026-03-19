from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URL = os.getenv("MONGO_URL")

if not MONGO_URL:
    raise RuntimeError("❌ MONGO_URL environment variable is not set!")

client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)

db = client["holi_event"]