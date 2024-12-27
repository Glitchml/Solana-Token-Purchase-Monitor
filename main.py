# Main imports for the bot's functionality
import os
import json
import time
import aiohttp
import asyncio
from datetime import datetime, timezone
from telegram import Bot, Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, filters, MessageHandler
from typing import Dict, Set
import websockets
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import sys
import signal
import contextlib

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_GROUP_ID, HELIUS_API_KEY,
    HELIUS_API_ENDPOINT, HELIUS_WS_ENDPOINT, HELIUS_BALANCE_ENDPOINT,
    HELIUS_TOKEN_ENDPOINT, INIT_TIME, INIT_USER, IGNORED_TOKENS,
    COMMANDS, API_SETTINGS
)

# Bot Information
INIT_TIME = datetime(2024, 12, 26, 7, 9, 41, tzinfo=timezone.utc)
INIT_USER = "Glitchml"

# Bot state class to store all runtime information
class BotState:
    """Manages the bot's state and runtime information"""
    def __init__(self):
        # Wallet being monitored
        self.wallet_address = None
        # Flag to control monitoring status
        self.is_monitoring = False
        # Active monitoring task
        self.monitoring_task = None
        self.application = None
        self.start_time = datetime.now(timezone.utc)
        self.processed_transactions = set()
        self.last_api_check_success = False
        self.historical_tokens = set()  # Track all tokens ever bought
        self.token_purchases = {}  # Track {mint: last_purchase_time}
        self.retry_count = 0
        self.max_retries = 3
        self.token_cache = set()  # Fast lookup cache for tokens
        self.transaction_queue = deque(maxlen=1000)  # Queue for transaction processing
        self.ws_connection = None
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.cache_lock = asyncio.Lock()
        self.current_context = None  # Add this line to store the context
        self.known_tokens = set()  # Single source of truth for all known tokens
        self.pending_alerts = asyncio.Queue()  # Queue for real-time alerts
        self.token_balances = {}  # Track current token balances
        self.purchased_tokens = set()  # Track only purchased tokens
        self.wallet_tokens = set()  # Current wallet balance tokens

# Create global bot state instance
bot_state = BotState()

# Store tokens we've already seen to avoid duplicates
previously_seen_tokens = set()

# List of tokens to ignore (like SOL)
IGNORED_TOKENS = {
    "So11111111111111111111111111111111111111112",  # SOL token
}

# Available bot commands and their descriptions
COMMANDS = {
    'set_wallet': 'Set wallet address to monitor. Usage: /set_wallet [wallet_address]',
    'start_monitoring': 'Start monitoring the set wallet address',
    'stop_monitoring': 'Stop monitoring the wallet address',
    'status': 'Display current monitoring status',
    'help': 'Show this help message',
    'analyze': 'Analyze wallet token purchases. Usage: /analyze <1h/1d/1w>'
}

async def fetch_transactions_with_pagination(session, address, before=None):
    """Fetch transactions using Helius API with pagination"""
    try:
        params = {
            "api-key": HELIUS_API_KEY,
            "limit": 100,  # Maximum limit per request
            "commitment": "confirmed"  # Ensure transaction finality
        }
        if before:
            params["before"] = before
        
        async with session.get(
            HELIUS_API_ENDPOINT.format(address=address),
            params=params,
            timeout=30  # Add explicit timeout
        ) as response:
            if response.status == 200:
                data = await response.json()
                bot_state.last_api_check_success = True
                bot_state.retry_count = 0
                return data
            elif response.status == 429:  # Rate limit
                print("Rate limit hit, waiting before retry...")
                await asyncio.sleep(2)
                bot_state.retry_count += 1
                if bot_state.retry_count < bot_state.max_retries:
                    return await fetch_transactions_with_pagination(session, address, before)
            else:
                print(f"Error fetching transactions: {response.status}")
                bot_state.last_api_check_success = False
                return None
    except asyncio.TimeoutError:
        print("Request timeout, retrying...")
        bot_state.retry_count += 1
        if bot_state.retry_count < bot_state.max_retries:
            return await fetch_transactions_with_pagination(session, address, before)
        return None
    except Exception as e:
        print(f"Error in fetch_transactions_with_pagination: {e}")
        return None

async def fetch_transactions(session, address):
    return await fetch_transactions_with_pagination(session, address)

def is_nft(token_info):
    """Check if the token is an NFT"""
    if token_info.get('tokenStandard') in ['NonFungible', 'NonFungibleEdition']:
        return True
    if token_info.get('amount') == 1 and token_info.get('decimals', 0) == 0:
        return True
    return False

