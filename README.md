# Aura Skincare — AI Voice CX Agent

A browser-based voice customer support demo for the DataStraw assessment. Aria can answer Aura Skincare policy questions, look up mock orders, validate cancellation eligibility, and show a live call transcript.

## Features

- Animated Aura intro, a three-step animated product-routine showcase on Home, and a minimal role-aware sign-in page with backend-verified customer CAPTCHA.
- Separate customer and admin dashboards. Customer access requires the matching name, CAPTCHA, customer password, and assigned order ID. Each customer dashboard shows only that customer's order and provides policy-checked cancellation and a damage-report workflow. Admin access requires the configured username and password without a CAPTCHA. Admins can review all three sample orders in an Order ID, Customer, Product, Value, Status, and Notes table, cancel any order that is not already cancelled, and edit public brand-policy text. Signing out from either dashboard returns to Home.
- Multi-page website: Home, product booking, demo payment, Voice Support, Sample Orders, and Our Policies.
- Responsive navigation and separate purpose-built pages instead of a single dashboard.
- Login requires the exact sample customer name, customer password, and assigned order ID; successful customer login opens a dashboard scoped to that single order.
- Order pages show product illustrations, descriptions, size, highlights, care instructions, payment, quantity, and order status.
- Orders and policy pages load their content dynamically from the FastAPI backend; only admins can update the public policy text.
- Each Home product has a Book the Order flow: customers enter the name already assigned to that sample product and delivery details, then choose simulated Google Pay, simulated PhonePe, or Cash on Delivery. The backend enforces Priya/Vitamin C/ORD-101, Rahul/Sunscreen/ORD-102, and Ananya/Green Tea set/ORD-103; after confirmation, only the matching existing record is updated and can be viewed after signing in.
- Booking requires a 10-digit mobile number; the form limits entry to 10 numeric digits and the backend rejects values of any other length.
- Checkout drafts expire after 15 minutes and are held in backend memory; orders and delivery details also reset when the backend restarts. UPI payments are simulated with dummy references. No real transaction occurs and the demo never requests card numbers, UPI PINs, or banking passwords.
- Browser speech-to-text using the Web Speech API where supported, with an optional tap-to-record OpenAI transcription fallback.
- Spoken responses using browser text-to-speech, preferring an English (India) voice when available.
- Public voice and typed support answer Aura product and policy questions. Order-specific questions and actions require the matching customer session.
- Aria answers common brand, catalog, multi-product pricing and benefits, delivery, payment, return, damage, cancellation, tracking, and contact questions from the available demo facts; she asks for clarification or says when information is unavailable instead of inventing details. Product answers use verified catalog details (how to use, listed price, size, highlights) and avoid unsupported ingredient, compatibility, or skin-suitability claims. Live courier/GPS location is not available in the sample data.
- Python FastAPI backend.
- AI model integration with function/tool calling when `OPENAI_API_KEY` is configured.
- Deterministic fallback mode so the UI and common test scenarios work without an AI key.
- Mock orders ORD-101, ORD-102, ORD-103.
- Policy-aware return/cancellation responses and invalid order handling.
- Live call transcript and typed test input.
- Typed test input when microphone support is unavailable.

## Architecture

The FastAPI backend serves a sign-in page, public home, voice-support, and policies pages, plus a protected sample-orders page:

- `/login` — Customer or admin sign-in. Customer sign-in uses CAPTCHA and checks the exact matching name, password, and order ID; admin sign-in checks the configured username and password without CAPTCHA.
- `/admin` — Admin-only view of the three sample customer orders, with delivery and tracking details, administrator cancellation for any active order, and an editor for public brand-policy text.
- `/` — Home and entry points into the demo.
- `/book?product=ORD-101` — Product booking and delivery-details form; use ORD-102 or ORD-103 for the other products.
- `/payment` — Demo payment choice and order confirmation; requires an active 15-minute checkout draft.
- `/voice` — Public voice/text product and policy support and a live transcript; private order tracking and cancellation actions require the matching customer sign-in.
- `/orders` — Private customer dashboard with a single assigned order, product and tracking details, cancellation, and damage reporting.
- `/policies` — Brand policies loaded from `GET /api/policies`.

