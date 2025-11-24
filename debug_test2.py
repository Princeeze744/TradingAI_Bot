import os
import json
from dotenv import load_dotenv
import anthropic

load_dotenv()

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

print("="*60)
print("🔍 IMPROVED SIGNAL PARSER TEST")
print("="*60)

test_signals = [
    "buy dex900 up sl 3031 tp 3173",
    "BUY EURUSD Entry 1.1000 TP 1.1100 SL 1.0950",
    "Entry price 1.41067 profit price 1.41384 stop loss price 1.40967",
    "buy limit on GBPAUD Entry price 2.01 profit 2.03 stop 2.00"
]

for i, test_signal in enumerate(test_signals, 1):
    print(f"\n   Test {i}: {test_signal}")
    
    prompt = f"""You are an expert forex signal parser. Extract trading signal from this message.

Message: {test_signal}

RULES:
- Look for ANY pair/symbol: EURUSD, GBPUSD, DEX900, USDCAD, GBPAUD, EURNZD, etc
- If no pair mentioned, infer from context or use first uppercase word
- BUY/SELL can be: buy, sell, long, short, bullish, bearish, etc
- Find 3 numbers: entry price, take profit, stop loss
- Order doesn't matter - find all numbers

Return ONLY this JSON (no markdown, no code blocks, just raw JSON):
{{
    "instrument": "DETECTED_PAIR",
    "side": "BUY or SELL",
    "entry": first_number,
    "tp": second_number,
    "sl": third_number
}}

CRITICAL: Return ONLY raw JSON. NO ```json markdown blocks. NO extra text."""
    
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = response.content[0].text.strip()
        print(f"      Raw response: {response_text[:80]}...")
        
        # Strip markdown code blocks if present
        response_text = response_text.replace('```json', '').replace('```', '').strip()
        
        signal_data = json.loads(response_text)
        
        if signal_data and signal_data.get('instrument') and signal_data.get('entry'):
            print(f"      ✅ PARSED: {signal_data['instrument']} {signal_data['side']}")
            print(f"         Entry: {signal_data['entry']}, TP: {signal_data['tp']}, SL: {signal_data['sl']}")
        else:
            print(f"      ❌ Missing required fields")
    
    except json.JSONDecodeError as e:
        print(f"      ❌ JSON Error: {e}")
    except Exception as e:
        print(f"      ❌ Error: {e}")

print("\n" + "="*60)