def is_token_purchase(transaction):
    """Analyze if the transaction is a token purchase"""
    try:
        if transaction.get('type') == 'SWAP':
            token_transfers = transaction.get('tokenTransfers', [])
            if token_transfers:
                for transfer in token_transfers:
                    if transfer.get('toUserAccount') == bot_state.wallet_address:
                        return {
                            'mint': transfer.get('mint'),
                            'amount': float(transfer.get('tokenAmount', 0)),
                            'symbol': transfer.get('symbol', 'Unknown'),
                            'timestamp': transaction.get('timestamp', 0)
                        }
        return None
    except Exception as e:
        print(f"Error in is_token_purchase: {e}")
        return None

async def process_wallet_history(session, address):
    """Fast initial token history build"""
    try:
        async with session.get(
            f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={HELIUS_API_KEY}"
        ) as response:
            if response.status == 200:
                transactions = await response.json()
                for tx in transactions:
                    if tx.get('type') == 'SWAP':
                        for transfer in tx.get('tokenTransfers', []):
                            if transfer.get('toUserAccount') == address:
                                bot_state.known_tokens.add(transfer.get('mint'))
        print(f"Loaded {len(bot_state.known_tokens)} historical tokens")
    except Exception as e:
        print(f"Error loading history: {e}")

async def connect_helius_websocket():
    """Connect to Helius WebSocket for real-time transaction updates"""
    ws_url = f"{HELIUS_WS_ENDPOINT}{HELIUS_API_KEY}"
    
    try:
        async with websockets.connect(ws_url) as websocket:
            bot_state.ws_connection = websocket
            
            # Subscribe to account updates using proper RPC method
            subscribe_message = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "accountSubscribe",
                "params": [
                    bot_state.wallet_address,
                    {
                        "encoding": "jsonParsed",
                        "commitment": "confirmed"
                    }
                ]
            }
            
            await websocket.send(json.dumps(subscribe_message))
            response = await websocket.recv()
            print(f"Subscription response: {response}")
            
            while bot_state.is_monitoring:
                try:
                    msg = await websocket.recv()
                    data = json.loads(msg)
                    if 'params' in data:
                        await process_realtime_transaction(data['params']['result'])
                except websockets.exceptions.ConnectionClosed:
                    print("WebSocket connection closed, reconnecting...")
                    break
                except Exception as e:
                    print(f"Error processing message: {e}")
                    await asyncio.sleep(1)
                    
    except Exception as e:
        print(f"WebSocket connection error: {e}")
        await asyncio.sleep(5)

async def process_realtime_transaction(tx_data):
    """Process real-time transaction data from WebSocket"""
    try:
        if not bot_state.current_context:
            print("No context available for sending notifications")
            return
            
        if not is_valid_transaction(tx_data):
            return
            
        token_info = await extract_token_info(tx_data)
        if not token_info:
            return
            
        async with bot_state.cache_lock:
            if token_info['mint'] in bot_state.token_cache:
                return
                
            # Quick check before detailed processing
            if token_info['mint'] in IGNORED_TOKENS or is_nft(token_info):
                return
            
            # Add to cache immediately to prevent duplicates
            bot_state.token_cache.add(token_info['mint'])
            
            # Queue notification using stored context
            asyncio.create_task(send_token_notification(bot_state.current_context, token_info))
            
    except Exception as e:
        print(f"Error processing realtime transaction: {e}")

async def get_current_tokens(session, address):
    """Get current token holdings efficiently"""
    try:
        async with session.get(
            HELIUS_TOKEN_ENDPOINT.format(address=address),
            params={"api-key": HELIUS_API_KEY}
        ) as response:
            if response.status == 200:
                data = await response.json()
                return {token['mint'] for token in data.get('tokens', [])}
            return set()
    except Exception as e:
        print(f"Error fetching tokens: {e}")
        return set()

