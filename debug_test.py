import os
import json
from dotenv import load_dotenv
import anthropic

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
SIGNAL_CHANNEL_ID = os.getenv('SIGNAL_CHANNEL_ID')

print("="*60)
print("🔍 TRADE2RETIRE BOT DEBUG TESTER")
print("="*60)

# Test 1: Check environment variables
print("\n1️⃣ CHECKING ENVIRONMENT VARIABLES:")
print(f"   ✅ TELEGRAM_BOT_TOKEN: {TELEGRAM_BOT_TOKEN[:20]}..." if TELEGRAM_BOT_TOKEN else "   ❌ TELEGRAM_BOT_TOKEN: NOT FOUND")
print(f"   ✅ ANTHROPIC_API_KEY: {ANTHROPIC_API_KEY[:20]}..." if ANTHROPIC_API_KEY else "   ❌ ANTHROPIC_API_KEY: NOT FOUND")
print(f"   ✅ SIGNAL_CHANNEL_ID: {SIGNAL_CHANNEL_ID}" if SIGNAL_CHANNEL_ID else "   ❌ SIGNAL_CHANNEL_ID: NOT FOUND")

# Test 2: Test Claude API
print("\n2️⃣ TESTING CLAUDE AI SIGNAL PARSER:")

test_signals = [
    "buy dex900 up sl 3031 tp 3173",
    "BUY EURUSD Entry 1.1000 TP 1.1100 SL 1.0950",
    "Entry price 1.41067 profit price 1.41384 stop loss price 1.40967",
    "buy limit on GBPAUD Entry price 2.01 profit 2.03 stop 2.00"
]

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

for i, test_signal in enumerate(test_signals, 1):
    print(f"\n   Test {i}: {test_signal}")
    
    prompt = f"""You are an expert forex trading signal parser. Extract trading signal information from this message.

Message: {test_signal}

Return ONLY valid JSON:
{{
    "instrument": "PAIR_NAME",
    "side": "BUY or SELL",
    "entry": number,
    "tp": number,
    "sl": number
}}

If cannot find all fields, return {{}}.
Return ONLY JSON."""
    
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = response.content[0].text.strip()
        signal_data = json.loads(response_text)
        
        if signal_data:
            print(f"      ✅ Parsed: {signal_data['instrument']} {signal_data['side']}")
            print(f"         Entry: {signal_data['entry']}, TP: {signal_data['tp']}, SL: {signal_data['sl']}")
        else:
            print(f"      ❌ Could not parse")
    
    except Exception as e:
        print(f"      ❌ Error: {e}")

print("\n" + "="*60)
print("DEBUG TEST COMPLETE")
print("="*60)