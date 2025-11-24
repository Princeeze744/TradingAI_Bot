import os
import logging
import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import anthropic
from openai import OpenAI

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
SIGNAL_CHANNEL_ID = int(os.getenv('SIGNAL_CHANNEL_ID'))

# Initialize AI clients
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Storage
active_signals: Dict = {}
closed_signals: List = []
user_preferences: Dict = {}
conversation_history: Dict = {}
user_stats: Dict = {}
faq_cache: Dict = {}

# Rate limiting
user_last_request: Dict = {}
RATE_LIMIT_SECONDS = 3

# FAQ Database (instant responses, no AI needed)
FAQ_RESPONSES = {
    "how to calculate lot size": """
📊 **Lot Size Calculator**

**Formula:**
Lot Size = (Account Balance × Risk %) / (Stop Loss in Pips × Pip Value)

**Example:**
- Account: $1000
- Risk: 2% = $20
- Stop Loss: 50 pips
- Pip Value: $10 (for 0.1 lot)

Lot Size = $20 / (50 × $1) = 0.4 lots

**Quick Reference:**
- 0.01 lot = $0.10 per pip (micro)
- 0.1 lot = $1 per pip (mini)
- 1.0 lot = $10 per pip (standard)

Want me to calculate for your specific trade?
""",
    "what is stop loss": """
🛡️ **Stop Loss (SL) Explained**

A stop loss automatically closes your trade when price moves against you by a set amount.

**Key Points:**
✅ Protects your capital
✅ Removes emotion from trading
✅ Set it based on technical levels
✅ Risk only 1-2% per trade

**Example:**
You buy EUR/USD at 1.1000
Set SL at 1.0950 (50 pips below)
If price drops to 1.0950, trade auto-closes
Your loss is limited to 50 pips

Never trade without a stop loss! 💪
""",
    "what is take profit": """
🎯 **Take Profit (TP) Explained**

Take profit automatically closes your trade when it reaches your profit target.

**Benefits:**
✅ Locks in profits automatically
✅ No emotional decisions
✅ Based on technical analysis
✅ Can have multiple TP levels

**Example:**
You buy EUR/USD at 1.1000
Set TP at 1.1100 (100 pips profit)
When price hits 1.1100, trade closes
You secure 100 pips profit

Our signals include TP levels! 🚀
""",
    "how do i start trading": """
🚀 **Getting Started with Trading**

**Step 1:** Learn the Basics
- Understand forex pairs, pips, lots
- Study candlestick patterns
- Learn risk management

**Step 2:** Choose a Broker
- Look for regulated brokers
- Check spreads and fees
- Test with demo account first

**Step 3:** Start Small
- Begin with micro lots (0.01)
- Risk only 1-2% per trade
- Follow our signals to learn

**Step 4:** Keep Learning
- Journal your trades
- Review wins and losses
- Stay disciplined

**Need specific help?** Just ask me! 📚
""",
    "what is forex": """
💱 **Forex Trading Explained**

Forex = Foreign Exchange Market

**What is it?**
Trading currencies against each other (EUR/USD, GBP/JPY, etc.)

**Key Facts:**
🌍 Largest financial market ($7.5 trillion daily)
⏰ Open 24/5 (Sunday-Friday)
📈 Trade currency pairs
💰 Profit from price movements

**Example:**
Buy EUR/USD at 1.1000
Sell at 1.1100
Profit: 100 pips

**Our signals help you trade profitably!** 📊

Want to know more about a specific topic?
"""
}


class TradingSignal:
    """Enhanced trading signal class"""
    def __init__(self, instrument: str, side: str, entry: float, 
                 tp: float, sl: float, timestamp: datetime, message_id: int = None):
        self.instrument = instrument
        self.side = side
        self.entry = entry
        self.tp = tp
        self.sl = sl
        self.timestamp = timestamp
        self.message_id = message_id
        self.status = "ACTIVE"
        self.current_profit = 0.0
        self.hit_tp = False
        self.hit_sl = False
    
    def to_dict(self):
        return {
            "instrument": self.instrument,
            "side": self.side,
            "entry": self.entry,
            "tp": self.tp,
            "sl": self.sl,
            "timestamp": self.timestamp.isoformat(),
            "status": self.status,
            "current_profit": self.current_profit,
            "hit_tp": self.hit_tp,
            "hit_sl": self.hit_sl
        }