async def monitor_transactions(context: ContextTypes.DEFAULT_TYPE):
    """Optimized purchase monitoring"""
    try:
        async with aiohttp.ClientSession() as session:
            # Initialize token sets
            bot_state.wallet_tokens = await get_current_tokens(session, bot_state.wallet_address)
            last_check = time.time()
            last_signature = None

            while bot_state.is_monitoring:
                try:
                    # Update wallet tokens every 30 seconds
                    current_time = time.time()
                    if current_time - last_check >= 30:
                        bot_state.wallet_tokens = await get_current_tokens(
                            session, 
                            bot_state.wallet_address
                        )
                        last_check = current_time

                    # Fetch new transactions
                    transactions = await fetch_transactions(session, bot_state.wallet_address)
                    if not transactions:
                        await asyncio.sleep(1)
                        continue

                    for tx in transactions:
                        # Skip processed transactions
                        if tx.get('signature') in bot_state.processed_transactions:
                            continue

                        # Process only SWAP transactions
                        if tx.get('type') != 'SWAP':
                            continue

                        # Check token transfers
                        for transfer in tx.get('tokenTransfers', []):
                            if transfer.get('toUserAccount') != bot_state.wallet_address:
                                continue

                            mint = transfer.get('mint')
                            if not mint or mint in IGNORED_TOKENS:
                                continue

                            # Skip if token was previously purchased or exists in wallet
                            if mint in bot_state.purchased_tokens or mint in bot_state.wallet_tokens:
                                continue

                            # New token purchase detected
                            bot_state.purchased_tokens.add(mint)
                            bot_state.processed_transactions.add(tx.get('signature'))

                            # Send alert immediately
                            await send_token_notification(context, {
                                'mint': mint,
                                'symbol': transfer.get('symbol', 'Unknown'),
                                'amount': float(transfer.get('tokenAmount', 0)),
                                'timestamp': tx.get('timestamp', 0)
                            })

                    await asyncio.sleep(0.5)  # Minimal delay

                except Exception as e:
                    print(f"Monitor error: {e}")
                    await asyncio.sleep(1)

    except Exception as e:
        print(f"Fatal monitoring error: {e}")
        bot_state.is_monitoring = False

async def process_alerts(context):
    """Dedicated alert processor"""
    while True:
        try:
            token_info = await bot_state.pending_alerts.get()
            await send_token_notification(context, token_info)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Alert processing error: {e}")

async def poll_transactions(context):
    """Fallback polling method"""
    async with aiohttp.ClientSession() as session:
        transactions = await fetch_transactions(session, bot_state.wallet_address)
        if transactions:
            for tx in transactions:
                if tx.get('signature') not in bot_state.processed_transactions:
                    await process_realtime_transaction(tx)

async def scan_historical_transactions(session):
    """Parallel historical transaction scanning"""
    tasks = []
    last_signature = None
    
    while True:
        transactions = await fetch_transactions_with_pagination(session, bot_state.wallet_address, last_signature)
        if not transactions:
            break
            
        # Process transactions in parallel
        for tx in transactions:
            task = asyncio.create_task(process_historical_transaction(tx))
            tasks.append(task)
            
        if len(transactions) < 100:
            break
            
        last_signature = transactions[-1].get('signature')
        
    await asyncio.gather(*tasks)

async def process_historical_transaction(tx):
    """Process single historical transaction"""
    try:
        token_info = is_token_purchase(tx)
        if token_info and token_info['mint']:
            async with bot_state.cache_lock:
                bot_state.token_cache.add(token_info['mint'])
    except Exception as e:
        print(f"Error processing historical tx: {e}")

def is_valid_transaction(tx_data):
    """Quick validation of transaction data"""
    return (
        isinstance(tx_data, dict) and
        tx_data.get('type') == 'SWAP' and
        tx_data.get('signature') not in bot_state.processed_transactions
    )

async def extract_token_info(tx_data):
    """Extract token information with optimized checks"""
    try:
        transfers = tx_data.get('tokenTransfers', [])
        for transfer in transfers:
            if transfer.get('toUserAccount') == bot_state.wallet_address:
                return {
                    'mint': transfer['mint'],
                    'amount': float(transfer['tokenAmount']),
                    'symbol': transfer.get('symbol', 'Unknown'),
                    'timestamp': tx_data['timestamp']
                }
        return None
    except Exception:
        return None

async def send_token_notification(context, token_info):
    """Fast notification sending"""
    try:
        message = (
            "🟢 New Token Purchase!\n"
            f"Token: {token_info['symbol']}\n"
            f"Amount: {token_info['amount']:,.0f}\n"
            f"CA: `{token_info['mint']}`"
        )
        
        keyboard = [[
            InlineKeyboardButton("📊", url=f"https://dexscreener.com/solana/{token_info['mint']}"),
            InlineKeyboardButton("💱", url=f"https://t.me/achilles_trojanbot?start={token_info['mint']}")
        ]]
        
        await context.bot.send_message(
            chat_id=TELEGRAM_GROUP_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )
    except Exception as e:
        print(f"Alert error: {e}")

