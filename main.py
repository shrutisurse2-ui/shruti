import os
import json
import re
import base64
import binascii
import logging
import copy
from datetime import datetime, timezone
import secrets
import time
from typing import Any
from urllib.parse import quote

from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from openai import APIConnectionError, APIStatusError, OpenAI
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DEFAULT_ADMIN_USERNAME = "admin_1"
DEFAULT_ADMIN_PASSWORD = "Admin@123"
CUSTOMER_PASSWORDS = {
    "ORD-101": "Priya@101",
    "ORD-102": "Rahul@102",
    "ORD-103": "Ananya@103",
}

app = FastAPI(title="Aura Skincare AI Voice Agent", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
logger = logging.getLogger(__name__)
SESSION_COOKIE = "aura_demo_session"
CHECKOUT_COOKIE = "aura_demo_checkout"
SESSION_TTL_SECONDS = 8 * 60 * 60
CAPTCHA_TTL_SECONDS = 5 * 60
CHECKOUT_TTL_SECONDS = 15 * 60
MAX_TRANSCRIPTION_BYTES = 20 * 1024 * 1024
TRANSCRIPTION_RATE_WINDOW_SECONDS = 5 * 60
TRANSCRIPTION_RATE_LIMIT = 8
SESSIONS: dict[str, dict[str, Any]] = {}
CAPTCHA_CHALLENGES: dict[str, dict[str, Any]] = {}
DAMAGE_REPORTS: dict[str, dict[str, Any]] = {}
DAMAGE_REPORT_PHOTOS: dict[str, bytes] = {}
TRANSCRIPTION_REQUESTS: dict[str, list[float]] = {}
CHECKOUT_DRAFTS: dict[str, dict[str, Any]] = {}

BRAND_POLICIES = [
    {"title": "Brand Overview", "icon": "✳", "summary": "Premium organic Indian skincare.", "details": "Aura Skincare focuses on simple, effective skincare products made with thoughtfully selected ingredients."},
    {"title": "Shipping", "icon": "↗", "summary": "Standard delivery takes 3–5 business days.", "details": "Delivery is free for orders above ₹499. Orders below ₹499 have a ₹50 shipping fee."},
    {"title": "Returns & Refunds", "icon": "↺", "summary": "Returns are accepted within 7 days of delivery.", "details": "Products must be unopened, unused, and in their original packaging. For damaged or defective products, report the issue within 48 hours of delivery and provide photos to request a replacement."},
    {"title": "Cancellations", "icon": "×", "summary": "Orders can be cancelled only while their status is Processing.", "details": "Once an order is Shipped or Out for Delivery, it cannot be cancelled. Customers may refuse delivery at the doorstep."},
    {"title": "Cash on Delivery", "icon": "₹", "summary": "COD is available for orders up to ₹2,500.", "details": "Pay by cash or UPI at the doorstep."},
]

# Demo-only in-memory order database. Data resets when the server restarts.
ORDERS: dict[str, dict[str, Any]] = {
    "ORD-101": {
        "order_id": "ORD-101", "customer": "Priya Sharma",
        "product": "Vitamin C Serum (30ml)", "value_inr": 699,
        "status": "Out for Delivery", "courier": "BlueDart",
        "tracking_id": "BD-982103", "expected_delivery": "by 6 PM today",
        "delivered_days_ago": None, "delivered_hours_ago": None, "product_image": "/static/products/vitamin-c-serum.svg",
        "product_description": "A lightweight daily serum for a simple, bright-feeling skincare routine.",
        "product_size": "30 ml", "product_highlights": ["Lightweight serum texture", "For daily skincare routines"],
        "how_to_use": "Apply a few drops to clean, dry skin. Follow with moisturizer and daytime sunscreen.",
        "quantity": 1, "order_date": "October 8, 2026", "payment_method": "Prepaid",
        "estimated_total_inr": 699
    },
    "ORD-102": {
        "order_id": "ORD-102", "customer": "Rahul Verma",
        "product": "Hydrating Sunscreen SPF 50", "value_inr": 499,
        "status": "Delivered", "courier": "Delhivery",
        "tracking_id": "DL-441029", "expected_delivery": None,
        "delivered_days_ago": 14, "delivered_hours_ago": 336, "product_image": "/static/products/hydrating-sunscreen.svg",
        "product_description": "A daily-use SPF 50 sunscreen with a hydrating feel.",
        "product_size": "50 g", "product_highlights": ["SPF 50", "Hydrating daily-use formula"],
        "how_to_use": "Apply generously to exposed skin before sun exposure. Reapply as needed during the day.",
        "quantity": 1, "order_date": "September 26, 2026", "payment_method": "Cash on Delivery",
        "estimated_total_inr": 499
    },
    "ORD-103": {
        "order_id": "ORD-103", "customer": "Ananya Patel",
        "product": "Green Tea Face Wash + Toner", "value_inr": 850,
        "status": "Processing", "courier": None, "tracking_id": None,
        "expected_delivery": None, "delivered_days_ago": None, "delivered_hours_ago": None,
        "product_image": "/static/products/green-tea-set.svg",
        "product_description": "A two-step routine set with a green tea face wash and a balancing toner.",
        "product_size": "100 ml face wash + 100 ml toner",
        "product_highlights": ["Two full-size skincare essentials", "Face wash and toner routine"],
        "how_to_use": "Use the face wash on damp skin and rinse. Apply toner afterward with clean hands or a cotton pad.",
        "quantity": 1, "order_date": "October 10, 2026", "payment_method": "Prepaid",
        "estimated_total_inr": 850, "ordered_hours_ago": 3
    }
}

CUSTOMER_ORDER_IDS = {
    order["customer"].casefold(): order_id
    for order_id, order in ORDERS.items()
}
BOOKING_PRODUCTS = {
    order_id: {
        key: copy.deepcopy(order[key])
        for key in (
            "product", "value_inr", "product_description", "product_size",
            "product_highlights", "how_to_use", "product_image",
        )
    }
    for order_id, order in ORDERS.items()
}

POLICIES = """
Aura Skincare is a premium organic Indian skincare brand focused on simple, effective skincare products made with thoughtfully selected ingredients.
Shipping: free delivery on orders above INR 499. Orders below INR 499 have a INR 50 shipping fee.
The policy does not specify the fee for an order exactly INR 499; do not guess, say the policy is unclear.
Standard delivery takes 3–5 business days.
Returns: accepted within 7 days of delivery only for unopened, unused products in original packaging.
Damaged or defective products must be reported within 48 hours of delivery with photos to request a replacement.
Cancellation is permitted only while an order is Processing. Shipped or Out for Delivery orders cannot be cancelled;
customers may refuse delivery at the doorstep.
Cash on Delivery is available for orders up to INR 2,500; customers may pay by cash or UPI at the doorstep.
Never promise refunds, replacements, cancellations, or other actions unless eligibility is validated and the action succeeds.
If you do not know, say so. Only answer Aura Skincare-related questions.
"""

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[dict[str, str]] = Field(default_factory=list)
    session_order_ids: list[str] = Field(default_factory=list)

class CancelRequest(BaseModel):
    order_id: str

class DamageReportRequest(BaseModel):
    description: str = Field(min_length=10, max_length=2000)
    photo_data_url: str = Field(min_length=1, max_length=7_000_000)

class DamageResolutionRequest(BaseModel):
    choice: str = Field(pattern="^(refund|replacement)$")

class CheckoutStartRequest(BaseModel):
    customer_name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=10, max_length=10, pattern=r"^[0-9]{10}$")
    address: str = Field(min_length=10, max_length=400)
    product_id: str = Field(pattern=r"^ORD-10[123]$")

class CheckoutCompleteRequest(BaseModel):
    payer_name: str = Field(min_length=1, max_length=100)
    payment_method: str = Field(pattern=r"^(gpay|phonepe|cod)$")

class LoginRequest(BaseModel):
    role: str = Field(default="customer", pattern="^(admin|customer)$")
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(default="", max_length=200)
    captcha_id: str = Field(default="", max_length=100)
    captcha_code: str = Field(default="", max_length=12)
    order_reference: str = Field(default="", max_length=40)

class PolicyUpdate(BaseModel):
    policies: list[dict[str, str]]

def get_session(request: Request) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE, "")
    session = SESSIONS.get(token)
    if not session or session["expires_at"] <= time.time():
        SESSIONS.pop(token, None)
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return session

