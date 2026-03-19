from fastapi import APIRouter, Response
from twilio.rest import Client
from app.config.settings import *
from app.phone.twiml import say_hello
import os 

router = APIRouter()

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


# 🔊 Twilio webhook (Twilio sends POST; GET allowed for browser testing)
@router.api_route("/voice", methods=["GET", "POST"])
async def voice():
    return Response(content=say_hello(), media_type="text/xml")


# 📞 Trigger outbound call (POST only - prevents accidental trigger from URL bar autocomplete)
@router.post("/call")
def call():
    call = client.calls.create(
        to=YOUR_PHONE_NUMBER,
        from_=TWILIO_PHONE_NUMBER,
        url=f"{NGROK_URL}/voice"
    )
    return {"status": "calling", "sid": call.sid}