async def set_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Simplified wallet setup"""
    if not context.args:
        await update.message.reply_text("Usage: `/set_wallet [address]`", parse_mode=ParseMode.MARKDOWN)
        return
    
    wallet_address = context.args[0]
    if len(wallet_address) != 44:
        await update.message.reply_text("❌ Invalid wallet address", parse_mode=ParseMode.MARKDOWN)
        return
    
    # Reset state
    bot_state.wallet_address = wallet_address
    bot_state.purchased_tokens.clear()
    bot_state.processed_transactions.clear()
    bot_state.wallet_tokens.clear()
    
    # Get initial token state
    async with aiohttp.ClientSession() as session:
        bot_state.wallet_tokens = await get_current_tokens(session, wallet_address)
    
    await update.message.reply_text(
        f"✅ Monitoring wallet:\n`{wallet_address}`\n\nFound {len(bot_state.wallet_tokens)} existing tokens",
        parse_mode=ParseMode.MARKDOWN
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command"""
    uptime = datetime.now(timezone.utc) - bot_state.start_time
    status_message = (
        "*🤖 Yin Bot Status*\n\n"
        f"*Monitored Wallet:*\n`{bot_state.wallet_address or 'Not set'}`\n\n"
        f"*Monitoring Active:* {'✅ Yes' if bot_state.is_monitoring else '❌ No'}\n"
        f"*Uptime:* {uptime.days}d {uptime.seconds//3600}h {(uptime.seconds//60)%60}m\n"
        f"*Transactions Processed:* {len(bot_state.processed_transactions)}\n"
        f"*API Status:* {'🟢 Connected' if bot_state.last_api_check_success else '🔴 Error'}"
    )
    await update.message.reply_text(status_message, parse_mode=ParseMode.MARKDOWN)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command"""
    help_text = "*🤖 Yin Bot Commands:*\n\n"
    for command, description in COMMANDS.items():
        help_text += f"*/{command}*\n{description}\n\n"
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def analyze_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Analyze wallet token purchases over time"""
    if not bot_state.wallet_address:
        await update.message.reply_text(
            "❌ Please set a wallet address first using `/set_wallet`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if not context.args or context.args[0] not in ['1h', '1d', '1w']:
        await update.message.reply_text(
            "❌ Please specify a valid time period: 1h, 1d, or 1w",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    loading_message = await update.message.reply_text(
        "🔄 Analyzing wallet transactions... This may take a moment.",
        parse_mode=ParseMode.MARKDOWN
    )
    
    time_period = context.args[0]
    current_time = time.time() * 1000  # Convert to milliseconds
    
    time_windows = {
        '1h': 60 * 60 * 1000,
        '1d': 24 * 60 * 1000,
        '1w': 7 * 24 * 60 * 60 * 1000
    }
    
    cutoff_time = current_time - time_windows[time_period]
    tokens_in_period = set()
    total_transactions = 0
    
    try:
        async with aiohttp.ClientSession() as session:
            last_signature = None
            while True:
                transactions = await fetch_transactions_with_pagination(session, bot_state.wallet_address, last_signature)
                if not transactions or len(transactions) == 0:
                    break
                
                total_transactions += len(transactions)
                oldest_tx_time = float('inf')
                
                for tx in transactions:
                    tx_time = tx.get('timestamp', 0)
                    oldest_tx_time = min(oldest_tx_time, tx_time)
                    
                    if tx_time < cutoff_time:
                        break
                    
                    token_info = is_token_purchase(tx)
                    if token_info and token_info['mint']:
                        if token_info['mint'] not in IGNORED_TOKENS and not is_nft(token_info):
                            tokens_in_period.add((
                                token_info['mint'],
                                token_info['symbol'],
                                token_info['timestamp']
                            ))
                
                if oldest_tx_time < cutoff_time or len(transactions) < 100:
                    break
                
                last_signature = transactions[-1].get('signature')
        
        # Sort tokens by timestamp
        sorted_tokens = sorted(tokens_in_period, key=lambda x: x[2], reverse=True)
        
        period_names = {'1h': 'hour', '1d': 'day', '1w': 'week'}
        analysis_message = (
            f"*📊 Wallet Analysis for the last {period_names[time_period]}*\n\n"
            f"*Wallet Address:*\n`{bot_state.wallet_address}`\n\n"
            f"*Unique Tokens Purchased:* {len(sorted_tokens)}\n"
            f"*Total Historical Tokens:* {len(bot_state.historical_tokens)}\n"
            f"*Total Transactions Analyzed:* {total_transactions}\n\n"
            "*Recent Purchases:*\n"
        )
        
        # Add recent purchases (up to 5)
        for mint, symbol, timestamp in sorted_tokens[:5]:
            tx_time = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
            analysis_message += f"• {symbol} - {tx_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        
        analysis_message += f"\n*Analysis Time:* {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        
        await loading_message.edit_text(
            analysis_message,
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        print(f"Error analyzing wallet: {e}")
        await loading_message.edit_text(
            "❌ Error analyzing wallet transactions. Please try again later.",
            parse_mode=ParseMode.MARKDOWN
        )

import sys
import signal
import contextlib

async def start_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start_monitoring command"""
    if not bot_state.wallet_address:
        await update.message.reply_text(
            "❌ Please set a wallet address first using `/set_wallet`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if bot_state.is_monitoring:
        await update.message.reply_text(
            "ℹ️ Monitoring is already active!",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        bot_state.is_monitoring = True
        bot_state.monitoring_task = asyncio.create_task(monitor_transactions(context))
        await update.message.reply_text(
            "✅ *Started Monitoring*\nTracking wallet for new token purchases!",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        bot_state.is_monitoring = False
        print(f"Error starting monitoring: {e}")
        await update.message.reply_text(
            "❌ Failed to start monitoring. Please try again.",
            parse_mode=ParseMode.MARKDOWN
        )

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop_monitoring command"""
    if not bot_state.is_monitoring:
        await update.message.reply_text(
            "ℹ️ Monitoring is not active!",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        bot_state.is_monitoring = False
        if bot_state.monitoring_task:
            bot_state.monitoring_task.cancel()
            bot_state.monitoring_task = None
        await update.message.reply_text(
            "🛑 *Monitoring Stopped*",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        print(f"Error stopping monitoring: {e}")
        await update.message.reply_text(
            "❌ Error stopping monitoring. Please try again.",
            parse_mode=ParseMode.MARKDOWN
        )

async def run_bot():
    """Initialize and run the bot"""
    stop_event = asyncio.Event()
    
    def signal_handler():
        stop_event.set()
    
    try:
        application = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .read_timeout(30)
            .write_timeout(30)
            .connect_timeout(30)
            .build()
        )
        bot_state.application = application
        
        # Register command handlers
        application.add_handler(CommandHandler("set_wallet", set_wallet))
        application.add_handler(CommandHandler("start_monitoring", start_monitoring))
        application.add_handler(CommandHandler("stop_monitoring", stop_monitoring))
        application.add_handler(CommandHandler("status", status))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("analyze", analyze_wallet))
        
        print("Starting bot...")
        
        # Set up signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda s, f: signal_handler())
        
        # Start the application
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        
        print("Bot is running...")
        # Run until stop event is set
        await stop_event.wait()
        
    except Exception as e:
        print(f"Error starting bot: {e}")
        raise
    finally:
        print("Shutting down...")
        # Clean up
        if bot_state.is_monitoring:
            bot_state.is_monitoring = False
        if bot_state.monitoring_task:
            bot_state.monitoring_task.cancel()
        if bot_state.application:
            with contextlib.suppress(Exception):
                await bot_state.application.stop()
                await bot_state.application.shutdown()

async def get_wallet_token_balances(session, address):
    """Get all token balances for a wallet"""
    try:
        params = {"api-key": HELIUS_API_KEY}
        async with session.get(
            HELIUS_BALANCE_ENDPOINT.format(address=address),
            params=params
        ) as response:
            if response.status == 200:
                data = await response.json()
                return {token['mint']: token['amount'] for token in data.get('tokens', [])}
            return {}
    except Exception as e:
        print(f"Error fetching balances: {e}")
        return {}

def main():
    """Main function to run the bot"""
    if sys.platform == "win32":
        # Use selector event loop policy on Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    print(f"Starting Yin Bot at {datetime.now(timezone.utc)}")
    print(f"Initialized by: {INIT_USER}")
    print("Bot configuration:")
    print(f"- Group ID: {TELEGRAM_GROUP_ID}")
    print("- Monitoring: Not started")
    print("- Status: Initializing...")
    print(f"- Current Time: {datetime.now(timezone.utc)}")
    
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\nBot stopped by user")
    except Exception as e:
        print(f"\nFatal error: {e}")

if __name__ == "__main__":
    main()