def valid_session(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    session = SESSIONS.get(token)
    if not session or session["expires_at"] <= time.time():
        SESSIONS.pop(token, None)
        return False
    return True

def require_admin(request: Request) -> dict[str, Any]:
    session = get_session(request)
    if session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access is required.")
    return session

def find_order_reference(reference: str) -> dict[str, Any] | None:
    normalized = reference.strip().upper()
    return next(
        (
            order for order in ORDERS.values()
            if normalized == order["order_id"].upper()
            or (order.get("tracking_id") and normalized == order["tracking_id"].upper())
        ),
        None,
    )

def session_order_list(session: dict[str, Any]) -> list[dict[str, Any]]:
    order = ORDERS.get(session["order_id"])
    return [order] if order else []

def order_scope_error(order_id: str, allowed_order_id: str) -> dict[str, Any]:
    return {
        "reply": f"I can only access the order linked to this sign-in, {allowed_order_id}. Please ask me about that order.",
        "intent": "ORDER_ACCESS",
        "order_id": allowed_order_id,
        "resolution_status": "POLICY_BLOCKED",
        "tool_used": None,
    }

def get_order_details(order_id: str) -> dict[str, Any]:
    """Required tool: safely retrieve one mock order."""
    normalized = order_id.strip().upper()
    order = ORDERS.get(normalized)
    if not order:
        return {"found": False, "order_id": normalized, "message": "No order found with that ID."}
    return {"found": True, **order}

def cancel_order(order_id: str) -> dict[str, Any]:
    """Cancel only if the current order status is Processing."""
    normalized = order_id.strip().upper()
    order = ORDERS.get(normalized)
    if not order:
        return {"success": False, "reason": "ORDER_NOT_FOUND", "message": "I couldn't find that order ID."}
    if order["status"] != "Processing":
        return {
            "success": False, "reason": "NOT_ELIGIBLE",
            "message": f"This order is {order['status']}, so it cannot be cancelled under the cancellation policy."
        }
    order["status"] = "Cancelled"
    return {"success": True, "order_id": normalized, "status": "Cancelled",
            "message": f"Order {normalized} has been cancelled successfully in this demo."}

def _is_order_tracking_query(message: str, order_id: str | None) -> bool:
    """Recognize order questions without treating every signed-in message as tracking."""
    msg = message.lower()
    tracking_patterns = [
        "track my order",
        "track order",
        "track this order",
        "my order",
        "my package",
        "my parcel",
        "where is my order",
        "where is my order now",
        "my order location",
        "where is the order",
        "status of my order",
        "order status",
        "check my order",
        "check the order",
        "when will my order arrive",
        "when will my order come",
        "how many days",
        "delivery date",
        "delivery time for my order",
        "when will it arrive",
        "when will it come",
        "when can i expect",
        "where is my package",
        "where is my parcel",
        "my order id",
        "tracking number",
        "tracking id",
        "tracking location",
        "location of my order",
        "location of that",
        "can i track my location",
        "where is it now",
        "where is it coming from",
        "courier",
    ]
    return bool(order_id and re.search(r"\b(?:ord[- ]?\d{3}|[a-z]{2}-\d{6})\b", msg, flags=re.I)) or any(
        pattern in msg for pattern in tracking_patterns
    )


PRODUCT_ALIASES = {
    "ORD-101": ("vitamin c serum", "vitamin c", "serum"),
    "ORD-102": ("hydrating sunscreen", "sunscreen", "spf 50", "spf"),
    "ORD-103": ("green tea face wash", "green tea set", "green tea", "face wash and toner", "face wash", "facewash", "toner"),
}


def product_support_reply(
    message: str,
    allowed_order_id: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Answer product questions from catalog facts without inventing product claims."""
    msg = message.casefold()
    policy_or_order_terms = (
        "track", "tracking", "order status", "my order", "where is my",
        "cancel", "cancellation", "return", "refund", "shipping", "delivery",
        "cash on delivery", "cod",
    )
    if any(term in msg for term in policy_or_order_terms):
        return None

    product_question_terms = (
        "product", "products", "skincare", "serum", "sunscreen", "spf",
        "face wash", "facewash", "toner", "green tea", "vitamin c",
        "ingredient", "benefit", "skin type", "use", "apply", "take",
        "pregnan", "routine", "face", "skin",
    )
    matched_ids = [
        order_id for order_id, aliases in PRODUCT_ALIASES.items()
        if any(alias in msg for alias in aliases)
    ]
    if not matched_ids and history:
        for item in reversed(history[-10:]):
            if item.get("role") != "assistant":
                continue
            previous_text = item.get("content", "").casefold()
            matched_ids = [
                order_id for order_id, aliases in PRODUCT_ALIASES.items()
                if any(alias in previous_text for alias in aliases)
            ]
            if matched_ids:
                break
    if not matched_ids and any(term in msg for term in ("all products", "every product", "each product", "whole range", "all three", "all 3", "each of these")):
        matched_ids = list(PRODUCT_ALIASES)

    is_product_question = bool(matched_ids) or any(
        term in msg for term in product_question_terms
    )
    if not is_product_question:
        return None

    if not matched_ids:
        faq = general_brand_faq(message)
        if faq:
            return {**faq, "order_id": None}

    if not matched_ids and allowed_order_id and any(
        term in msg for term in ("price", "cost", "how much", "benefit", "benefits", "what is it", "tell me about")
    ):
        matched_ids = [allowed_order_id]
    if not matched_ids and allowed_order_id is None:
        reply = (
            "Aura offers a Vitamin C Serum, Hydrating Sunscreen SPF 50, and Green Tea Face Wash + Toner. "
            "Which one would you like to know about?"
        )
        return {
            "reply": reply, "intent": "PRODUCT_SUPPORT", "order_id": None,
            "resolution_status": "NEEDS_FOLLOW_UP", "tool_used": None,
        }

    if len(matched_ids) > 1:
        products = [ORDERS[order_id] for order_id in matched_ids]
        if any(term in msg for term in ("ingredient", "fragrance", "scent", "formula", "what's in", "what is in", "contain", "pregnan", "side effect", "allergy", "allergic")):
            reply = "I don't have verified ingredient or medical-suitability details for those products. Please check each product's packaging; for pregnancy, allergies, or a skin condition, consult a healthcare professional."
        elif any(term in msg for term in ("price", "prices", "cost", "how much", "₹")) and any(term in msg for term in ("benefit", "benefits", "good for", "what does", "purpose", "help with", "why use")):
            reply = "Listed prices and product highlights (not medical claims): " + " ".join(
                f"{product['product']} — ₹{product['value_inr']}. {product['product_description']} Highlights: {'; '.join(product['product_highlights'])}."
                for product in products
            )
        elif any(term in msg for term in ("price", "prices", "cost", "how much", "₹")):
            reply = "Listed prices: " + "; ".join(
                f"{product['product']} — ₹{product['value_inr']}" for product in products
            ) + "."
        elif any(term in msg for term in ("size", "volume", "how many ml", "how many g", "grams")):
            reply = "Listed sizes: " + "; ".join(
                f"{product['product']} — {product['product_size']}" for product in products
            ) + "."
        elif any(term in msg for term in ("benefit", "benefits", "good for", "what does", "purpose", "help with", "why use")):
            reply = "Here are the listed product details and highlights (not medical claims): " + " ".join(
                f"{product['product']}: {product['product_description']} Highlights: {'; '.join(product['product_highlights'])}."
                for product in products
            )
        elif any(term in msg for term in ("use", "apply", "routine", "how do i")):
            reply = "How to use them: " + " ".join(
                f"{product['product']}: {product['how_to_use']}"
                for product in products
            )
        elif any(term in msg for term in ("together", "combine", "same routine", "at the same time")):
            reply = "I can't verify ingredient compatibility between these specific products. Follow each product's packaging directions; the listed Vitamin C Serum directions say to follow with moisturizer and daytime sunscreen."
        else:
            reply = " ".join(
                f"{product['product']} ({product['product_size']}, ₹{product['value_inr']}): "
                f"{product['product_description']} Listed highlights: {'; '.join(product['product_highlights'])}. "
                f"How to use: {product['how_to_use']}"
                for product in products
            )
    else:
        order_id = matched_ids[0] if matched_ids else allowed_order_id
        if order_id is None:
            return None
        product = ORDERS[order_id]
        highlights = "; ".join(product["product_highlights"])
        if any(term in msg for term in ("ingredient", "fragrance", "scent", "formula", "made of", "what's in", "what is in",
        "what's inside", "what is inside", "contain", "ph level", "percentage", "concentration",
        "paraben", "sulfate", "alcohol", "niacinamide", "hyaluronic", "retinol", "vitamin e",
        "clinical", "dermatologist tested", "vegan", "cruelty-free", "pregnan", "comedogenic",
            "side effect", "expiry", "shelf life", "waterproof",
        )):
            if any(term in msg for term in ("pregnan", "side effect", "allergy", "allergic", "irritat", "eczema", "rosacea")):
                reply = (
                    f"I can't confirm whether {product['product']} is suitable during pregnancy or for a medical or allergy-related concern. "
                    "Please check the product packaging and consult a qualified healthcare professional before use."
                )
            else:
                reply = (
                    f"I don't have verified details for that question about {product['product']}. "
                    f"What I can confirm is: {product['product_description']}"
                )
        elif any(term in msg for term in (
            "oily skin", "dry skin", "sensitive skin", "skin type", "safe for", "suitable for",
            "allergy", "allergic", "irritat", "acne", "eczema", "rosacea", "wrinkle",
            "pigment", "dark spot", "pores",
        )):
            reply = (
                f"I can't confirm skin-type suitability or allergy safety for {product['product']} from the available product information. "
                f"The listed details are: {product['product_description']} For personal skin concerns, check the product packaging or consult a dermatologist."
            )
        elif any(term in msg for term in ("texture", "feel like", "consistency")):
            reply = f"The available description for {product['product']} is: {product['product_description']}"
        elif any(term in msg for term in ("price", "cost", "how much", "rupee", "₹")) and any(term in msg for term in ("benefit", "benefits", "good for", "what does", "purpose", "help with", "why use")):
            reply = (
                f"{product['product']} is listed at ₹{product['value_inr']}. "
                f"{product['product_description']} Listed highlights: {highlights}."
            )
        elif any(term in msg for term in ("price", "cost", "how much", "rupee", "₹")):
            reply = f"{product['product']} is listed at ₹{product['value_inr']}."
        elif any(term in msg for term in ("size", "volume", "how big", "how many ml", "how many g", "grams")):
            reply = f"{product['product']} comes in {product['product_size']}."
        elif any(term in msg for term in ("how", "apply", "use", "take", "morning", "night", "daily", "every day", "each day", "routine")):
            reply = f"How to use {product['product']}: {product['how_to_use']}"
            if any(term in msg for term in ("daily", "every day", "each day", "morning", "night", "how often", "frequency")):
                reply += " The product information doesn't specify a separate use frequency."
        elif any(term in msg for term in ("benefit", "good for", "what does", "what is it for", "purpose", "work", "help with", "why use")) or re.search(r"\bwhat (?:is|does).+\bfor\b", msg):
            reply = f"{product['product']}: {product['product_description']} Listed highlights: {highlights}."
        else:
            reply = (
                f"{product['product']} ({product['product_size']}): {product['product_description']} "
                f"Listed highlights: {highlights}. How to use: {product['how_to_use']}"
            )

    return {
        "reply": reply, "intent": "PRODUCT_SUPPORT", "order_id": matched_ids[0] if len(matched_ids) == 1 else None,
        "resolution_status": "RESOLVED", "tool_used": "product_catalog",
    }


def local_fallback(
    message: str,
    allowed_order_id: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Deterministic no-key fallback so UI and policy examples can be tested."""
    msg = message.lower()
    references = re.findall(r"ord[- ]?\d{3}|[A-Z]{2}-\d{6}", message, flags=re.I)
    referenced_order = find_order_reference(references[0]) if references else None
    if referenced_order and referenced_order["order_id"] != allowed_order_id:
        return order_scope_error(referenced_order["order_id"], allowed_order_id)
    explicit_order_id = referenced_order["order_id"] if referenced_order else None
    order_id = explicit_order_id or (
        references[0].replace(" ", "-").upper() if references else allowed_order_id
    )

    if any(w in msg for w in ["flight", "hotel booking", "book a flight", "weather", "stock price", "cricket score", "recipe", "politics"]):
        return {"reply": "I'm Aria from Aura Skincare. I can help with Aura Skincare orders, delivery, returns, cancellations, and product support. I can't help with that request.", "intent": "OUT_OF_SCOPE", "order_id": order_id, "resolution_status": "OUT_OF_SCOPE", "tool_used": None}
    order_tracking_query = _is_order_tracking_query(msg, explicit_order_id)
    asks_cancel_policy = any(term in msg for term in ("cancellation policy", "cancel policy", "cancellation process", "how do i cancel", "how can i cancel", "can i cancel", "eligible to cancel", "when can i cancel", "what if i cancel", "want to cancel", "if i want to cancel"))
    if any(w in msg for w in ["cancel", "cancellation"]):
        if asks_cancel_policy:
            order = ORDERS.get(order_id)
            if order and order["status"] == "Processing":
                reply = f"Your order is Processing and is currently eligible for cancellation. To submit the cancellation, use the Cancel Order button in your signed-in order dashboard. This policy check has not cancelled it."
            elif order:
                reply = f"Your order is {order['status']}, so customer cancellation is not available. Orders can be cancelled only while Processing; for shipped or out-for-delivery orders, you may refuse delivery at the doorstep."
            else:
                reply = "Customers can cancel an order only while its status is Processing. Once Shipped or Out for Delivery, it cannot be cancelled; you may refuse delivery at the doorstep. Sign in and open your order dashboard to check your order."
            return {"reply": reply, "intent": "CANCELLATION_REQUEST", "order_id": order_id, "resolution_status": "RESOLVED", "tool_used": "get_order_details" if order else None}
        if not order_id:
            return {"reply": "I can check cancellation eligibility. Please sign in with your Aura customer account and open your order dashboard. Orders can be cancelled only while Processing.", "intent": "CANCELLATION_REQUEST", "order_id": None, "resolution_status": "NEEDS_SIGN_IN", "tool_used": None}
        result = cancel_order(order_id)
        status = "RESOLVED" if result["success"] else ("UNRESOLVED" if result["reason"] == "ORDER_NOT_FOUND" else "POLICY_BLOCKED")
        return {"reply": result["message"], "intent": "CANCELLATION_REQUEST", "order_id": order_id, "resolution_status": status, "tool_used": "cancel_order"}
    if any(w in msg for w in ["return", "refund", "opened", "damaged", "defective", "broken", "leaking", "replacement"]):
        if any(term in msg for term in ("damaged", "defective", "broken", "leaking", "replacement")):
            order = ORDERS.get(order_id)
            if order and (order["status"] != "Delivered" or order["delivered_hours_ago"] is None):
                reply = f"To report damage, the order must be marked Delivered and reported within 48 hours with a photo. This order is currently {order['status'].lower()}, so it isn't eligible for a damage report yet."
            elif order and order["delivered_hours_ago"] > 48:
                reply = f"This order was delivered {order['delivered_days_ago']} days ago, outside the 48-hour damage-report window. For an eligible delivered order, sign in, submit a damage description and photo within 48 hours, then choose a refund or replacement review."
            elif order and order_id in DAMAGE_REPORTS:
                reply = f"A damage report for this order is already recorded with status: {DAMAGE_REPORTS[order_id]['status']}."
            elif order:
                reply = "This delivered order is within the 48-hour report window. Sign in to your order dashboard, describe the damage, upload a PNG, JPG, or WebP photo under 5 MB, then choose a refund or replacement review. This demo records your choice; it does not issue a real refund or ship a replacement."
            else:
                reply = "Report a damaged or defective product within 48 hours of delivery by signing in, describing the damage, and uploading a PNG, JPG, or WebP photo under 5 MB. Then choose a refund or replacement review. This demo records the choice but does not issue a real refund or ship a replacement."
            intent = "DAMAGED_PRODUCT"
        else:
            order = ORDERS.get(order_id)
            days_ago = re.search(r"\b(\d+)\s+days?\s+ago\b", msg)
            stated_days_outside_window = bool(days_ago and int(days_ago.group(1)) > 7)
            order_days_outside_window = bool(
                order
                and order.get("delivered_days_ago") is not None
                and order["delivered_days_ago"] > 7
            )
            says_opened_or_used = bool(re.search(r"\b(?:opened|used|tried)\b", msg)) and not bool(
                re.search(r"\b(?:not|never|haven't|hasn't|have not|has not)\s+(?:been\s+)?(?:opened|used|tried)\b", msg)
            )
            if order and order["status"] != "Delivered":
                reply = f"This order is currently {order['status'].lower()}. The 7-day return period starts after delivery; returns are then accepted only for unopened, unused products in their original packaging."
            elif stated_days_outside_window or order_days_outside_window or says_opened_or_used:
                reasons = []
                if stated_days_outside_window or order_days_outside_window:
                    reasons.append("returns must be requested within 7 days of delivery")
                if says_opened_or_used:
                    reasons.append("products must be unopened and unused in their original packaging")
                reason_text = " and ".join(reasons)
                reply = f"I'm sorry, but this item isn't eligible for return because {reason_text}. I can't promise a refund outside the policy."
            else:
                reply = "Returns are accepted within 7 days of delivery only for unopened, unused products in their original packaging. Could you share your order ID and confirm whether the product is unopened and unused?"
            intent = "RETURN_REQUEST"
        blocked = intent == "RETURN_REQUEST" and (
            "isn't eligible" in reply
        )
        return {"reply": reply, "intent": intent, "order_id": order_id, "resolution_status": "RESOLVED" if intent == "DAMAGED_PRODUCT" else ("POLICY_BLOCKED" if blocked else "NEEDS_FOLLOW_UP"), "tool_used": None}
    if not order_tracking_query and any(w in msg for w in ["cod", "cash on delivery", "upi at the doorstep", "pay by upi", "pay with upi"]):
        return {"reply": "Cash on Delivery is available for orders up to ₹2,500. You can pay by cash or UPI at the doorstep.", "intent": "COD_QUERY", "order_id": order_id, "resolution_status": "RESOLVED", "tool_used": None}
    if not order_tracking_query and any(w in msg for w in ["shipping", "shipping fee", "delivery fee", "delivery charges", "delivery time", "delivery takes", "how long", "in how many days", "when will it arrive", "when will it come", "free delivery", "delivery free", "free shipping", "ship free", "deliver for free"]):
        return {"reply": "Standard delivery takes 3 to 5 business days. Delivery is free for orders above ₹499, and orders below ₹499 have a ₹50 shipping fee. The policy doesn't specify the fee for an order exactly ₹499, so I don't want to guess.", "intent": "SHIPPING_POLICY", "order_id": order_id, "resolution_status": "RESOLVED", "tool_used": None}
    product_reply = product_support_reply(message, allowed_order_id, history)
    if product_reply:
        return product_reply
    if order_tracking_query:
        if not order_id:
            return {"reply": "I'd be happy to check that. Could you please tell me your Aura Skincare order ID, such as ORD-101?", "intent": "ORDER_TRACKING", "order_id": None, "resolution_status": "NEEDS_FOLLOW_UP", "tool_used": None}
        order = get_order_details(order_id)
        if not order["found"]:
            return {"reply": f"I couldn't locate an order with ID {order_id}. Could you please repeat or verify the ID?", "intent": "ORDER_TRACKING", "order_id": order_id, "resolution_status": "UNRESOLVED", "tool_used": "get_order_details"}
        if any(term in msg for term in ("where from", "shipped from", "ship from", "warehouse", "origin", "delivery address", "deliver to me", "location", "where is it now")):
            if order["courier"] and order["tracking_id"]:
                reply = f"I don't have live GPS or a precise delivery address for {order_id}. The latest demo status is {order['status']} with {order['courier']}; use tracking ID {order['tracking_id']} with the courier for location updates."
                if order["expected_delivery"]:
                    reply += f" The order is expected {order['expected_delivery']}."
            else:
                reply = f"I don't have a live location for {order_id}; its status is {order['status'].lower()} and no courier or tracking ID has been assigned yet."
        elif any(term in msg for term in ("payment", "paid", "pay by", "payment method")):
            reply = f"Your order's payment method is {order['payment_method']}. The recorded order total is ₹{order['estimated_total_inr']}."
        elif any(term in msg for term in ("price", "cost", "total", "how much", "value")):
            reply = f"The recorded total for your {order['product']} order is ₹{order['estimated_total_inr']}."
        elif order["status"] == "Out for Delivery":
            reply = f"Your {order['product']} is out for delivery with {order['courier']}. It is expected {order['expected_delivery']}. Your tracking ID is {order['tracking_id']}."
        elif order["status"] == "Delivered":
            delivered_days = order["delivered_days_ago"]
            delivery_age = f"{delivered_days} days ago" if delivered_days is not None else "already"
            reply = f"Your {order['product']} order was delivered {delivery_age} through {order['courier']}. The tracking ID is {order['tracking_id']}."
        else:
            reply = f"Your {order['product']} order is currently {order['status'].lower()} and was placed on {order['order_date']}. Standard delivery takes 3–5 business days, but no exact delivery estimate, courier, or tracking ID has been recorded yet."
        return {"reply": reply, "intent": "ORDER_TRACKING", "order_id": order_id, "resolution_status": "RESOLVED", "tool_used": "get_order_details"}
    faq = general_brand_faq(message)
    if faq:
        return {**faq, "order_id": order_id}
    if any(w in msg for w in ["hello", "hi", "hey", "good morning"]):
        return {"reply": "Hi! I'm Aria from Aura Skincare. How can I help you with your order or skincare support today?", "intent": "GENERAL_BRAND_QUERY", "order_id": order_id, "resolution_status": "NEEDS_FOLLOW_UP", "tool_used": None}
    return {"reply": "I can help with Aura Skincare orders, shipping, returns, cancellations, damaged products, and Cash on Delivery. Could you tell me a little more about what you need?", "intent": "UNKNOWN", "order_id": order_id, "resolution_status": "NEEDS_FOLLOW_UP", "tool_used": None}


def general_brand_faq(message: str) -> dict[str, Any] | None:
    """Answer common Aura questions using only the brand and policy facts in this demo."""
    msg = message.casefold()
    if any(term in msg for term in ("what products", "which products", "product range", "what do you sell", "catalog", "all products", "list products")):
        reply = (
            "Aura's sample range has three products: Vitamin C Serum (30 ml) for ₹699, "
            "Hydrating Sunscreen SPF 50 (50 g) for ₹499, and Green Tea Face Wash + Toner "
            "(100 ml each) for ₹850. Which product would you like to know more about?"
        )
        return {"reply": reply, "intent": "PRODUCT_SUPPORT", "resolution_status": "NEEDS_FOLLOW_UP", "tool_used": "product_catalog"}
    if any(term in msg for term in ("price", "prices", "how much", "cost of all", "price list", "cost list")):
        reply = (
            "The sample prices are: Vitamin C Serum ₹699, Hydrating Sunscreen SPF 50 ₹499, "
            "and Green Tea Face Wash + Toner ₹850. Which one are you interested in?"
        )
        return {"reply": reply, "intent": "PRODUCT_SUPPORT", "resolution_status": "NEEDS_FOLLOW_UP", "tool_used": "product_catalog"}
    if any(term in msg for term in ("who are you", "what is aura", "about aura", "about your brand", "tell me about aura", "brand overview")):
        return {
            "reply": "Aura Skincare is a premium organic Indian skincare brand focused on simple, effective products made with thoughtfully selected ingredients.",
            "intent": "GENERAL_BRAND_QUERY", "resolution_status": "RESOLVED", "tool_used": "brand_information",
        }
    if any(term in msg for term in ("policies", "policy details", "all policies", "terms and conditions", "what are your rules")):
        return {
            "reply": (
                "Aura's demo policies: Standard delivery takes 3–5 business days; delivery is free above ₹499, "
                "and orders below ₹499 have a ₹50 fee (the fee at exactly ₹499 is unspecified). Returns are accepted "
                "within 7 days of delivery only for unopened, unused products in original packaging. Report damage "
                "within 48 hours with a photo to request a review; this demo records your refund or replacement choice "
                "but does not issue a real refund or ship a replacement. Customers can cancel only while an order is "
                "Processing; for shipped or out-for-delivery orders, refuse delivery at the doorstep. COD is available "
                "up to ₹2,500, payable by cash or UPI at the door."
            ),
            "intent": "GENERAL_BRAND_QUERY", "resolution_status": "RESOLVED", "tool_used": "brand_policies",
        }
    if any(term in msg for term in ("contact", "phone number", "email address", "customer care number", "customer service number", "contact number")):
        return {
            "reply": "I don't have a verified Aura customer-care phone number or email in this demo, so I don't want to give you an incorrect contact. I can still help with product details, your order, or our policies.",
            "intent": "GENERAL_BRAND_QUERY", "resolution_status": "NEEDS_FOLLOW_UP", "tool_used": None,
        }
    if any(term in msg for term in ("international", "outside india", "which cities", "where do you deliver", "delivery areas", "serviceable areas", "ship to", "deliver to", "delivery in", "shipping to")) or re.search(r"\b(?:deliver|delivery|ship|shipping)\s+(?:in|to|at)\s+[a-z]", msg):
        location = re.search(r"\b(?:deliver|delivery|ship|shipping)\s+(?:in|to|at)\s+([a-z][a-z -]{0,30}?)(?:[?.!,]|$)", msg)
        location_text = f" to {location.group(1).strip().title()}" if location else ""
        return {
            "reply": f"I can't confirm whether Aura delivers{location_text}; the service-area list isn't included in the details I have. Standard delivery is listed as 3–5 business days. Please check with Aura's team to confirm your location.",
            "intent": "SHIPPING_POLICY", "resolution_status": "NEEDS_FOLLOW_UP", "tool_used": None,
        }
    if any(term in msg for term in ("payment method", "payment options", "how can i pay", "ways to pay")):
        return {
            "reply": "Cash on Delivery is available for orders up to ₹2,500. At the doorstep, you can pay by cash or UPI. I don't have information about other payment methods.",
            "intent": "COD_QUERY", "resolution_status": "RESOLVED", "tool_used": "brand_policies",
        }
    if any(term in msg for term in ("damaged", "defective", "broken", "leaking")):
        return {
            "reply": "Report damage within 48 hours of delivery by signing in, describing the issue, and uploading a PNG, JPG, or WebP photo under 5 MB. Then choose a refund or replacement review. This demo records the choice but does not issue a real refund or ship a replacement.",
            "intent": "DAMAGED_PRODUCT", "resolution_status": "RESOLVED", "tool_used": "brand_policies",
        }
    if any(term in msg for term in ("what can you do", "what can i ask", "how can you help", "help me with")):
        return {
            "reply": "I can help with Aura product details and usage, shipping fees and delivery timing, return and damaged-item policies, Cash on Delivery, and sample-order tracking. Sign in for private order support.",
            "intent": "GENERAL_BRAND_QUERY", "resolution_status": "RESOLVED", "tool_used": None,
        }
    return None


def run_ai(message: str, history: list[dict[str, str]], allowed_order_id: str) -> dict[str, Any]:
    references = re.findall(r"ord[- ]?\d{3}|[A-Z]{2}-\d{6}", message, flags=re.I)
    referenced_order = find_order_reference(references[0]) if references else None
    if referenced_order and referenced_order["order_id"] != allowed_order_id:
        return order_scope_error(referenced_order["order_id"], allowed_order_id)

    product_reply = product_support_reply(message, allowed_order_id, history)
    if product_reply:
        product_reply["mode"] = "catalog"
        return product_reply

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        result = local_fallback(message, allowed_order_id, history)
        result["mode"] = "fallback"
        return result

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        tools = [
            {"type": "function", "function": {
                "name": "get_order_details",
                "description": "Retrieve the exact details of an Aura Skincare order. Use when a customer asks about an order.",
                "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"], "additionalProperties": False}
            }},
            {"type": "function", "function": {
                "name": "cancel_order",
                "description": "Attempt cancellation only after a customer clearly requests it. Backend checks status and cancels only Processing orders.",
                "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"], "additionalProperties": False}
            }}
        ]
        messages = [
            {"role": "system", "content": (
                "You are Aria, a friendly, professional, concise Indian customer support specialist for Aura Skincare. "
                f"This signed-in customer is authorized only for order {allowed_order_id}. "
                "Never disclose or act on another customer's order. "
                "Speak naturally and keep replies brief for voice. Follow these policies exactly:\n" + POLICIES +
                "\nUse tools for order-specific questions. Never invent order data. Use the linked order ID when an order-specific question omits an ID. "
                "For cancellation, call cancel_order only after an explicit request and report success only if the tool says success. "
                "If the order ID is unknown, ask the customer to verify it. Stay within Aura Skincare support."
            )}
        ]
        for item in history[-10:]:
            if item.get("role") in ("user", "assistant") and item.get("content"):
                messages.append({"role": item["role"], "content": item["content"][:2000]})
        messages.append({"role": "user", "content": message})

        tool_used = []
        for _ in range(3):
            response = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.3
            )
            msg = response.choices[0].message
            if not msg.tool_calls:
                text = msg.content or "I'm sorry, I couldn't form a response. Could you please repeat that?"
                intent, order_id, status = infer_metadata(message, text)
                return {"reply": text, "intent": intent, "order_id": order_id, "resolution_status": status,
                        "tool_used": tool_used or None, "mode": "ai"}
            messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
                {"id": t.id, "type": "function", "function": {"name": t.function.name, "arguments": t.function.arguments}}
                for t in msg.tool_calls
            ]})
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                    if call.function.name == "get_order_details":
                        requested_order_id = args.get("order_id", "").strip().upper()
                        result = get_order_details(requested_order_id) if requested_order_id == allowed_order_id else {
                            "found": False, "message": "Order not found in this signed-in account."
                        }
                    elif call.function.name == "cancel_order":
                        requested_order_id = args.get("order_id", "").strip().upper()
                        result = cancel_order(requested_order_id) if requested_order_id == allowed_order_id else {
                            "success": False, "reason": "ORDER_ACCESS", "message": "Order not found in this signed-in account."
                        }
                    else:
                        result = {"error": "Unknown tool"}
                    tool_used.append(call.function.name)
                except Exception:
                    result = {"error": "Tool failed safely"}
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})
        return {"reply": "I'm sorry, I couldn't complete that request. Could you please try again?", "intent": "UNKNOWN", "order_id": None, "resolution_status": "UNRESOLVED", "tool_used": tool_used or None, "mode": "ai"}
    except Exception as exc:
        # Do not expose provider details or secrets to the browser.
        result = local_fallback(message, allowed_order_id, history)
        result["mode"] = "fallback"
        result["notice"] = "AI provider unavailable; deterministic fallback used."
        return result