The voice flow is browser microphone → SpeechRecognition → `POST /api/chat` with recent conversation history → policy-aware answer and optional order tools → live transcript and browser SpeechSynthesis. If browser speech recognition is unavailable, users can explicitly tap Record and Stop & send to submit a recording of up to 30 seconds to `POST /api/transcribe`; the backend sends it to OpenAI using `OPENAI_API_KEY` and `OPENAI_TRANSCRIPTION_MODEL`. Audio is sent only after the user taps Stop & send, is held in memory for the request, and is not saved by this demo. Transcription requests are limited to 20 MB and eight per client IP per five minutes. Add the key to the server's private `.env` file; never expose it to browser code or commit it. Typed input remains available if transcription is not configured or fails.

FastAPI exposes `/api/captcha`, `/api/login`, `/api/session`, `/api/logout`, `/api/health`, `/api/transcribe`, `/api/booking/products`, `/api/checkout/start`, `/api/checkout/draft`, `/api/checkout/complete`, `/api/orders`, `/api/demo-orders`, `/api/orders/{order_id}`, `/api/orders/{order_id}/damage`, `/api/orders/{order_id}/damage/resolution`, `/api/policies`, `/api/admin/orders`, `/api/admin/orders/{order_id}/cancel`, `/api/admin/policies`, `/api/chat`, and `/api/cancel`. Successful sign-in creates an HttpOnly session cookie that expires after eight hours. Admin APIs require an admin session, and admins can cancel any order that is not already cancelled; customer cancellation remains limited to Processing orders, with customer order details and actions scoped to the signed-in customer's order. Damage claims require a delivered order within the 48-hour window and an uploaded photo. For this assignment's demo flow, eligible claims record the customer's choice of refund or replacement in memory; this does not issue a real payment refund or ship a product. Product and policy questions work without a session; order-specific support requires a matching customer session. Policy-sensitive answers use deterministic checks even when an AI key is configured; other supported questions use the LLM with function calling when configured, or deterministic fallback responses otherwise.

Demo customer passwords:

| Customer | Order | Password |
|---|---|---|
| Priya Sharma | ORD-101 | `Priya@101` |
| Rahul Verma | ORD-102 | `Rahul@102` |
| Ananya Patel | ORD-103 | `Ananya@103` |

The read-only `/api/demo-orders` endpoint provides sample-order data to the demo application. The customer sign-in page does not expose sample-order shortcuts. Voice support requires a matching customer sign-in before providing order-specific information or changing an order.

The sample order database is an in-memory dictionary for the assignment. It resets when the server restarts. Product images are locally stored SVG illustrations. It is not production storage.

The mock orders do not include a delivery street address, warehouse origin, or live GPS location. The customer dashboard shows the available courier, tracking ID, and delivery estimate, and labels unavailable location fields rather than inventing them.

## Requirements

- Python 3.10+ recommended.
- Google Chrome or another browser supporting Web Speech API for voice input. Support varies by browser/OS.
- Optional AI API key for model-powered natural language and tool calling.

## Run locally (Windows)

Open Command Prompt or PowerShell in this folder:

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Open `.env` in a text editor and configure `AURA_ADMIN_USERNAME` and `AURA_ADMIN_PASSWORD` for the admin account. The default admin login is `admin_1` with password `Admin@123`. Set `OPENAI_API_KEY` only if you have one. Never publish `.env` or share these values.

Customer login names and order IDs:

- Priya Sharma — ORD-101
- Rahul Verma — ORD-102
- Ananya Patel — ORD-103

Start the app:

```powershell
uvicorn main:app --reload
```

Open http://127.0.0.1:9013/
On macOS/Linux, activate the environment with `source .venv/bin/activate` and copy `.env.example` to `.env`.

## Test scenarios

1. Start Call and ask: "Where is my order ORD-101?"
   - Expected: Out for delivery, BlueDart, BD-982103, expected by 6 PM today.
2. Ask: "Can I return ORD-102? It was delivered 14 days ago."
   - Expected: outside the seven-day return window.
   - Public support must also decline a return when the customer says the product was opened or used, while explaining both policy requirements.
