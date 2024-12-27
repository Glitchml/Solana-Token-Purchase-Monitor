# Solana Token Purchase Monitor

## Overview
A Telegram bot that monitors Solana wallet addresses for new token purchases in real-time.

## Important Setup Steps
1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Configure settings in `config.py`:
- Add Telegram Bot Token
- Set Group ID
- Configure Helius API Key

## Commands
- `/set_wallet [address]` - Set wallet address to monitor
- `/start_monitoring` - Begin monitoring
- `/stop_monitoring` - Stop monitoring
- `/status` - Check bot status
- `/analyze <1h/1d/1w>` - Analyze wallet history
- `/help` - Show available commands

## Alert Format
```
🟢 New Token Purchase!
Token: [Symbol]
Amount: [Amount]
CA: [Contract Address]
```

## Technical Details

### APIs Used
- Helius API for Solana transactions
- Telegram Bot API for notifications
- DexScreener for token information

### Key Features
- Efficient token tracking
- Real-time transaction monitoring
- Duplicate prevention system
- Automatic balance updates
- Error handling and recovery

## Requirements
- Python 3.7+
- Active internet connection
- Helius API key
- Telegram Bot token
- Solana wallet address to monitor

## Installation
1. Clone the repository
2. Install requirements:
```bash
pip install -r requirements.txt
```
3. Configure `config.py`
4. Run the bot:
```bash
python main.py
```

## Configuration
Edit `config.py` to set:
- Telegram credentials
- API keys
- Monitoring settings
- Token ignore list

## Error Handling
- API connection retries
- Automatic reconnection
- Transaction validation
- Error logging
- Graceful shutdown

## Performance
- Sub-second alert delivery
- Minimal resource usage
- Efficient caching
- Optimized API calls
- Parallel processing

## Security Notes
- Keep API keys secure
- Don't share config.py
- Monitor API usage
- Regular updates recommended

## Troubleshooting
- Check API keys
- Verify wallet address
- Monitor error logs
- Ensure proper permissions
- Check internet connectivity

## Credits
- Developed with assistance from Claude AI
- Uses python-telegram-bot
- Powered by Helius API
- Solana blockchain integration

## License
MIT License