def infer_metadata(message: str, reply: str):
    low = message.lower()
    match = re.search(r"ord[- ]?\d{3}", message, flags=re.I)
    order_id = match.group(0).replace(" ", "-").upper() if match else None
    if any(x in low for x in ["cancel"]):
        intent = "CANCELLATION_REQUEST"
    elif any(x in low for x in ["return", "refund", "opened"]):
        intent = "RETURN_REQUEST"
    elif any(x in low for x in ["damaged", "defective", "broken"]):
        intent = "DAMAGED_PRODUCT"
    elif any(x in low for x in ["cod", "cash on delivery", "upi"]):
        intent = "COD_QUERY"
    elif any(x in low for x in ["product", "how to use", "ingredients", "skin type"]):
        intent = "PRODUCT_SUPPORT"
    elif any(x in low for x in ["shipping fee", "shipping", "delivery time", "how long", "delivery takes"]):
        intent = "SHIPPING_POLICY"
    elif any(x in low for x in ["track", "order", "where is", "delivery", "status"]):
        intent = "ORDER_TRACKING"
    else:
        intent = "GENERAL_BRAND_QUERY"
    status = "RESOLVED" if order_id or intent in ("COD_QUERY", "SHIPPING_POLICY", "GENERAL_BRAND_QUERY") else "NEEDS_FOLLOW_UP"
    return intent, order_id, status

