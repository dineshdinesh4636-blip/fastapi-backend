from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone, timedelta
import uuid
import json
import os
import hmac
import hashlib
import razorpay
from dotenv import load_dotenv
from bson import ObjectId
import qrcode
import requests
from fastapi.staticfiles import StaticFiles

from database import db

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Holi Event API (MongoDB)")

# ✅ CORS must be added BEFORE mounting static files
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("static/qrcodes", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

ADULT_TICKET_PRICE = 2499
KID_TICKET_PRICE = 499

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")

rzp_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


# -------------------------
# MODELS
# -------------------------

class MemberDetail(BaseModel):
    name: str
    phone: str
    type: str


class RegistrationRequest(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    gender: str
    age: int
    city: str
    adult_tickets: int
    kid_tickets: int
    members: Optional[List[MemberDetail]] = None


class AdminLogin(BaseModel):
    username: str
    password: str


class VerifyTicketRequest(BaseModel):
    ticket_id: str


class ApproveEntryRequest(BaseModel):
    ticket_id: str


class CreateOrderRequest(BaseModel):
    user_id: str


class VerifyPaymentRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    user_id: str


# -------------------------
# ROOT
# -------------------------

@app.get("/")
def root():
    return {"message": "Holi Event API Running 🚀 (MongoDB)"}


# -------------------------
# ADMIN LOGIN
# -------------------------

@app.post("/admin/login")
async def admin_login(login: AdminLogin):
    admin = db["admins"].find_one({"username": login.username})

    if not admin:
        raise HTTPException(status_code=401, detail="Invalid username")

    if admin["password_hash"] != login.password:
        raise HTTPException(status_code=401, detail="Invalid password")

    return {
        "status": "success",
        "role": admin["role"],
        "username": admin["username"]
    }


# -------------------------
# REGISTER USER
# -------------------------

@app.post("/register")
async def register_user(reg: RegistrationRequest):
    total_amount = (
        reg.adult_tickets * ADULT_TICKET_PRICE +
        reg.kid_tickets * KID_TICKET_PRICE
    )

    payment_ref = f"PAY-{uuid.uuid4().hex[:8].upper()}"

    members_json = None
    if reg.members:
        members_json = json.dumps([m.dict() for m in reg.members])

    user = {
        "name": reg.name,
        "phone": reg.phone,
        "email": reg.email,
        "gender": reg.gender,
        "age": reg.age,
        "city": reg.city,
        "adult_tickets": reg.adult_tickets,
        "kid_tickets": reg.kid_tickets,
        "members_json": members_json,
        "total_amount": total_amount,
        "payment_ref": payment_ref,
        "payment_status": "pending",
        "created_at": datetime.now(IST)
    }

    result = db["users"].insert_one(user)

    ticket_id = f"HOLI-{uuid.uuid4().hex[:8].upper()}"
    ticket = {
        "ticket_id": ticket_id,
        "name": reg.name,
        "phone": reg.phone,
        "ticket_type": f"{reg.adult_tickets}A + {reg.kid_tickets}K",
        "qr_code": ticket_id,
        "is_used": False
    }
    db["tickets"].insert_one(ticket)

    return {
        "status": "success",
        "user_id": str(result.inserted_id),
        "payment_ref": payment_ref,
        "total_amount": total_amount,
        "qr_code": ticket_id
    }


# -------------------------
# ADMIN STATS
# -------------------------

@app.get("/admin/stats")
async def get_stats():
    total = db["users"].count_documents({})
    pending = db["users"].count_documents({"payment_status": "pending"})
    approved = db["users"].count_documents({"payment_status": "approved"})
    entered = db["entries"].count_documents({"entry_status": "used"})

    return {
        "total_registrations": total,
        "pending_approvals": pending,
        "approved_registrations": approved,
        "total_entered": entered,
        "remaining_capacity": 20000 - entered
    }


# -------------------------
# GET REGISTRATIONS
# FIX: Wrapped each record in try/except to avoid one bad document
#      crashing the entire endpoint.
# -------------------------

@app.get("/admin/registrations")
async def get_registrations():
    users = list(db["users"].find().sort("created_at", -1))

    results = []

    for u in users:
        try:
            uid = str(u["_id"])
            entry = db["entries"].find_one({"user_id": uid})

            created_at = u.get("created_at")
            if created_at:
                # If naive (old UTC records), make it UTC-aware, then convert to IST
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc).astimezone(IST)
                created_at_str = created_at.strftime("%Y-%m-%dT%H:%M:%S+05:30")
            else:
                created_at_str = None

            results.append({
                "id": uid,
                "name": u.get("name"),
                "phone": u.get("phone"),
                "email": u.get("email"),
                "gender": u.get("gender"),
                "age": u.get("age"),
                "city": u.get("city"),
                "adult_tickets": u.get("adult_tickets"),
                "kid_tickets": u.get("kid_tickets"),
                "total_amount": u.get("total_amount"),
                "payment_ref": u.get("payment_ref"),
                "payment_status": u.get("payment_status"),
                "created_at": created_at_str,
                "entry_status": entry["entry_status"] if entry else None,
                "qr_code": entry["qr_code"] if entry else None
            })
        except Exception as e:
            print(f"[WARN] Skipping malformed user record: {e}")
            continue

    return results


# -------------------------
# WHATSAPP HELPER
# -------------------------

def send_whatsapp_message(phone: str, user_name: str, ticket_id: str, qr_url: str):
    token = os.getenv("WHATSAPP_TOKEN", "")
    phone_id = os.getenv("WHATSAPP_PHONE_ID", "")

    if not token or not phone_id:
        print("WhatsApp API credentials missing. Skipping message send.")
        return

    url = f"https://graph.facebook.com/v17.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    formatted_phone = phone.replace("+", "").replace("-", "").replace(" ", "")
    if len(formatted_phone) == 10:
        formatted_phone = f"91{formatted_phone}"

    caption = (
        f"Hello {user_name},\n"
        f"Your ticket for Holi Festival 2026 has been approved successfully.\n\n"
        f"Ticket ID: {ticket_id}\n"
        f"Event Date: 15 March 2026\n"
        f"Location: City Grounds, Main Arena\n\n"
        f"Please present the attached QR Code at the event entry gate for verification.\n\n"
        f"Thank you and enjoy the event!"
    )

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": formatted_phone,
        "type": "image",
        "image": {
            "link": qr_url,
            "caption": caption
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"WhatsApp API Response: {response.text}")
    except Exception as e:
        print(f"Failed to send WhatsApp message: {str(e)}")


# -------------------------
# APPROVE USER
# -------------------------

@app.post("/admin/approve/{user_id}")
async def approve_registration(user_id: str, request: Request):
    # FIX: Validate ObjectId before querying to avoid 500 on bad IDs
    try:
        obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    user = db["users"].find_one({"_id": obj_id})

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db["users"].update_one(
        {"_id": obj_id},
        {"$set": {"payment_status": "approved"}}
    )

    # Check if entry already exists to prevent duplication
    entry = db["entries"].find_one({"user_id": user_id})
    if entry:
        qr_code = entry["qr_code"]
    else:
        qr_code = f"HOLI-2026-{uuid.uuid4().hex[:8].upper()}"
        db["entries"].insert_one({
            "user_id": user_id,
            "qr_code": qr_code,
            "entry_status": "unused",
            "entry_time": None
        })

    # Generate and save QR Code image locally
    qr_img = qrcode.make(qr_code)
    qr_img.save(f"static/qrcodes/{qr_code}.png")

    base_url = str(request.base_url).rstrip('/')
    qr_url = f"{base_url}/static/qrcodes/{qr_code}.png"

    user_name = user.get("name", "Guest")
    phone = user.get("phone", "")
    send_whatsapp_message(phone, user_name, qr_code, qr_url)

    return {"status": "approved", "qr_code": qr_code}


# -------------------------
# RESEND WHATSAPP TICKET
# -------------------------

@app.post("/admin/send-whatsapp/{user_id}")
async def send_whatsapp_ticket(user_id: str, request: Request):
    try:
        obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    user = db["users"].find_one({"_id": obj_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    entry = db["entries"].find_one({"user_id": user_id})
    if not entry:
        raise HTTPException(status_code=400, detail="User has not been approved yet, no QR code exists.")

    qr_code = entry["qr_code"]
    user_name = user.get("name", "Guest")
    phone = user.get("phone", "")
    adults = user.get("adult_tickets", 0)
    kids = user.get("kid_tickets", 0)
    total_amount = user.get("total_amount", 0)

    caption = (
        f"Hello {user_name},\n"
        f"Your event ticket has been confirmed 🎉\n\n"
        f"Ticket Details:\n"
        f"Ticket ID: {qr_code}\n"
        f"Adults: {adults}\n"
        f"Kids: {kids}\n"
        f"Total Amount: ₹{total_amount}\n\n"
        f"Please show the QR code at the entry gate."
    )

    base_url = str(request.base_url).rstrip('/')
    qr_url = f"{base_url}/static/qrcodes/{qr_code}.png"

    token = os.getenv("WHATSAPP_TOKEN", "")
    phone_id = os.getenv("WHATSAPP_PHONE_ID", "")

    if not token or not phone_id:
        print("WhatsApp API credentials missing.")
        return {"status": "skipped", "message": "Credentials missing"}

    url = f"https://graph.facebook.com/v17.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    formatted_phone = phone.replace("+", "").replace("-", "").replace(" ", "")
    if len(formatted_phone) == 10:
        formatted_phone = f"91{formatted_phone}"

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": formatted_phone,
        "type": "image",
        "image": {
            "link": qr_url,
            "caption": caption
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        return {"status": "sent", "response": response.json()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------
# VERIFY ENTRY
# -------------------------

@app.get("/entry/verify/{qr_code}")
async def verify_entry(qr_code: str):
    entry = db["entries"].find_one({"qr_code": qr_code})

    if not entry:
        return {"status": "invalid"}

    try:
        user = db["users"].find_one({"_id": ObjectId(entry["user_id"])})
    except Exception:
        return {"status": "invalid"}

    if entry["entry_status"] == "used":
        return {
            "status": "used",
            "name": user["name"] if user else "Unknown"
        }

    return {
        "status": "eligible",
        "name": user["name"] if user else "Unknown",
        "entry_id": str(entry["_id"])
    }


# -------------------------
# CONFIRM ENTRY
# -------------------------

@app.post("/entry/confirm/{entry_id}")
async def confirm_entry(entry_id: str):
    try:
        obj_id = ObjectId(entry_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid entry ID format")

    db["entries"].update_one(
        {"_id": obj_id},
        {"$set": {
            "entry_status": "used",
            "entry_time": datetime.now(IST)
        }}
    )

    return {"status": "success"}


# -------------------------
# VERIFY TICKET (QR SCANNER)
# -------------------------

@app.post("/verify-ticket")
async def verify_ticket(req: VerifyTicketRequest):
    ticket = db["tickets"].find_one({"ticket_id": req.ticket_id})

    if not ticket:
        # Fallback to older `entries` format for backwards compatibility
        entry = db["entries"].find_one({"qr_code": req.ticket_id})
        if entry:
            try:
                user = db["users"].find_one({"_id": ObjectId(entry["user_id"])})
                user_name = user["name"] if user else "Unknown"
            except Exception:
                user_name = "Unknown"

            if entry.get("entry_status") == "used":
                return {"status": "already_used", "name": user_name}
            return {"status": "valid", "name": user_name}
        return {"status": "invalid"}

    if ticket.get("is_used", False):
        return {
            "status": "already_used",
            "name": ticket.get("name", "Unknown")
        }

    return {
        "status": "valid",
        "name": ticket.get("name", "Unknown")
    }


@app.post("/approve-entry")
async def approve_entry(req: ApproveEntryRequest):
    result = db["tickets"].update_one(
        {"ticket_id": req.ticket_id},
        {"$set": {"is_used": True}}
    )

    if result.matched_count == 0:
        entry = db["entries"].find_one({"qr_code": req.ticket_id})
        if entry:
            db["entries"].update_one(
                {"_id": entry["_id"]},
                {"$set": {
                    "entry_status": "used",
                    "entry_time": datetime.now(IST)
                }}
            )
            return {"status": "success"}
        return {"status": "invalid"}

    return {"status": "success"}


# -------------------------
# RAZORPAY ORDER
# -------------------------

@app.post("/payment/create-order")
async def create_order(req: CreateOrderRequest):
    try:
        obj_id = ObjectId(req.user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    user = db["users"].find_one({"_id": obj_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    amount = int(user["total_amount"]) * 100

    order = rzp_client.order.create({
        "amount": amount,
        "currency": "INR",
        "receipt": user["payment_ref"]
    })

    db["users"].update_one(
        {"_id": obj_id},
        {"$set": {"razorpay_order_id": order["id"]}}
    )

    return {
        "order_id": order["id"],
        "amount": amount,
        "key_id": RAZORPAY_KEY_ID
    }


# -------------------------
# VERIFY PAYMENT
# FIX: Changed hmac.new → hmac.new (correct: hmac.new is valid in Python 3,
#      but the real fix is ensuring RAZORPAY_KEY_SECRET is not None before encode())
# -------------------------

@app.post("/payment/verify")
async def verify_payment(req: VerifyPaymentRequest):
    if not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=500, detail="Razorpay secret not configured")

    body = f"{req.razorpay_order_id}|{req.razorpay_payment_id}"

    # FIX: Use hmac.new correctly (was already correct syntax, but added None guard above)
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, req.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    try:
        obj_id = ObjectId(req.user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    db["users"].update_one(
        {"_id": obj_id},
        {"$set": {
            "payment_status": "approved",
            "razorpay_payment_id": req.razorpay_payment_id
        }}
    )

    return {"status": "payment verified"}


# -------------------------
# DASHBOARD
# -------------------------

@app.get("/dashboard")
async def get_dashboard():
    total = db["users"].count_documents({})
    pending = db["users"].count_documents({"payment_status": "pending"})
    approved = db["users"].count_documents({"payment_status": "approved"})
    entered = db["entries"].count_documents({"entry_status": "used"})

    latest_users = list(db["users"].find().sort("created_at", -1).limit(20))
    attendees = []
    for u in latest_users:
        try:
            name = u.get("name", "")
            parts = name.strip().split()
            initials = "".join([p[0].upper() for p in parts if p]) or "?"
            attendees.append({
                "name": name,
                "initials": initials,
                "status": u.get("payment_status", "pending").upper()
            })
        except Exception as e:
            print(f"[WARN] Skipping bad attendee record: {e}")
            continue

    return {
        "totalRegistrations": total,
        "pendingApprovals": pending,
        "approvedUsers": approved,
        "peopleInside": entered,
        "maxCapacity": 20000,
        "attendees": attendees
    }