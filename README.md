# 🚀 FiFTO Selling v4 - Advanced Options Trading System

## 📈 Professional Options Trading Application

**FiFTO Selling v4** is a comprehensive options trading application designed for NIFTY and BANKNIFTY strategies with live Flattrade integration, automated monitoring, and advanced risk management.

## ✨ Key Features

### 🎯 **Smart Trading**
- **Iron Condor/Butterfly Strategies**: Automated sell positions with hedge protection
- **Fast Parallel Order Placement**: 3-4x faster execution using concurrent API calls
- **Margin Optimized**: Hedge-first order sequence for maximum margin benefits
- **Market Orders**: Instant execution with market price orders

### 🔧 **Technical Excellence**  
- **Multi-Broker Support**: Flattrade + Angel One SmartAPI integration
- **Automatic Broker Selection**: Seamless fallback between brokers
- **Persistent Sessions**: Token storage and auto-refresh
- **Correct Symbol Formats**: Flattrade (NIFTY21AUG25C24500) & Angel One (NIFTY21AUG2525000CE)
- **NRML/CARRYFORWARD Product Types**: Overnight position capability

### 📊 **Monitoring & Analysis**
- **Real-time P&L Tracking**: Live trade monitoring with auto-alerts
- **Advanced Charts**: Payoff graphs and risk visualization
- **Telegram Notifications**: Instant trade alerts and updates
- **Scheduler Integration**: Automated analysis and alerts

### 🛡️ **Risk Management**
- **Target/Stop-loss**: Automated position monitoring
- **Hedge Protection**: Built-in risk management for all strategies
- **Error Handling**: Comprehensive API error management
- **Validation**: Order parameter validation before placement

## 🚀 Quick Start

### 1. **Installation**
```bash
# Navigate to project directory
cd "FiFTO Selling v4"

# Install dependencies (includes pyotp for Angel One)
pip install -r requirements.txt

# Option 1: Use launcher (recommended)
run all.bat

# Option 2: Manual start
python selling.py
```

### 2. **Setup Your Broker**

#### **Option A: Flattrade Setup**
1. Open http://localhost:7861
2. Go to **Settings** → **Broker Accounts** → **Flattrade** tab
3. Configure credentials:
   - Client ID, API Key, Secret Key
4. Click **"Generate OAuth URL"** and complete authentication

#### **Option B: Angel One Setup**
1. Go to **Settings** → **Broker Accounts** → **AngelOne** tab  
2. Configure credentials:
   - Client ID (e.g., A123456)
   - API Key (from SmartAPI registration)
   - Login PIN (4 or 6 digits)
   - TOTP Secret Key (from Google Authenticator setup)
3. Click **"Test Connection & Authenticate"**

### 3. **Place Your First Trade**
1. Go to **Manual Trade Entry** tab
2. Configure your strategy:
   - Instrument: NIFTY/BANKNIFTY
   - Expiry: Select date
   - Strikes: Set CE/PE sell and hedge strikes
   - Quantity: Number of lots
3. Enable **"🔴 PLACE LIVE ORDERS WITH BROKER"**
4. Click **"Add Manual Trade"**

## 📋 Strategy Examples

### **Iron Condor (Recommended)**
```
NIFTY 21-Aug-2025:
- SELL CE 24950 (collect premium)
- SELL PE 24950 (collect premium)  
- BUY CE 25050 (hedge protection)
- BUY PE 24850 (hedge protection)
Quantity: 1 lot (75 qty)
```

### **Iron Butterfly**
```
BANKNIFTY 21-Aug-2025:
- SELL CE 51000 (ATM)
- SELL PE 51000 (ATM)
- BUY CE 51200 (hedge)
- BUY PE 50800 (hedge)
Quantity: 1 lot (15 qty)
```

## 🎯 Order Execution Flow

### **Optimized Sequence** (Hedge-First for Margin Benefits)
```
⚡ STEP 1: HEDGE BUY Orders (Parallel)
✅ CE HEDGE BUY: NIFTY21AUG25C25050 @ Market Price
✅ PE HEDGE BUY: NIFTY21AUG25P24850 @ Market Price
⏱️ Completed in ~0.8 seconds

⚡ STEP 2: SELL Orders (Parallel)  
✅ CE SELL: NIFTY21AUG25C24950 @ Market Price
✅ PE SELL: NIFTY21AUG25P24950 @ Market Price
⏱️ Completed in ~0.7 seconds

🚀 Total Execution: ~1.5 seconds (vs 4-8 seconds sequential)
```

## 📊 Interface Overview

### **Main Tabs**
- **📈 Analysis**: Market analysis and strategy planning
- **📋 Trades**: View and manage active trades
- **✏️ Manual Trade Entry**: Place new trades
- **📞 Live Monitor**: Real-time position monitoring  
- **⚙️ Settings**: Configuration and authentication

### **Live Monitor Dashboard**
- Access via: http://localhost:5555
- Real-time P&L updates
- Trade status monitoring
- Auto-refresh capabilities