def check_rate_limit(user_id: int) -> bool:
    """Check if user is rate limited"""
    now = datetime.now()
    if user_id in user_last_request:
        time_diff = (now - user_last_request[user_id]).total_seconds()
        if time_diff < RATE_LIMIT_SECONDS:
            return False
    user_last_request[user_id] = now
    return True


def should_respond_in_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Determine if bot should respond in group chat"""
    message = update.message
    
    # Always respond in private chats
    if message.chat.type == 'private':
        return True
    
    # In groups, only respond if:
    # 1. Message is a command
    if message.text and message.text.startswith('/'):
        return True
    
    # 2. Bot is mentioned
    if message.entities:
        for entity in message.entities:
            if entity.type == 'mention':
                mentioned_username = message.text[entity.offset:entity.offset + entity.length]
                if mentioned_username == f"@{context.bot.username}":
                    return True
    
    # 3. Message is a reply to bot
    if message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id:
        return True
    
    return False


def search_faq(query: str) -> Optional[str]:
    """Search FAQ for instant responses"""
    query_lower = query.lower().strip()
    
    # Direct keyword matching
    for key, response in FAQ_RESPONSES.items():
        if key in query_lower or query_lower in key:
            return response
    
    # Fuzzy matching for common variations
    if any(word in query_lower for word in ['lot', 'size', 'calculate', 'position']):
        return FAQ_RESPONSES.get("how to calculate lot size")
    
    if any(word in query_lower for word in ['stop', 'loss', 'sl']):
        return FAQ_RESPONSES.get("what is stop loss")
    
    if any(word in query_lower for word in ['take', 'profit', 'tp', 'target']):
        return FAQ_RESPONSES.get("what is take profit")
    
    if any(word in query_lower for word in ['start', 'begin', 'beginner', 'new']):
        return FAQ_RESPONSES.get("how do i start trading")
    
    if any(word in query_lower for word in ['what is forex', 'forex trading', 'currency']):
        return FAQ_RESPONSES.get("what is forex")
    
    return None


def determine_ai_complexity(query: str) -> str:
    """Determine which AI to use based on query complexity"""
    query_lower = query.lower()
    
    # Use GPT-3.5 for simple queries (cheaper)
    simple_keywords = [
        'hi', 'hello', 'thanks', 'thank you', 'ok', 'okay', 
        'yes', 'no', 'good', 'great', 'cool'
    ]
    
    if any(keyword == query_lower.strip() for keyword in simple_keywords):
        return 'gpt-simple'
    
    # Use GPT-3.5 for basic questions
    if len(query.split()) < 10 and '?' in query:
        return 'gpt-basic'
    
    # Use Claude for complex analysis
    complex_indicators = [
        'explain', 'analyze', 'why', 'strategy', 'recommend',
        'should i', 'what do you think', 'advice', 'suggestion'
    ]
    
    if any(indicator in query_lower for indicator in complex_indicators):
        return 'claude-complex'
    
    # Default to GPT for medium complexity
    return 'gpt-basic'


async def get_ai_response(user_message: str, user_id: int, complexity: str = 'auto') -> str:
    """Get AI response with smart routing"""
    
    # Auto-detect complexity if not specified
    if complexity == 'auto':
        complexity = determine_ai_complexity(user_message)
    
    # Build context
    history = conversation_history.get(user_id, [])
    signal_context = f"Active signals: {len(active_signals)} - {list(active_signals.keys())}" if active_signals else "No active signals"
    
    try:
        if complexity == 'gpt-simple':
            # Very simple responses with GPT-3.5
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a friendly trading assistant. Keep responses very brief and conversational."},
                    {"role": "user", "content": user_message}
                ],
                max_tokens=100,
                temperature=0.7
            )
            return response.choices[0].message.content
        
        elif complexity == 'gpt-basic':
            # Basic questions with GPT-3.5
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": f"You are Trade2Retire AI Assistant. Be helpful and concise. {signal_context}"},
                    *history[-4:],
                    {"role": "user", "content": user_message}
                ],
                max_tokens=300,
                temperature=0.8
            )
            return response.choices[0].message.content
        
        else:  # claude-complex
            # Complex analysis with Claude
            system_context = f"""You are Trade2Retire AI Assistant, a professional forex trading support bot.

Your expertise:
- Forex signal analysis and explanations
- Risk management and position sizing
- Market analysis and trading strategies
- Educational support for traders

Current context: {signal_context}