3. Ask: "Please cancel ORD-103."
   - Expected: cancellation succeeds only while its status is Processing.
4. Ask to cancel ORD-101.
   - Expected: cancellation denied because it is Out for Delivery.
5. Ask about ORD-999.
   - Expected: not found; asks customer to verify the ID.
6. Ask: "Can you book me a flight to Goa?"
   - Expected: politely out of scope.
7. Ask about COD.
   - Expected: COD up to ₹2,500, cash or UPI at doorstep.
8. End the call.
   - Expected: the call transcript remains visible.

To enter the demo, enter the matching customer name, CAPTCHA, and order ID:

- Priya Sharma — ORD-101 or BD-982103
- Rahul Verma — ORD-102 or DL-441029
- Ananya Patel — ORD-103 (no courier tracking ID is assigned yet)

Enter the visible CAPTCHA code with the matching customer name and order ID. Each authenticated account can see and ask Aria about only its selected order. The sign-in is for the demo only and does not create a real customer account.

## Brand policy implementation

- Free delivery applies to orders above ₹499.
- Orders below ₹499 have a ₹50 shipping fee.
- For exactly ₹499, the brief does not explicitly define the shipping fee; the agent should not guess.
- Standard delivery takes 3–5 business days.
- Returns: within 7 days of delivery for unopened, unused products in original packaging.
- Damaged/defective items: report within 48 hours with photos for replacement.
- Cancellation only while status is Processing.
- COD available up to ₹2,500; cash or UPI at doorstep.

## Deployment on Render

1. Push this project to a GitHub repository. Do not commit `.env`; it contains local credentials.
2. In Render, choose **New → Blueprint** and connect the repository containing `render.yaml`.
3. Set `AURA_ADMIN_USERNAME` and a strong, unique `AURA_ADMIN_PASSWORD` when Render prompts for them. `OPENAI_API_KEY` is optional; leave it unset for deterministic fallback mode.
4. Deploy and open the HTTPS URL supplied by Render.
5. Test the deployed site from a fresh browser session. Grant microphone access.
6. Confirm `/api/health` reports `ai_configured: true` if you configured the AI key.

The Blueprint runs a single free Web Service and includes `/api/health` as its health check. The application currently stores orders, sessions, checkout drafts, and damage reports in memory; they reset when the service restarts and are not suitable as persistent production data.

Browser microphone APIs generally require a secure context such as HTTPS on a deployed site; localhost is normally accepted for local testing.

## Environment variables

- `OPENAI_API_KEY`: optional for fallback mode, required for LLM tool-calling.
- `OPENAI_MODEL`: model name accepted by your API account; default is `gpt-4o-mini`.

## Assessment reflection

### 1. Why this architecture and stack?
I chose FastAPI because it lets me implement API endpoints and serve the browser frontend from one Python application, reducing deployment complexity. The browser Web Speech API provides a quick speech-to-text and text-to-speech prototype, while an LLM with function calling handles natural-language reasoning and tool use.

### 2. Most difficult part and solution
The key challenge is ensuring the agent's spoken responses follow the actual order data and brand policies. I separated order lookup and cancellation validation into backend functions and provide a deterministic fallback for core test scenarios.

### 3. What would I improve with one more week?
I would improve speech latency and interruption handling, add persistent storage and automated tests, and evaluate better speech recognition for Indian English and Hinglish. I would also strengthen call-summary generation so it captures multiple intents and outcomes across a full conversation.

### 4. What changes for 1,000 conversations per day?
I would move mock orders and call records to a managed database, add authentication/authorization, rate limits, observability, retries, concurrency/load testing, and data-retention controls. I would measure model/voice latency and cost per call, then scale the API and voice pipeline based on actual traffic.

## Limitations to disclose

- This is a demo using mock order data, not a live commerce integration.
- The in-memory database resets on restart.
- Browser speech recognition support and voice availability vary.
- The fallback mode is rule-based and is not equivalent to full LLM conversation.
- A production system needs stronger privacy controls, persistence, authentication, audit logs, and monitoring.