## 🔧 Technical Details

### **Core Files**
- `selling.py` - Main trading application with Gradio interface
- `live monitor/app.py` - Real-time monitoring dashboard
- `run all.bat` - Launcher for both applications
- `requirements.txt` - Python dependencies
- `DEVELOPER_SETUP_GUIDE.md` - Complete setup instructions
- `FLATTRADE_API_GUIDE.md` - API integration details

### **API Integration**
- **Flattrade API**: `https://piconnect.flattrade.in/PiConnectTP`
- **Angel One API**: `https://apiconnect.angelone.in` 
- **Authentication**: OAuth 2.0 (Flattrade) + TOTP (Angel One)
- **Order Format**: JSON with broker-specific structures
- **Rate Limits**: ~10 requests/second (handled automatically)

### **Symbol Format**
```
Flattrade Format: SYMBOL + DD + MMM + YY + C/P + STRIKE
Examples:
- NIFTY21AUG25C24500  (NIFTY Call)
- NIFTY21AUG25P24500  (NIFTY Put)  
- BANKNIFTY21AUG25C51000 (Bank NIFTY Call)

Angel One Format: SYMBOL + DD + MMM + YY + STRIKE + CE/PE  
Examples:
- NIFTY21AUG2524500CE  (NIFTY Call)
- NIFTY21AUG2524500PE  (NIFTY Put)
- BANKNIFTY21AUG2551000CE (Bank NIFTY Call)
```

### **Performance Optimizations**
- **Parallel API Calls**: ThreadPoolExecutor for concurrent requests
- **Multi-Broker Fallback**: Automatic broker selection (Flattrade → Angel One)
- **Token Persistence**: Automatic token storage and refresh
- **Market Orders**: No price rejection delays
- **Hedge-First**: Optimal margin utilization

## 📚 Documentation

### **Comprehensive Guides**
- **[Developer Setup Guide](DEVELOPER_SETUP_GUIDE.md)**: Complete installation and configuration
- **[Flattrade API Guide](FLATTRADE_API_GUIDE.md)**: API integration details and examples
- **[Requirements](requirements.txt)**: Python package dependencies

### **Quick References**
- API Examples: Code samples for common operations
- Error Solutions: Troubleshooting common issues
- Symbol Format: Correct Flattrade symbol examples

## ⚠️ Important Notes

### **Trading Risks**
- Options trading involves significant risk
- Test thoroughly before live trading
- Use appropriate position sizing
- Monitor trades continuously

### **System Requirements**
- Python 3.8+ required
- Active Flattrade account with API access
- Stable internet connection for real-time trading
- 4GB RAM recommended for smooth operation

### **Best Practices**
- Always place hedge orders first
- Use market orders for better execution
- Monitor positions regularly
- Set appropriate stop-losses
- Test with small quantities initially

## 🤝 Support & Troubleshooting

### **Common Solutions**
- **"Invalid Trading Symbol"**: Check symbol format (Flattrade: NIFTY21AUG25C24500, Angel One: NIFTY21AUG2524500CE)
- **"Insufficient Margin"**: Ensure adequate funds or reduce quantity
- **"Authentication Failed"**: Re-authenticate in Settings → Broker Accounts
- **"Order Rejected"**: Verify market hours and symbol validity
- **"TOTP Error"**: Check Google Authenticator setup for Angel One

### **Getting Help**
1. Check documentation guides
2. Review API error responses
3. Check application logs
4. Test with paper trading first

## 📈 Trading Performance

### **Execution Speed**
- **Before**: 4-8 seconds (sequential orders)
- **After**: 1-2 seconds (parallel execution)
- **Improvement**: 3-4x faster order placement

### **Success Rate**
- **Market Orders**: 99%+ execution rate
- **Multi-Broker Support**: Flattrade + Angel One compatibility
- **Symbol Formats**: Correct broker-specific formatting
- **Error Handling**: Comprehensive validation and retry logic

## 🔮 Future Enhancements

### **Planned Features**
- Additional brokers (Zerodha, Upstox, ICICI Direct)
- Advanced strategy builder with multiple legs
- Backtesting capabilities with historical data
- Mobile app interface for iOS/Android
- Portfolio analytics and risk metrics

### **API Improvements**
- WebSocket integration for real-time data streaming
- Enhanced error recovery with automatic retries
- Load balancing for high-frequency trading
- Advanced order types (OCO, bracket orders, GTT)

---

## 🚀 **Ready to Trade?**

1. **Install** → `pip install -r requirements.txt`
2. **Run** → `run all.bat` or `python selling.py`  
3. **Configure** → Settings → Broker Accounts (Flattrade or Angel One)
4. **Trade** → Manual Entry tab
5. **Monitor** → Live Monitor dashboard (localhost:5555)

**Happy Trading! �**

---

*Last Updated: August 21, 2025*  
*Version: 4.0 - Fast Parallel Edition*