Be professional, insightful, and supportive. Provide detailed explanations when needed."""
            
            message = anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                system=system_context,
                messages=[
                    *history[-6:],
                    {"role": "user", "content": user_message}
                ]
            )
            
            return message.content[0].text
    
    except Exception as e:
        logger.error(f"AI error ({complexity}): {e}")
        return "I'm having a moment! 😅 Please try again or use /help for quick commands."


async def update_conversation_history(user_id: int, user_msg: str, bot_response: str):
    """Update conversation history for context"""
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    
    conversation_history[user_id].append({"role": "user", "content": user_msg})
    conversation_history[user_id].append({"role": "assistant", "content": bot_response})
    
    # Keep last 8 messages
    if len(conversation_history[user_id]) > 8:
        conversation_history[user_id] = conversation_history[user_id][-8:]


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    
    # Initialize user stats
    if user.id not in user_stats:
        user_stats[user.id] = {
            "joined": datetime.now(),
            "queries": 0,
            "favorite_pairs": []
        }
    
    welcome_message = f"""
🤖 **Welcome to Trade2Retire AI Assistant, {user.first_name}!**

I'm your intelligent 24/7 trading companion powered by advanced AI.

**What I Can Do:**
✅ Answer trading questions instantly
✅ Track and analyze signals automatically
✅ Calculate risk and position sizes
✅ Provide market insights
✅ Remember our conversations

**How to Use Me:**

📱 **In Private Chat:** Just message me anything!

👥 **In Group Chat:** 
• Mention me: @{context.bot.username}
• Reply to my messages
• Use commands: /signals, /help

**Quick Start:**
/signals - View active signals
/help - See all commands
/stats - Your trading statistics

💡 **Pro Tip:** I use smart AI routing to respond super fast while managing costs efficiently!

Ready to elevate your trading? Ask me anything! 🚀
"""
    
    keyboard = [
        [InlineKeyboardButton("📊 Active Signals", callback_data='view_signals')],
        [InlineKeyboardButton("📚 Trading Guide", callback_data='quick_guide')],
        [InlineKeyboardButton("💡 How to Use", callback_data='how_to_use')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = """
🔧 **Trade2Retire AI Assistant - Full Command List**

**📊 Signal Commands:**
/signals - All active trading signals
/status [PAIR] - Check specific pair status
/closed - Recently closed signals
/performance - Overall win rate & stats

**🎯 Analysis Commands:**
/analyze [PAIR] - Deep market analysis
/risk [AMOUNT] [PAIR] - Position size calculator
/why [PAIR] - Explain signal rationale

**👤 Personal Commands:**
/track [PAIRS] - Set favorite pairs to follow
/stats - Your usage statistics
/alerts - Manage notifications
/settings - Customize preferences

**📚 Learning Commands:**
/learn - Trading education resources
/faq - Common questions answered
/glossary - Trading term definitions

**💬 Natural Conversation:**
Just talk to me! Ask questions like:
• "What's happening with EURUSD?"
• "Calculate lot size for $500 account"
• "Explain this week's signals"
• "Should I enter this trade?"

**Group Chat Usage:**
To activate me in groups:
• @mention me: @{context.bot.username}
• Reply to my messages
• Use any command

**Need Help?** Just ask! I'm designed to understand natural language. 🤖✨
"""
    await update.message.reply_text(help_text)


async def signals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display all active signals"""
    if not active_signals:
        keyboard = [
            [InlineKeyboardButton("🔄 Refresh", callback_data='view_signals')],
            [InlineKeyboardButton("📈 Past Performance", callback_data='performance')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📭 **No Active Signals**\n\n"
            "All signals are currently closed or no new signals have been posted.\n\n"
            "💡 Signals are automatically tracked from the channel!\n"
            "⏰ Check back soon or enable alerts with /alerts",
            reply_markup=reply_markup
        )
        return
    
    message = "📊 **ACTIVE TRADING SIGNALS**\n" + "="*30 + "\n\n"
    
    for instrument, signal in active_signals.items():
        # Calculate potential profit
        pip_difference = abs(signal.tp - signal.entry)
        
        if signal.current_profit > 0:
            emoji = "🟢"
            status_text = f"+{signal.current_profit:.1f} pips"
        elif signal.current_profit < 0:
            emoji = "🔴"
            status_text = f"{signal.current_profit:.1f} pips"
        else:
            emoji = "⚪"
            status_text = "Pending Entry"
        
        message += f"{emoji} **{signal.instrument}**\n"
        message += f"├─ Direction: **{signal.side}**\n"
        message += f"├─ Entry: `{signal.entry}`\n"
        message += f"├─ Take Profit: `{signal.tp}`\n"
        message += f"├─ Stop Loss: `{signal.sl}`\n"
        message += f"├─ Status: {status_text}\n"
        message += f"└─ Posted: {signal.timestamp.strftime('%b %d, %H:%M')}\n\n"
    
    message += f"📈 **Total Active:** {len(active_signals)}\n"
    message += f"🎯 **Closed Today:** {len([s for s in closed_signals if (datetime.now() - datetime.fromisoformat(s['timestamp'])).days == 0])}\n\n"
    message += "💬 Ask me: \"Explain the EURUSD signal\" for analysis!"
    
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data='view_signals')],
        [InlineKeyboardButton("📊 Performance Stats", callback_data='performance')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, reply_markup=reply_markup)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user statistics"""
    user_id = update.effective_user.id
    
    if user_id not in user_stats:
        user_stats[user_id] = {
            "joined": datetime.now(),
            "queries": 0,
            "favorite_pairs": []
        }
    
    stats = user_stats[user_id]
    member_since = stats['joined'].strftime('%B %d, %Y')
    
    stats_message = f"""
