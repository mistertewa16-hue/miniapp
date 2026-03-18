import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dodopayments import DodoPayments
from standardwebhooks import Webhook
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ──────────────────────────────────────────────────────────────────
DODO_API_KEY        = os.environ["DODO_API_KEY"]
DODO_WEBHOOK_SECRET = os.environ["DODO_WEBHOOK_SECRET"]
DODO_PRODUCT_ID     = os.environ["DODO_PRODUCT_ID"]
POINTS_PER_PURCHASE = 1000

# ── Clients ──────────────────────────────────────────────────────────────────
dodo = DodoPayments(bearer_token=DODO_API_KEY, environment="test_mode")
wh   = Webhook(DODO_WEBHOOK_SECRET)

# ── In-memory points store (swap with DB later) ───────────────────────────────
points_store: dict[str, int] = {}

# ── Models ────────────────────────────────────────────────────────────────────
class CreatePaymentRequest(BaseModel):
    telegram_user_id: str
    username: str | None = "User"

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/points/{telegram_user_id}")
def get_points(telegram_user_id: str):
    pts = points_store.get(telegram_user_id, 0)
    return {"telegram_user_id": telegram_user_id, "points": pts}


@app.post("/create-payment")
async def create_payment(body: CreatePaymentRequest):
    try:
        session = dodo.checkout_sessions.create(
            product_cart=[{"product_id": DODO_PRODUCT_ID, "quantity": 1}],
            customer={"name": body.username or "User", "email": f"{body.telegram_user_id}@telegram.user"},
            billing_address={"city": "N/A", "state": "N/A", "country": "US", "street": "N/A", "zipcode": "00000"},
            return_url="https://t.me",
            metadata={"telegram_user_id": body.telegram_user_id},
        )
        logger.info(f"Session created: {session.session_id} for user {body.telegram_user_id}")
        return {"checkout_url": session.checkout_url, "session_id": session.session_id}
    except Exception as e:
        logger.error(f"Payment creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook")
async def dodo_webhook(request: Request):
    raw_body = await request.body()
    headers = {
        "webhook-id":        request.headers.get("webhook-id", ""),
        "webhook-signature": request.headers.get("webhook-signature", ""),
        "webhook-timestamp": request.headers.get("webhook-timestamp", ""),
    }

    try:
        wh.verify(raw_body, headers)
    except Exception as e:
        logger.warning(f"Webhook signature invalid: {e}")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event_type = payload.get("type") or payload.get("event_type", "")
    logger.info(f"Webhook received: {event_type}")

    if event_type == "payment.succeeded":
        data = payload.get("data", {})
        metadata = data.get("metadata", {})
        telegram_user_id = metadata.get("telegram_user_id")

        if telegram_user_id:
            points_store[telegram_user_id] = points_store.get(telegram_user_id, 0) + POINTS_PER_PURCHASE
            logger.info(f"Credited {POINTS_PER_PURCHASE} pts to user {telegram_user_id}. Total: {points_store[telegram_user_id]}")
        else:
            logger.warning("Webhook: no telegram_user_id in metadata")

    return JSONResponse({"received": True})

