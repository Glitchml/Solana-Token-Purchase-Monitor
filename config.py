from datetime import datetime, timezone

# Telegram Configuration
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_GROUP_ID = ""

# Helius API Configuration
HELIUS_API_KEY = ""
HELIUS_API_ENDPOINT = "https://api.helius.xyz/v0/addresses/{address}/transactions"
HELIUS_WS_ENDPOINT = "wss://rpc.helius.xyz/?api-key="
HELIUS_BALANCE_ENDPOINT = "https://api.helius.xyz/v0/addresses/{address}/balances"
HELIUS_TOKEN_ENDPOINT = "https://api.helius.xyz/v0/token-balances/{address}"

# Bot Information
INIT_TIME = datetime(2024, 12, 26, 7, 9, 41, tzinfo=timezone.utc)
INIT_USER = "Glitchml"

# Token Configuration
IGNORED_TOKENS = {
    "So11111111111111111111111111111111111111112",  # SOL token
}

# Command Descriptions
COMMANDS = {
    'set_wallet': 'Set wallet address to monitor. Usage: /set_wallet [wallet_address]',
    'start_monitoring': 'Start monitoring the set wallet address',
    'stop_monitoring': 'Stop monitoring the wallet address',
    'status': 'Display current monitoring status',
    'help': 'Show this help message',
    'analyze': 'Analyze wallet token purchases. Usage: /analyze <1h/1d/1w>'
}

# API Settings
API_SETTINGS = {
    'read_timeout': 30,
    'write_timeout': 30,
    'connect_timeout': 30,
    'max_retries': 3,
    'polling_interval': 0.5,
    'balance_update_interval': 30
}