📊 **Your Trade2Retire Statistics**

👤 **Profile:**
Member Since: {member_since}
Total Queries: {stats['queries']}

📈 **Activity:**
Active Signals: {len(active_signals)}
Signals Tracked: {len(active_signals) + len(closed_signals)}

🎯 **Favorites:**
{', '.join(stats['favorite_pairs']) if stats['favorite_pairs'] else 'None set - use /track to add'}

💡 Want to track specific pairs? Use:
/track EURUSD GBPUSD XAUUSD
"""
    
    await update.message.reply_text(stats_message)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages with smart AI routing"""
    
    # Check if should respond in group
    if not should_respond_in_group(update, context):
        return
    
    user_id = update.effective_user.id
    user_message = update.message.text
    
    # Remove bot mention if present
    if context.bot.username:
        user_message = user_message.replace(f"@{context.bot.username}", "").strip()
    
    # Rate limiting
    if not check_rate_limit(user_id):
        await update.message.reply_text("⏳ Please wait a moment before sending another message!")
        return
    
    # Update stats
    if user_id in user_stats:
        user_stats[user_id]['queries'] += 1
    
    # Check FAQ first (instant, free response)
    faq_response = search_faq(user_message)
    if faq_response:
        await update.message.reply_text(faq_response)
        return
    
    # Show typing indicator
    await update.message.chat.send_action(action="typing")
    
    # Get AI response with smart routing
    response = await get_ai_response(user_message, user_id)
    
    # Update conversation history
    await update_conversation_history(user_id, user_message, response)
    
    await update.message.reply_text(response)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages"""
    await update.message.reply_text(
        "🎤 **Voice Message Received!**\n\n"
        "Voice transcription is coming in a future update. \n"
        "For now, please type your question and I'll respond instantly! 😊"
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'view_signals':
        if not active_signals:
            await query.edit_message_text(
                "📭 **No Active Signals**\n\n"
                "All signals are currently closed.\n"
                "New signals will appear here automatically! ⚡"
            )
            return
        
        message = "📊 **ACTIVE SIGNALS**\n\n"
        
        for instrument, signal in active_signals.items():
            emoji = "🟢" if signal.current_profit > 0 else "🔴" if signal.current_profit < 0 else "⚪"
            
            message += f"{emoji} **{signal.instrument}** - {signal.side}\n"
            message += f"   Entry: {signal.entry} | TP: {signal.tp} | SL: {signal.sl}\n\n"
        
        message += f"Total: {len(active_signals)} active signals"
        await query.edit_message_text(message)
        
    elif query.data == 'quick_guide':
        guide = """
📚 **Quick Trading Guide**

**Using This Bot:**
1️⃣ Check /signals for active trades
2️⃣ Ask questions in natural language
3️⃣ Get instant FAQ responses
4️⃣ Receive AI-powered analysis

**In Groups:**
• Mention: @{} [your question]
• Reply to my messages
• Use commands like /signals

**Popular Commands:**
/signals - Active signals
/help - Full command list
/stats - Your statistics
/learn - Trading education

**Pro Tips:**
✨ I respond instantly to common questions
✨ Complex questions get deep AI analysis
✨ I remember our conversation context
✨ Set alerts with /alerts

Start by asking me anything! 🚀
""".format(context.bot.username)
        await query.edit_message_text(guide)
        
    elif query.data == 'how_to_use':
        usage = """
💡 **How to Use Me Effectively**

**Best Practices:**

✅ Be specific in questions
✅ Ask about signal rationale
✅ Request risk calculations
✅ Learn trading concepts