@app.get("/")
def home():
    return FileResponse("static/index.html", headers={"Cache-Control": "no-store"})

@app.get("/login")
def login_page():
    return FileResponse("static/login.html")

@app.get("/book")
def booking_page():
    return FileResponse("static/booking.html")

@app.get("/payment")
def payment_page():
    return FileResponse("static/payment.html")

@app.get("/voice")
def voice_page():
    return FileResponse("static/voice.html")

@app.get("/orders")
def orders_page(request: Request):
    if not valid_session(request):
        return RedirectResponse("/login?next=/orders", status_code=303)
    session = SESSIONS[request.cookies[SESSION_COOKIE]]
    if session.get("role") == "admin":
        return RedirectResponse("/admin", status_code=303)
    return FileResponse("static/orders.html")

@app.get("/admin")
def admin_page(request: Request):
    if not valid_session(request):
        return RedirectResponse("/login?next=/admin", status_code=303)
    session = SESSIONS[request.cookies[SESSION_COOKIE]]
    if session.get("role") != "admin":
        return RedirectResponse("/orders", status_code=303)
    return FileResponse("static/admin.html")

@app.get("/policies")
def policies_page():
    return FileResponse("static/policies.html")

def checkout_shipping_note(amount: int) -> str:
    if amount == 499:
        return "The sample shipping policy does not specify the fee for an order of exactly ₹499."
    if amount > 499:
        return "Free standard delivery (3–5 business days)."
    return "A ₹50 shipping fee applies to orders below ₹499."

