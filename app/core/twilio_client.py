from twilio.rest import Client
import os
from dotenv import load_dotenv

load_dotenv()

client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

def make_call():
    call = client.calls.create(
        to=os.getenv("YOUR_PHONE_NUMBER"),
        from_=os.getenv("TWILIO_PHONE_NUMBER"),
        url="https://uninterlinked-augusta-unaproned.ngrok-free.dev/voice"  # IMPORTANT
    )
    return call.sid