**Example Questions:**
• "Analyze the GBPUSD signal"
• "Calculate lot size for $1000, 2% risk"
• "Why was EURUSD signal given?"
• "What's the status of today's trades?"

**Features:**
🤖 Smart AI routing (fast + efficient)
💬 Natural conversation
📊 Automatic signal tracking
📚 Instant FAQ responses
🎯 Personalized experience

**Need help?** Just ask naturally! 
I understand context and remember our chat. 😊
"""
        await query.edit_message_text(usage)
    
    elif query.data == 'performance':
        if not closed_signals:
            await query.edit_message_text(
                "📊 **Performance Stats**\n\n"
                "No closed signals yet to analyze.\n"
                "Stats will appear here once signals are closed! 📈"
            )
            return
        
        wins = len([s for s in closed_signals if s.get('hit_tp')])
        losses = len([s for s in closed_signals if s.get('hit_sl')])
        total = len(closed_signals)
        win_rate = (wins / total * 100) if total > 0 else 0
        
        perf_message = f"""
📊 **Trading Performance**

**Overall Stats:**
✅ Wins: {wins}
❌ Losses: {losses}
📈 Win Rate: {win_rate:.1f}%
🎯 Total Signals: {total}

**Recent Activity:**
Active Now: {len(active_signals)}
Closed Today: {len([s for s in closed_signals if (datetime.now() - datetime.fromisoformat(s['timestamp'])).days == 0])}

Keep following the signals! 🚀
"""
        await query.edit_message_text(perf_message)


async def parse_signal_from_channel(message_text: str) -> Optional[TradingSignal]:
    """Parse trading signal from channel message"""
    try:
        lines = [line.strip() for line in message_text.split('\n') if line.strip()]
        
        signal_data = {}
        instrument = None
        
        for line in lines:
            line_upper = line.upper()
            
            # Detect instrument (currency pair)
            pairs = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 
                    'EURNZD', 'EURAUD', 'GBPAUD', 'CHFJPY', 'NZDJPY', 'GBPNZD']
            for pair in pairs:
                if pair in line_upper.replace('.', '').replace(' ', ''):
                    instrument = pair
                    break
            
            # Detect BUY/SELL
            if 'BUY' in line_upper and 'SELL' not in line_upper:
                signal_data['side'] = 'BUY'
            elif 'SELL' in line_upper:
                signal_data['side'] = 'SELL'
            
            # Extract numeric values
            if 'ENTRY' in line_upper or 'MARKET' in line_upper:
                nums = re.findall(r'\d+\.\d+', line)
                if nums:
                    signal_data['entry'] = float(nums[0])
            
            if 'TAKE PROFIT' in line_upper or 'TP' in line_upper:
                nums = re.findall(r'\d+\.\d+', line)
                if nums:
                    signal_data['tp'] = float(nums[-1])
            
            if 'STOP LOSS' in line_upper or 'SL' in line_upper:
                nums = re.findall(r'\d+\.\d+', line)
                if nums:
                    signal_data['sl'] = float(nums[-1])
        
        # Validate we have all required data
        if instrument and all(k in signal_data for k in ['side', 'entry', 'tp', 'sl']):
            return TradingSignal(
                instrument=instrument,
                side=signal_data['side'],
                entry=signal_data['entry'],
                tp=signal_data['tp'],
                sl=signal_data['sl'],
                timestamp=datetime.now()
            )
        
    except Exception as e:
        logger.error(f"Signal parsing error: {e}")
    
    return None


async def monitor_signal_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Monitor and parse signals from channel"""
    if update.channel_post and update.channel_post.chat_id == SIGNAL_CHANNEL_ID:
        message_text = update.channel_post.text
        
        if message_text:
            signal = await parse_signal_from_channel(message_text)
            
            if signal:
                active_signals[signal.instrument] = signal
                logger.info(f"✅ New signal tracked: {signal.instrument} {signal.side}")


def main():
    """Start the bot"""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("signals", signals_command))
    application.add_handler(CommandHandler("stats", stats_command))
    
    # Button callbacks
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    
    # Channel monitoring for signals
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, monitor_signal_channel))
    
    # Start
    logger.info("🚀 Trade2Retire AI Assistant - PRODUCTION VERSION")
    logger.info("✅ Smart group activation enabled")
    logger.info("✅ Hybrid AI system active (GPT-3.5 + Claude)")
    logger.info("✅ FAQ caching enabled")
    logger.info("✅ Rate limiting enabled")
    logger.info("✅ Channel monitoring active")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()