def checkout_draft_from_request(request: Request) -> dict[str, Any]:
    token = request.cookies.get(CHECKOUT_COOKIE, "")
    draft = CHECKOUT_DRAFTS.get(token)
    if not draft or draft["expires_at"] <= time.time():
        CHECKOUT_DRAFTS.pop(token, None)
        raise HTTPException(status_code=401, detail="Your checkout expired. Please start the booking again.")
    return draft

@app.post("/api/checkout/start")
def start_checkout(request: CheckoutStartRequest, response: Response, http_request: Request):
    customer_name = " ".join(request.customer_name.split())
    order_id = CUSTOMER_ORDER_IDS.get(customer_name.casefold())
    if not order_id:
        raise HTTPException(
            status_code=403,
            detail="Bookings are limited to the three sample customers: Priya Sharma, Rahul Verma, and Ananya Patel.",
        )
    if valid_session(http_request):
        session = SESSIONS[http_request.cookies[SESSION_COOKIE]]
        if session.get("role") != "customer" or session.get("order_id") != order_id:
            raise HTTPException(status_code=403, detail="Sign out or use the customer name linked to your current sign-in.")
    product_id = request.product_id.strip().upper()
    product = BOOKING_PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Choose one of the three Aura products shown on the home page.")
    if order_id != product_id:
        assigned_customer = ORDERS[product_id]["customer"]
        raise HTTPException(
            status_code=403,
            detail=f"{product['product']} is assigned to {assigned_customer} ({product_id}) in the sample data. Enter {assigned_customer} to continue.",
        )

    now = time.time()
    for token, draft in list(CHECKOUT_DRAFTS.items()):
        if draft["expires_at"] <= now:
            CHECKOUT_DRAFTS.pop(token, None)
    token = secrets.token_urlsafe(32)
    CHECKOUT_DRAFTS[token] = {
        "customer_name": ORDERS[order_id]["customer"],
        "order_id": order_id,
        "product_id": product_id,
        "product": copy.deepcopy(product),
        "phone": request.phone.strip(),
        "address": " ".join(request.address.split()),
        "expires_at": now + CHECKOUT_TTL_SECONDS,
    }
    response.set_cookie(
        CHECKOUT_COOKIE,
        token,
        max_age=CHECKOUT_TTL_SECONDS,
        httponly=True,
        secure=http_request.url.scheme == "https",
        samesite="lax",
    )
    return {
        "order_id": order_id,
        "customer": ORDERS[order_id]["customer"],
        "product": product["product"],
        "product_id": product_id,
        "product_image": product["product_image"],
        "product_size": product["product_size"],
        "price_inr": product["value_inr"],
        "shipping_note": checkout_shipping_note(product["value_inr"]),
        "delivery_estimate": "3–5 business days after booking",
        "expires_in": CHECKOUT_TTL_SECONDS,
    }

