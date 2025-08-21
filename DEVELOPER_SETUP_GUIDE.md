# 🚀 FiFTO Selling v4 - Developer Setup Guide

## 📋 Table of Contents
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Application](#running-the-application)
- [Development Environment](#development-environment)
- [Troubleshooting](#troubleshooting)
- [Project Structure](#project-structure)

## 🔧 Prerequisites

### System Requirements
- **Python**: 3.8 or higher
- **Operating System**: Windows 10/11 (tested), Linux, macOS
- **Memory**: 4GB RAM minimum, 8GB recommended
- **Storage**: 500MB free space

### Required Accounts
- **Flattrade Account**: Active trading account with API access
- **Telegram Bot** (Optional): For notifications

## 🛠️ Installation

### 1. Clone/Download Project
```bash
# If using Git
git clone <repository-url>
cd "FiFTO Selling v4"

# Or download and extract ZIP file
```

### 2. Install Python Dependencies
```bash
# Install required packages
pip install -r requirements.txt

# Or install manually
pip install gradio pandas matplotlib yfinance requests apscheduler pytz numpy
```

### 3. Verify Installation
```bash
python -c "import gradio, pandas, matplotlib; print('All dependencies installed successfully!')"
```

## ⚙️ Configuration

### 1. Flattrade API Setup
1. **Login to Flattrade Account**
2. **Navigate to API Section**
3. **Generate API Credentials**:
   - Client ID
   - API Key
   - API Secret
   - API Redirect URL (use: `http://localhost:7861/callback`)

### 2. Application Configuration
1. **Start the application** (see Running section below)
2. **Go to Settings tab**
3. **Configure Broker Settings**:
   ```
   Broker: Flattrade
   Client ID: [Your Flattrade Client ID]
   API Key: [Your API Key]
   Secret Key: [Your API Secret]
   User ID: [Your User ID]
   Password: [Your Password]
   TOTP Key: [Your TOTP Secret] (Optional)
   ```

### 3. Telegram Setup (Optional)
1. **Create Telegram Bot**:
   - Message @BotFather on Telegram
   - Create new bot with `/newbot`
   - Get Bot Token

2. **Get Chat ID**:
   - Add bot to your group/channel
   - Send a message
   - Visit: `https://api.telegram.org/bot[BOT_TOKEN]/getUpdates`
   - Find your chat ID in the response

3. **Configure in Settings**:
   ```
   Telegram Enabled: ✓
   Bot Token: [Your Bot Token]
   Chat ID: [Your Chat ID]
   ```

## 🎯 Running the Application

### 1. Start the Application
```bash
cd "c:\Users\[USERNAME]\Desktop\Hari Dhan\FiFTO Selling v3\FiFTO Selling v4"
python selling.py
```

### 2. Access the Interface
- **Local URL**: http://localhost:7861
- **Browser**: Will open automatically
- **Interface**: Gradio web interface

### 3. Authentication Flow
1. **Go to Settings tab**
2. **Click "Authenticate with Flattrade"**
3. **Complete OAuth flow in popup**
4. **Return to application**
5. **Verify connection status**

## 💻 Development Environment

### Recommended IDE Setup
```
Visual Studio Code with extensions:
- Python
- Pylance
- Python Docstring Generator
- GitLens
```

### Code Structure
```
selling.py              # Main application file
├── FlattradeAPI        # API wrapper class
├── Trading Functions   # Order placement logic
├── UI Components       # Gradio interface
├── Data Management     # Trade storage/retrieval
└── Notification System # Telegram integration
```

### Key Classes and Functions
```python
# Main API Class
class FlattradeAPI:
    def authenticate()          # OAuth authentication
    def place_order()          # Order placement
    def get_positions()        # Position retrieval
    def make_api_request()     # Base API call

# Trading Functions
def add_manual_trade_to_db()   # Manual trade entry
def place_live_orders()        # Live order execution
def monitor_trades()           # Trade monitoring

# UI Functions
def create_gradio_interface()  # Main UI setup
def load_trades_for_display()  # Trade display
```

## 🐛 Troubleshooting

### Common Issues

#### 1. Port Already in Use
```bash
# Error: OSError: Cannot find empty port in range: 7861-7861
# Solution: Kill existing process
taskkill /F /IM python.exe
# Or change port in selling.py line 2086
demo.launch(server_port=7862)
```

#### 2. Module Import Errors
```bash
# Error: ModuleNotFoundError: No module named 'gradio'
# Solution: Install missing packages
pip install gradio pandas matplotlib
```

#### 3. Authentication Issues
```bash
# Error: "Flattrade not authenticated"
# Solution: Check credentials and re-authenticate
1. Verify API credentials in Settings
2. Click "Authenticate with Flattrade"
3. Complete OAuth flow
4. Check authentication status
```

#### 4. API Errors
```bash
# Error: "Invalid Trading Symbol"
# Solution: Symbol format issue
- Ensure correct format: NIFTY21AUG25C24950
- Check expiry date format
- Verify strike prices
```

### Debug Mode
Enable detailed logging by adding to the top of `selling.py`:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Performance Optimization
```python
# For faster order placement (already implemented)
- Parallel API calls using ThreadPoolExecutor
- Market orders for immediate execution
- Hedge-first strategy for margin benefits
```

## 📁 Project Structure

```
FiFTO Selling v4/
├── selling.py                 # Main application
├── settings.json              # User settings (auto-generated)
├── data/
│   └── trades.json           # Trade data storage
├── temp_generated_files/     # Temporary charts/files
├── live_monitor/             # Live monitoring data
│   ├── app.py               # Live monitor server
│   ├── active_trades.json   # Active trades data
│   └── templates/
│       └── index.html       # Monitor UI template
├── DEVELOPER_SETUP_GUIDE.md # This file
├── FLATTRADE_API_GUIDE.md   # API documentation
├── LIVE_TRADING_GUIDE.md    # Trading guide
└── README.md                # Basic documentation
```

## 🔧 Advanced Configuration

### Custom Settings File
The application automatically creates `settings.json`:
```json
{
  "flattrade_client_id": "",
  "flattrade_api_key": "",
  "flattrade_secret_key": "",
  "flattrade_user_id": "",
  "flattrade_password": "",
  "flattrade_totp_key": "",
  "telegram_enabled": false,
  "telegram_bot_token": "",
  "telegram_chat_id": "",
  "access_token": "",
  "access_token_expires": ""
}
```

### Environment Variables (Optional)
```bash
# Set environment variables for credentials
export FLATTRADE_CLIENT_ID="your_client_id"
export FLATTRADE_API_KEY="your_api_key"
export FLATTRADE_SECRET_KEY="your_secret_key"
```

### Development Mode
```python
# In selling.py, change line 2086 for development
demo.launch(
    inbrowser=False,      # Don't auto-open browser
    server_name="127.0.0.1",  # Localhost only
    server_port=7861,
    debug=True,           # Enable debug mode
    share=False           # Don't create public link
)
```

## 🚀 Deployment Options

### Local Development
```bash
python selling.py
```

### Production Deployment
```bash
# Use gunicorn or similar WSGI server
pip install gunicorn
# Note: Gradio apps may need special handling for production
```

### Docker Deployment (Optional)
Create `Dockerfile`:
```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 7861
CMD ["python", "selling.py"]
```

## 📚 Additional Resources

- [Flattrade API Documentation](FLATTRADE_API_GUIDE.md)
- [Live Trading Guide](LIVE_TRADING_GUIDE.md)
- [Gradio Documentation](https://gradio.app/docs)
- [Python Threading](https://docs.python.org/3/library/threading.html)

## 🤝 Contributing

### Code Style
- Follow PEP 8 guidelines
- Use meaningful variable names
- Add docstrings to functions
- Comment complex logic

### Testing
```python
# Test API connection
def test_flattrade_connection():
    api = FlattradeAPI()
    result = api.authenticate()
    assert result[0] == True

# Test order placement (use paper trading)
def test_order_placement():
    # Implement with mock data
    pass
```

### Submitting Changes
1. Fork the repository
2. Create feature branch
3. Test thoroughly
4. Submit pull request

## 🆘 Support

### Getting Help
1. **Check this guide first**
2. **Review error messages carefully**
3. **Check application logs**
4. **Test with minimal configuration**

### Reporting Issues
Include:
- Error message (full stack trace)
- Steps to reproduce
- System information
- Configuration (without sensitive data)

---

**Happy Trading! 📈**

*Last Updated: August 21, 2025*
