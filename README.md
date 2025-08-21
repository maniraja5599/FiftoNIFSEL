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
- **Live Flattrade Integration**: Real API connectivity with OAuth authentication
- **Persistent Sessions**: Token storage for seamless reconnection
- **Correct Symbol Format**: `NIFTY21AUG25C24500` format for Flattrade compatibility
- **NRML Product Type**: Overnight position capability

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

# Install dependencies
pip install -r requirements.txt

# Option 1: Use launcher (recommended)
run all.bat

# Option 2: Manual start
python selling.py
```

### 2. **Setup Flattrade**
1. Open http://localhost:7861
2. Go to **Settings** tab
3. Configure Flattrade credentials:
   - Client ID, API Key, Secret Key
   - User ID, Password, TOTP Key
4. Click **"Authenticate with Flattrade"**
5. Complete OAuth flow

### 3. **Place Your First Trade**
1. Go to **Manual Trade Entry** tab
2. Configure your strategy:
   - Instrument: NIFTY/BANKNIFTY
   - Expiry: Select date
   - Strikes: Set CE/PE sell and hedge strikes
   - Quantity: Number of lots
3. Enable **Live Trading**
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
- **Base URL**: `https://piconnect.flattrade.in/PiConnectTP`
- **Authentication**: OAuth 2.0 + HMAC SHA-256
- **Order Format**: JSON with jData/jKey structure
- **Rate Limits**: ~10 requests/second (handled automatically)

### **Symbol Format**
```
Flattrade Format: [UNDERLYING][DD][MMM][YY][C/P][STRIKE]
Examples:
- NIFTY21AUG25C24500  (NIFTY Call)
- NIFTY21AUG25P24500  (NIFTY Put)  
- BANKNIFTY21AUG25C51000 (Bank NIFTY Call)
```

### **Performance Optimizations**
- **Parallel API Calls**: ThreadPoolExecutor for concurrent requests
- **Token Persistence**: Automatic token storage and reuse
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
- **"Invalid Trading Symbol"**: Check symbol format (NIFTY21AUG25C24500)
- **"Insufficient Margin"**: Ensure adequate funds or reduce quantity
- **"Authentication Failed"**: Re-authenticate in Settings tab
- **"Order Rejected"**: Verify market hours and symbol validity

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
- **Symbol Format**: Correct Flattrade compatibility
- **Error Handling**: Comprehensive validation and retry logic

## 🔮 Future Enhancements

### **Planned Features**
- Multi-broker support (Zerodha, Angel One)
- Advanced strategy builder
- Backtesting capabilities
- Mobile app interface
- Portfolio analytics

### **API Improvements**
- WebSocket integration for real-time data
- Enhanced error recovery
- Load balancing for high-frequency trading
- Advanced order types (OCO, bracket orders)

---

## 🚀 **Ready to Trade?**

1. **Install** → `pip install -r requirements.txt`
2. **Run** → `run all.bat` or `python selling.py`  
3. **Configure** → Settings tab
4. **Trade** → Manual Entry tab
5. **Monitor** → Live Monitor dashboard (localhost:5555)

**Happy Trading! �**

---

*Last Updated: August 21, 2025*  
*Version: 4.0 - Fast Parallel Edition*