@app.get("/api/booking/products")
def booking_products():
    return {
        "products": [
            {
                "product_id": order_id,
                "customer": ORDERS[order_id]["customer"],
                "product": product["product"],
                "product_image": product["product_image"],
                "product_size": product["product_size"],
                "price_inr": product["value_inr"],
                "description": product["product_description"],
                "delivery_estimate": "3–5 business days after booking",
                "shipping_note": checkout_shipping_note(product["value_inr"]),
            }
            for order_id, product in BOOKING_PRODUCTS.items()
        ]
    }

@app.get("/api/checkout/draft")
def checkout_draft(request: Request):
    draft = checkout_draft_from_request(request)
    product = draft["product"]
    return {
        "order_id": draft["order_id"],
        "customer": draft["customer_name"],
        "phone": draft["phone"],
        "address": draft["address"],
        "product": product["product"],
        "product_id": draft["product_id"],
        "product_image": product["product_image"],
        "product_size": product["product_size"],
        "price_inr": product["value_inr"],
        "shipping_note": checkout_shipping_note(product["value_inr"]),
        "delivery_estimate": "3–5 business days after booking",
    }

@app.post("/api/checkout/complete")
def complete_checkout(
    request: CheckoutCompleteRequest,
    response: Response,
    http_request: Request,
):
    draft = checkout_draft_from_request(http_request)
    payer_name = " ".join(request.payer_name.split())
    if payer_name.casefold() != draft["customer_name"].casefold():
        raise HTTPException(
            status_code=403,
            detail=f"Enter the matching customer name, {draft['customer_name']}, to complete this demo booking.",
        )
    order = ORDERS[draft["order_id"]]
    product = draft["product"]
    order.update(copy.deepcopy(product))
    order.update({
        "customer": draft["customer_name"],
        "status": "Processing",
        "courier": None,
        "tracking_id": None,
        "expected_delivery": "within 3–5 business days",
        "delivered_days_ago": None,
        "delivered_hours_ago": None,
        "ordered_hours_ago": 0,
        "order_date": datetime.now().strftime("%B %d, %Y").replace(" 0", " "),
        "payment_method": {
            "gpay": "Google Pay (demo)",
            "phonepe": "PhonePe (demo)",
            "cod": "Cash on Delivery",
        }[request.payment_method],
        "payment_status": "Paid (demo only)" if request.payment_method != "cod" else "Due on delivery",
        "payment_reference": (
            f"AURA-DEMO-{secrets.token_hex(4).upper()}" if request.payment_method != "cod" else None
        ),
        "shipping_phone": draft["phone"],
        "shipping_address": draft["address"],
        "quantity": 1,
        "estimated_total_inr": product["value_inr"],
    })
    DAMAGE_REPORTS.pop(draft["order_id"], None)
    DAMAGE_REPORT_PHOTOS.pop(draft["order_id"], None)
    CHECKOUT_DRAFTS.pop(http_request.cookies.get(CHECKOUT_COOKIE, ""), None)
    response.delete_cookie(CHECKOUT_COOKIE, httponly=True, samesite="lax")
    return {
        "success": True,
        "order_id": order["order_id"],
        "customer": order["customer"],
        "product": order["product"],
        "price_inr": order["estimated_total_inr"],
        "payment_method": order["payment_method"],
        "payment_status": order["payment_status"],
        "payment_reference": order["payment_reference"],
        "status": order["status"],
        "expected_delivery": order["expected_delivery"],
        "login_url": f"/login?next={quote('/orders')}",
        "message": (
            "Demo payment marked successful; no money was charged."
            if request.payment_method != "cod"
            else "Your demo order is booked. Payment is due on delivery."
        ),
    }

@app.get("/api/captcha")
def new_captcha():
    now = time.time()
    for challenge_id, challenge in list(CAPTCHA_CHALLENGES.items()):
        if challenge["expires_at"] <= now:
            CAPTCHA_CHALLENGES.pop(challenge_id, None)
    code = "".join(secrets.choice("23456789") for _ in range(5))
    challenge_id = secrets.token_urlsafe(18)
    CAPTCHA_CHALLENGES[challenge_id] = {"code": code, "expires_at": now + CAPTCHA_TTL_SECONDS}
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="190" height="58" viewBox="0 0 190 58">'
        '<rect width="190" height="58" rx="9" fill="#e9eee4"/>'
        '<path d="M8 43 Q49 5 95 39 T182 13 M0 18 Q65 54 132 12 T195 48" fill="none" stroke="#c4d2c1" stroke-width="2"/>'
        f'<text x="95" y="39" text-anchor="middle" font-family="Georgia,serif" font-size="29" font-weight="bold" letter-spacing="7" fill="#294936">{code}</text>'
        '</svg>'
    )
    return {"captcha_id": challenge_id, "image": f"data:image/svg+xml,{quote(svg)}"}

@app.post("/api/login")
def login(request: LoginRequest, response: Response, http_request: Request):
    if request.role == "customer":
        challenge = CAPTCHA_CHALLENGES.pop(request.captcha_id, None)
        if not challenge or challenge["expires_at"] <= time.time():
            raise HTTPException(status_code=400, detail="That CAPTCHA expired. Please refresh it and try again.")
        if not secrets.compare_digest(challenge["code"].lower(), request.captcha_code.strip().lower()):
            raise HTTPException(status_code=400, detail="The CAPTCHA code does not match. Please try again.")
    display_name = " ".join(request.name.split())
    if not display_name:
        raise HTTPException(status_code=400, detail="Please enter your name.")
    if request.role == "admin":
        admin_username = os.getenv("AURA_ADMIN_USERNAME", DEFAULT_ADMIN_USERNAME).strip() or DEFAULT_ADMIN_USERNAME
        admin_password = os.getenv("AURA_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD) or DEFAULT_ADMIN_PASSWORD
        if (
            not secrets.compare_digest(display_name.casefold(), admin_username.casefold())
            or not secrets.compare_digest(request.password, admin_password)
        ):
            raise HTTPException(status_code=403, detail="The username or password is incorrect.")
        display_name = admin_username
        order = None
    else:
        order = ORDERS.get(request.order_reference.strip().upper())
        if not order:
            raise HTTPException(status_code=404, detail="Enter one of the sample order IDs: ORD-101, ORD-102, or ORD-103.")
        if display_name.casefold() != " ".join(order["customer"].split()).casefold():
            raise HTTPException(
                status_code=403,
                detail=f"That name doesn't match the customer for {order['order_id']}. Use {order['customer']} for this order.",
            )
        if not secrets.compare_digest(request.password, CUSTOMER_PASSWORDS[order["order_id"]]):
            raise HTTPException(status_code=403, detail="The customer password is incorrect.")
        display_name = order["customer"]
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = {
        "role": request.role,
        "customer": display_name,
        "order_id": order["order_id"] if order else None,
        "expires_at": time.time() + SESSION_TTL_SECONDS,
    }
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=http_request.url.scheme == "https",
        samesite="lax",
    )
    return {
        "role": request.role,
        "customer": display_name,
        "order": order,
        "redirect": "/admin" if request.role == "admin" else "/orders",
        "expires_in": SESSION_TTL_SECONDS,
    }

@app.get("/api/session")
def current_session(request: Request):
    session = SESSIONS.get(request.cookies.get(SESSION_COOKIE, ""))
    if not session or session["expires_at"] <= time.time():
        return {"signed_in": False}
    if session.get("role") == "admin":
        return {"signed_in": True, "role": "admin", "customer": session["customer"]}
    order = get_order_details(session["order_id"])
    return {"signed_in": True, "role": "customer", "customer": session["customer"], "order": order}

@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE, "")
    SESSIONS.pop(token, None)
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax")
    return {"success": True}

@app.get("/api/health")
def health():
    openai_configured = bool(os.getenv("OPENAI_API_KEY", "").strip())
    return {
        "status": "ok",
        "ai_configured": openai_configured,
        "transcription_configured": openai_configured,
    }

