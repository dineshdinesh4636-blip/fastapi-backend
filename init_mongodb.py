"""
Seed default admins into MongoDB.
Run once: python init_mongodb.py
"""

from database import db
from datetime import datetime

def seed_admins():

    admins = db["admins"]

    defaults = [
        {"username": "admin", "password_hash": "admin2026", "role": "admin"},
        {"username": "staff1", "password_hash": "staff@1", "role": "staff"},
        {"username": "staff2", "password_hash": "staff@2", "role": "staff"},
        {"username": "staff3", "password_hash": "staff@3", "role": "staff"},
        {"username": "staff4", "password_hash": "staff@4", "role": "staff"},
        {"username": "staff5", "password_hash": "staff@5", "role": "staff"},
    ]

    for admin in defaults:

        existing = admins.find_one({"username": admin["username"]})

        if existing:
            print(f"⚠️ Admin '{admin['username']}' already exists — skipping")
            continue

        admins.insert_one({
            **admin,
            "is_active": True,
            "created_at": datetime.utcnow()
        })

        print(f"✅ Created admin: {admin['username']}")

    print("🔥 MongoDB seeding complete!")


if __name__ == "__main__":
    seed_admins()