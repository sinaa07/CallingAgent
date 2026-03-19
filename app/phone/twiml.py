from app.config.prompts import GREETING

def say_hello() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">{GREETING}</Say>
</Response>"""