@app.get("/api/policies")
def policies():
    return {"brand": "Aura Skincare", "policies": BRAND_POLICIES}

@app.get("/api/admin/orders")
def admin_orders(_: dict[str, Any] = Depends(require_admin)):
    return {"orders": list(ORDERS.values())}

@app.post("/api/admin/orders/{order_id}/cancel")
def admin_cancel_order(order_id: str, _: dict[str, Any] = Depends(require_admin)):
    normalized = order_id.strip().upper()
    order = ORDERS.get(normalized)
    if not order:
        raise HTTPException(status_code=404, detail="I couldn't find that order ID.")
    if order["status"] == "Cancelled":
        raise HTTPException(status_code=409, detail=f"Order {normalized} is already cancelled.")
    previous_status = order["status"]
    order["status"] = "Cancelled"
    return {
        "success": True,
        "order_id": normalized,
        "status": "Cancelled",
        "message": f"Order {normalized} was cancelled by an administrator (previous status: {previous_status}).",
    }

@app.put("/api/admin/policies")
def update_brand_policies(
    request: PolicyUpdate,
    _: dict[str, Any] = Depends(require_admin),
):
    expected_titles = {policy["title"] for policy in BRAND_POLICIES}
    if len(request.policies) != len(BRAND_POLICIES):
        raise HTTPException(status_code=422, detail="Submit all brand policies.")
    if any(
        set(policy) != {"title", "icon", "summary", "details"}
        or not all(value.strip() for value in policy.values())
        for policy in request.policies
    ):
        raise HTTPException(status_code=422, detail="Each policy needs a title, icon, summary, and details.")
    if {policy["title"] for policy in request.policies} != expected_titles:
        raise HTTPException(status_code=422, detail="Policy titles cannot be changed.")
    BRAND_POLICIES[:] = request.policies
    return {"brand": "Aura Skincare", "policies": BRAND_POLICIES}

def public_support_reply(
    message: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Answer general brand questions without exposing or changing customer orders."""
    msg = message.casefold()
    order_ref = re.search(r"ord[- ]?\d{3}|[A-Z]{2}-\d{6}", message, flags=re.I)
    order_intent = any(term in msg for term in (
        "track", "tracking", "order status", "status of my order", "where is my order",
        "where is the order", "my order", "my package", "my parcel",
    ))
    cancellation_action = any(term in msg for term in (
        "please cancel", "cancel my order", "cancel order", "cancel it", "i want to cancel",
        "i'd like to cancel", "i would like to cancel", "request cancellation",
    ))
    if cancellation_action or (order_ref and any(term in msg for term in ("cancel", "cancellation"))):
        reply = "For privacy, please sign in with the customer name and matching order ID to get order-specific tracking or cancellation help."
        return {
            "reply": reply, "intent": "CANCELLATION_REQUEST",
            "order_id": None, "resolution_status": "NEEDS_SIGN_IN", "tool_used": None, "mode": "public_faq",
        }
    if any(term in msg for term in ("cancel", "cancellation")):
        reply = "Customers can cancel an order only while it is Processing. Once Shipped or Out for Delivery, it cannot be cancelled; you may refuse delivery at the doorstep. Sign in and open your order dashboard to check eligibility and submit a cancellation."
        return {
            "reply": reply, "intent": "CANCELLATION_REQUEST", "order_id": None,
            "resolution_status": "RESOLVED", "tool_used": "brand_policies", "mode": "public_faq",
        }
    if any(term in msg for term in ("flight", "hotel", "travel", "weather", "stock price", "cricket score", "recipe", "politics")):
        return {
            "reply": "I'm Aria from Aura Skincare. I can only help with Aura Skincare products, orders, and policies, so I can't book travel.",
            "intent": "OUT_OF_SCOPE", "order_id": None,
            "resolution_status": "OUT_OF_SCOPE", "tool_used": None, "mode": "public_faq",
        }
    if order_ref:
        order_query_intent = "RETURN_REQUEST" if any(term in msg for term in ("return", "refund")) else "ORDER_TRACKING"
        order_query = "order return or refund" if order_query_intent == "RETURN_REQUEST" else "order"
        return {
            "reply": f"For your privacy, please sign in with the customer account linked to this {order_query}. Aria can then answer order-specific questions without exposing another customer's details.",
            "intent": order_query_intent, "order_id": None,
            "resolution_status": "NEEDS_SIGN_IN", "tool_used": None, "mode": "public_faq",
        }
    if any(term in msg for term in ("damaged", "defective", "broken", "leaking")):
        return {
            "reply": "If your product arrived damaged or defective, report it within 48 hours of delivery and include photos. The team can review it for a replacement; I can't promise approval before the review.",
            "intent": "DAMAGED_PRODUCT", "order_id": None,
            "resolution_status": "RESOLVED", "tool_used": "brand_policies", "mode": "public_faq",
        }
    if order_intent:
        return {
            "reply": "I can check that for you. Could you please share your Aura Skincare order ID, such as ORD-101?",
            "intent": "ORDER_TRACKING", "order_id": None,
            "resolution_status": "NEEDS_FOLLOW_UP", "tool_used": None, "mode": "public_faq",
        }
    resolution_status = "RESOLVED"
    if any(term in msg for term in ("shipping", "delivery fee", "delivery charge", "how long", "delivery time", "free delivery", "delivery free", "free shipping", "ship free", "deliver for free")):
        reply = "Standard delivery takes 3–5 business days. Delivery is free for orders above ₹499, and orders below ₹499 have a ₹50 shipping fee. The policy does not specify the shipping fee for an order exactly ₹499, so I don't want to guess."
        intent = "SHIPPING_POLICY"
    elif any(term in msg for term in ("return", "refund")):
        days_ago = re.search(r"\b(\d+)\s+days?\s+ago\b", msg)
        used_product = bool(re.search(r"\b(?:opened|used|tried)\b", msg)) and not bool(
            re.search(r"\b(?:not|never|haven't|hasn't|have not|has not)\s+(?:been\s+)?(?:opened|used|tried)\b", msg)
        )
        outside_return_window = bool(days_ago and int(days_ago.group(1)) > 7)
        if used_product or outside_return_window:
            reply = "Based on our policy, this item isn't eligible for return. Returns must be requested within 7 days of delivery, and products must be unopened and unused in their original packaging. The details you shared don't meet those requirements. For a damaged or defective item, report it within 48 hours of delivery with photos to request a replacement."
            resolution_status = "POLICY_BLOCKED"
        else:
            reply = "Returns are accepted within 7 days of delivery for unopened, unused products in their original packaging. For a damaged or defective item, report it within 48 hours of delivery and include photos to request a replacement."
            resolution_status = "RESOLVED"
        intent = "RETURN_REQUEST"
    elif any(term in msg for term in ("damaged", "defective", "broken", "replacement")):
        reply = "Please report damaged or defective products within 48 hours of delivery and include photos to request a replacement. Sign in if you need help with a specific order."
        intent = "DAMAGED_PRODUCT"
    elif any(term in msg for term in ("cancellation", "cancel")):
        reply = "Orders can be cancelled only while their status is Processing. Once an order is Shipped or Out for Delivery, it cannot be cancelled; customers may refuse delivery at the doorstep. Sign in for help with a specific order."
        intent = "CANCELLATION_REQUEST"
    elif any(term in msg for term in ("cod", "cash on delivery", "upi", "payment method", "payment option", "ways to pay")):
        reply = "Cash on Delivery is available for orders up to ₹2,500. You can pay with cash or UPI at the doorstep."
        intent = "COD_QUERY"
    elif (product_reply := product_support_reply(message, history=history)) is not None:
        product_reply["mode"] = "public_faq"
        return product_reply
    elif any(term in msg for term in ("hello", "hi", "hey", "good morning", "good afternoon")):
        reply = "Hi! I'm Aria from Aura Skincare. I can help with product information, shipping, returns, damaged items, and Cash on Delivery. Sign in for help with a specific order."
        intent = "GENERAL_BRAND_QUERY"
    else:
        faq = general_brand_faq(message)
        if faq:
            faq["order_id"] = None
            return {**faq, "mode": "public_faq"}
        reply = "I can help with Aura products, order tracking, shipping, returns, damaged items, and payment policies. Could you share a little more about your Aura question so I can give you an accurate answer?"
        intent = "UNKNOWN"
        resolution_status = "NEEDS_FOLLOW_UP"
    return {
        "reply": reply, "intent": intent, "order_id": None,
        "resolution_status": resolution_status, "tool_used": None, "mode": "public_faq",
    }

@app.get("/api/orders")
def list_orders(request: Request):
    if valid_session(request):
        session = SESSIONS[request.cookies[SESSION_COOKIE]]
        if session.get("role") == "admin":
            raise HTTPException(status_code=403, detail="Admin accounts do not use customer order access.")
        orders = session_order_list(session)
        return {"orders": [{**order, "damage_report": DAMAGE_REPORTS.get(order["order_id"])} for order in orders]}
    return {"orders": []}

@app.get("/api/demo-orders")
def demo_orders():
    return {
        "orders": [
            {
                "order_id": order["order_id"],
                "customer": order["customer"],
                "product": order["product"],
                "value_inr": order["value_inr"],
                "status": order["status"],
                "courier": order["courier"],
                "tracking_id": order["tracking_id"],
                "product_image": order["product_image"],
                "expected_delivery": order["expected_delivery"],
                "delivered_days_ago": order["delivered_days_ago"],
                "ordered_hours_ago": order.get("ordered_hours_ago"),
            }
            for order in ORDERS.values()
        ]
    }

@app.get("/api/orders/{order_id}")
def order_details(order_id: str, session: dict[str, Any] = Depends(get_session)):
    if session.get("role") != "customer":
        raise HTTPException(status_code=403, detail="Customer sign-in is required.")
    if order_id.strip().upper() != session["order_id"]:
        raise HTTPException(status_code=404, detail="No order found in this signed-in account.")
    result = get_order_details(order_id)
    if not result["found"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return {**result, "damage_report": DAMAGE_REPORTS.get(result["order_id"])}

@app.post("/api/chat")
def chat(request: ChatRequest, http_request: Request):
    session = SESSIONS.get(http_request.cookies.get(SESSION_COOKIE, ""))
    if session and session["expires_at"] <= time.time():
        session = None
    if session and session.get("role") == "admin":
        raise HTTPException(status_code=403, detail="Admin accounts cannot use customer support sessions.")
    message = request.message.strip()
    deterministic_policy_query = any(
        term in message.casefold()
        for term in ("return", "refund", "cancel", "cancellation", "shipping", "delivery fee",
                     "delivery charge", "cash on delivery", "cod", "flight", "travel", "weather",
                     "stock price")
    )
    if session and not deterministic_policy_query:
        result = run_ai(message, request.history, session["order_id"])
    elif session:
        result = local_fallback(message, session["order_id"], request.history)
        result["mode"] = "fallback"
    else:
        result = public_support_reply(message, request.history)
    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    return result


@app.post("/api/transcribe")
async def transcribe_audio(request: Request):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Server transcription is not configured. Add OPENAI_API_KEY to the server environment.",
        )

    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    file_extensions = {
        "audio/webm": "webm",
        "audio/mp4": "mp4",
        "audio/mpeg": "mp3",
        "audio/mpga": "mpga",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
    }
    extension = file_extensions.get(media_type)
    if not extension:
        raise HTTPException(status_code=415, detail="Record audio as WebM, MP4, MP3, or WAV and try again.")

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_TRANSCRIPTION_BYTES:
                raise HTTPException(status_code=413, detail="The recording must be shorter than 20 MB.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="The audio request has an invalid content length.") from exc

    if not request.client:
        raise HTTPException(status_code=400, detail="The request source could not be identified.")
    client_ip = request.client.host
    now = time.monotonic()
    recent_requests = [
        timestamp for timestamp in TRANSCRIPTION_REQUESTS.get(client_ip, [])
        if now - timestamp < TRANSCRIPTION_RATE_WINDOW_SECONDS
    ]
    if len(recent_requests) >= TRANSCRIPTION_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many voice transcriptions. Please wait a few minutes and try again.")
    recent_requests.append(now)
    TRANSCRIPTION_REQUESTS[client_ip] = recent_requests

    audio_chunks = []
    total_bytes = 0
    async for chunk in request.stream():
        total_bytes += len(chunk)
        if total_bytes > MAX_TRANSCRIPTION_BYTES:
            raise HTTPException(status_code=413, detail="The recording must be shorter than 20 MB.")
        audio_chunks.append(chunk)
    audio_bytes = b"".join(audio_chunks)
    if not audio_bytes:
        raise HTTPException(status_code=422, detail="No audio was received. Record your question and try again.")

    def create_transcription() -> str:
        client = OpenAI(api_key=api_key)
        result = client.audio.transcriptions.create(
            model=os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"),
            file=(f"aria-question.{extension}", audio_bytes, media_type),
            response_format="json",
        )
        return result.text.strip()

    try:
        text = await run_in_threadpool(create_transcription)
    except APIConnectionError as exc:
        logger.error("OpenAI transcription connection failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Could not connect to the speech transcription service. Check the server connection and try again.",
        ) from exc
    except APIStatusError as exc:
        logger.error("OpenAI transcription returned HTTP %s", exc.status_code)
        if exc.status_code in (401, 403):
            detail = "Speech transcription authorization failed. Check the server's OpenAI API key."
        elif exc.status_code == 429:
            detail = "Speech transcription is rate limited or over quota. Check OpenAI billing and try again later."
        else:
            detail = "The speech transcription service could not process this recording. Please try again."
        raise HTTPException(status_code=502, detail=detail) from exc
    if not text:
        raise HTTPException(status_code=422, detail="No speech was detected. Try recording again and speak clearly.")
    return {"text": text}


@app.post("/api/cancel")
def cancel(request: CancelRequest, session: dict[str, Any] = Depends(get_session)):
    if session.get("role") != "customer":
        raise HTTPException(status_code=403, detail="Customer sign-in is required.")
    if request.order_id.strip().upper() != session["order_id"]:
        raise HTTPException(status_code=404, detail="No order found in this signed-in account.")
    return cancel_order(request.order_id)

@app.post("/api/orders/{order_id}/damage")
def submit_damage_report(
    order_id: str,
    request: DamageReportRequest,
    session: dict[str, Any] = Depends(get_session),
):
    if session.get("role") != "customer":
        raise HTTPException(status_code=403, detail="Customer sign-in is required.")
    normalized = order_id.strip().upper()
    if normalized != session["order_id"]:
        raise HTTPException(status_code=404, detail="No order found in this signed-in account.")
    order = ORDERS[normalized]
    if order["status"] != "Delivered" or order["delivered_hours_ago"] is None or order["delivered_hours_ago"] > 48:
        raise HTTPException(
            status_code=409,
            detail="Damage reports must be submitted within 48 hours after delivery. This order is not currently eligible.",
        )
    if normalized in DAMAGE_REPORTS:
        raise HTTPException(status_code=409, detail="A damage report has already been submitted for this order.")
    photo_match = re.fullmatch(
        r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})",
        request.photo_data_url,
    )
    if not photo_match:
        raise HTTPException(status_code=422, detail="Upload a PNG, JPG, or WebP photo of the damage.")
    try:
        photo_bytes = base64.b64decode(photo_match.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="The damage photo could not be read. Please upload it again.")
    if not photo_bytes or len(photo_bytes) > 5_000_000:
        raise HTTPException(status_code=422, detail="The damage photo must be smaller than 5 MB.")
    photo_type = photo_match.group(1)
    valid_signature = (
        photo_type == "png" and photo_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        or photo_type == "jpeg" and photo_bytes.startswith(b"\xff\xd8\xff")
        or photo_type == "webp" and photo_bytes.startswith(b"RIFF") and photo_bytes[8:12] == b"WEBP"
    )
    if not valid_signature:
        raise HTTPException(status_code=422, detail="The selected file is not a valid PNG, JPG, or WebP image.")
    submitted_at = datetime.now(timezone.utc).isoformat()
    DAMAGE_REPORTS[normalized] = {
        "order_id": normalized,
        "description": request.description.strip(),
        "submitted_at": submitted_at,
        "status": "Awaiting customer choice",
        "resolution": None,
    }
    DAMAGE_REPORT_PHOTOS[normalized] = photo_bytes
    return {
        "report": DAMAGE_REPORTS[normalized],
        "message": "Your damage report and photo have been recorded. Would you prefer a refund or a replacement?",
    }

@app.post("/api/orders/{order_id}/damage/resolution")
def resolve_damage_report(
    order_id: str,
    request: DamageResolutionRequest,
    session: dict[str, Any] = Depends(get_session),
):
    if session.get("role") != "customer":
        raise HTTPException(status_code=403, detail="Customer sign-in is required.")
    normalized = order_id.strip().upper()
    if normalized != session["order_id"]:
        raise HTTPException(status_code=404, detail="No order found in this signed-in account.")
    report = DAMAGE_REPORTS.get(normalized)
    if not report:
        raise HTTPException(status_code=409, detail="Submit an eligible damage report before choosing a resolution.")
    if report["resolution"]:
        raise HTTPException(status_code=409, detail="A resolution has already been recorded for this damage report.")
    resolution = request.choice
    report["resolution"] = resolution
    report["status"] = "Refund choice recorded (demo only)" if resolution == "refund" else "Replacement choice recorded (demo only)"
    return {
        "report": report,
        "message": "Your refund choice has been recorded. No payment was issued in this demo." if resolution == "refund"
        else "Your replacement choice has been recorded. No replacement shipment was created in this demo.",
    }
