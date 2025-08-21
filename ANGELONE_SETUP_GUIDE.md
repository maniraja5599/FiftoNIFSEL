# 🚀 Angel One (AngelOne) Integration Guide

## 📋 Complete Setup Instructions for Angel One SmartAPI

This guide will help you configure Angel One broker integration in FiFTO Selling v4 for live options trading.

## 🎯 Prerequisites

### **1. Angel One Trading Account**
- Active Angel One (Angel Broking) trading account
- F&O trading enabled
- Sufficient margin for options trading

### **2. SmartAPI Registration**
- Access to [Angel One SmartAPI Portal](https://smartapi.angelbroking.com/)
- API app registration completed
- API credentials generated

### **3. TOTP Setup (Important!)**
- Google Authenticator app installed on your phone
- TOTP enabled in your Angel One account
- TOTP secret key noted down

## 🔧 Step-by-Step Configuration

### **Step 1: Create SmartAPI App**

1. **Login to SmartAPI Portal**
   - Visit: https://smartapi.angelbroking.com/
   - Login with your Angel One credentials

2. **Create New App**
   - Go to "My Apps" section
   - Click "Create App"
   - Fill app details:
     - App Name: `FiFTO Trading Bot`
     - Description: `Options trading automation`
     - Redirect URL: `http://localhost:3001/callback`

3. **Get API Credentials**
   - Note down your **API Key** (32-character string)
   - This will be used in FiFTO configuration

### **Step 2: Setup TOTP (Two-Factor Authentication)**

1. **Enable TOTP in Angel One Account**
   - Login to Angel One web platform
   - Go to Profile → Security Settings
   - Enable TOTP/2FA

2. **Configure Google Authenticator**
   - Install Google Authenticator app
   - Scan QR code or enter secret key manually
   - **Important**: Save the secret key string - you'll need this for FiFTO

3. **Test TOTP**
   - Verify TOTP code generation is working
   - Note down the secret key (format: `ABCD1234EFGH5678IJKL`)

### **Step 3: Configure FiFTO Application**

1. **Open FiFTO Application**
   ```bash
   cd "FiFTO Selling v4"
   python selling.py
   ```
   - Access: http://localhost:7861

2. **Navigate to Broker Settings**
   - Go to **Settings** tab
   - Click **Broker Accounts**
   - Select **AngelOne** tab

3. **Enter Credentials**
   ```
   ✅ Enable AngelOne Integration: [Checked]
   📱 Client ID: A123456 (your Angel One client ID)
   🔑 API Key: abcd1234... (from SmartAPI app)
   🔒 Login PIN: 1234 (your 4 or 6 digit PIN)
   🔐 TOTP Secret Key: ABCD1234... (from Google Authenticator setup)
   ```

4. **Save and Test**
   - Click **"Save AngelOne Settings"**
   - Click **"Test Connection & Authenticate"**
   - Verify successful authentication message

## 🚀 Angel One API Features

### **Supported Order Types**
- ✅ **MARKET** - Instant execution at market price
- ✅ **LIMIT** - Execute at specified price or better
- ✅ **STOPLOSS_LIMIT** - Stop loss with limit price
- ✅ **STOPLOSS_MARKET** - Stop loss at market price

### **Product Types**
- ✅ **INTRADAY** (MIS) - Same day square-off
- ✅ **DELIVERY** (CNC) - Cash & Carry for equity
- ✅ **CARRYFORWARD** (NRML) - Normal for F&O (overnight)

### **Symbol Format**
```bash
Angel One Format: SYMBOL + DD + MMM + YY + STRIKE + CE/PE

Examples:
✅ NIFTY21AUG2524500CE  (NIFTY 24500 Call, Aug 21, 2025)
✅ NIFTY21AUG2524500PE  (NIFTY 24500 Put, Aug 21, 2025)
✅ BANKNIFTY21AUG2551000CE (BANKNIFTY 51000 Call)
✅ BANKNIFTY21AUG2551000PE (BANKNIFTY 51000 Put)
```

## 📊 Live Trading Process

### **Order Execution Flow**
1. **Broker Selection**: FiFTO automatically selects Angel One if configured
2. **Symbol Generation**: Converts to Angel One format
3. **Parallel Orders**: HEDGE orders first, then SELL orders
4. **Real-time Status**: Live order tracking with Order IDs

### **Example Trading Session**
```bash
📡 Using AngelOne for live trading
🔧 Symbol base: NIFTY21AUG25
📊 Placing 2 HEDGE orders in parallel...
✅ CE_HEDGE_BUY: NIFTY21AUG2525000CE @ Market Price (Order ID: 230821000001)
✅ PE_HEDGE_BUY: NIFTY21AUG2524000PE @ Market Price (Order ID: 230821000002)
✅ HEDGE orders completed in 0.85 seconds

📊 Placing 2 SELL orders in parallel...
✅ CE_SELL: NIFTY21AUG2524500CE @ Market Price (Order ID: 230821000003)
✅ PE_SELL: NIFTY21AUG2524500PE @ Market Price (Order ID: 230821000004)
✅ SELL orders completed in 0.92 seconds
```

## 🔐 Security & Authentication

### **Token Management**
- **JWT Token**: For API authentication
- **Refresh Token**: For automatic token renewal
- **Feed Token**: For market data access
- **Auto-Refresh**: Tokens automatically refreshed before expiry

### **TOTP Security**
- **Time-based**: 30-second rotating codes
- **Automatic**: FiFTO generates TOTP automatically
- **Backup**: Manual authentication option available
- **Secure Storage**: TOTP secret encrypted in settings

## 🛡️ Error Handling & Troubleshooting

### **Common Issues**

#### **"Authentication Failed"**
```bash
❌ Possible Causes:
- Wrong Client ID or API Key
- Incorrect Login PIN
- Invalid TOTP secret key
- TOTP not enabled in Angel One account

✅ Solutions:
- Verify credentials in Angel One account
- Re-setup TOTP with Google Authenticator
- Check API Key from SmartAPI portal
- Ensure F&O trading is enabled
```

#### **"Invalid Trading Symbol"**
```bash
❌ Issue: Symbol format mismatch
✅ Solution: Angel One uses format like NIFTY21AUG2524500CE
   (NOT NIFTY21AUG25C24500 like Flattrade)
```

#### **"Insufficient Margin"**
```bash
❌ Issue: Not enough margin for options trading
✅ Solutions:
- Add funds to Angel One account
- Reduce position size/quantity
- Check margin requirements for F&O
```

#### **"Order Rejected"**
```bash
❌ Possible Causes:
- Market closed
- Symbol not tradeable
- Lot size mismatch
- Circuit limits

✅ Solutions:
- Trade during market hours (9:15 AM - 3:30 PM)
- Verify correct option symbols
- Use standard lot sizes (NIFTY: 75, BANKNIFTY: 15)
```

### **Debug Information**
- **API Logs**: Check terminal output for detailed errors
- **Order Status**: Use Angel One platform to verify orders
- **Session Check**: Monitor authentication status in settings
- **Manual Test**: Use "Manual Authentication" button

## 📈 Trading Best Practices

### **Risk Management**
- ✅ Start with small quantities (1-2 lots)
- ✅ Always place hedge orders first
- ✅ Monitor positions continuously
- ✅ Set appropriate stop losses
- ✅ Test during market hours

### **Performance Optimization**
- ✅ Use MARKET orders for faster execution
- ✅ Enable parallel order placement
- ✅ Keep TOTP secret secure
- ✅ Monitor API rate limits
- ✅ Regular token refresh

### **Margin Benefits**
- ✅ CARRYFORWARD product type for overnight positions
- ✅ Hedge-first strategy reduces margin requirements
- ✅ Portfolio margining benefits
- ✅ Lower margin than individual legs

## 🔄 Multi-Broker Setup

### **Broker Priority**
FiFTO automatically selects brokers in this order:
1. **Flattrade** (if configured and authenticated)
2. **Angel One** (if configured and authenticated)
3. **Zerodha** (future implementation)

### **Simultaneous Configuration**
- ✅ Configure both Flattrade and Angel One
- ✅ Automatic fallback if one broker fails
- ✅ Seamless switching without restart
- ✅ Independent authentication status

## 📞 Support & Resources

### **Angel One Resources**
- **SmartAPI Documentation**: https://smartapi.angelbroking.com/docs
- **Angel One Support**: 1800-208-2020
- **SmartAPI Forum**: https://smartapi.angelone.in/forum
- **Developer Portal**: https://smartapi.angelbroking.com/

### **FiFTO Support**
- **GitHub Repository**: https://github.com/maniraja5599/FiftoNIFSEL
- **Documentation**: Available in project folder
- **Error Logs**: Check terminal output for debugging

## ✅ Configuration Checklist

### **Pre-Trading Verification**
- [ ] Angel One trading account active
- [ ] F&O trading segment enabled
- [ ] SmartAPI app created and approved
- [ ] API Key generated and copied
- [ ] TOTP setup completed with Google Authenticator
- [ ] TOTP secret key saved securely
- [ ] Login PIN confirmed
- [ ] Sufficient margin in trading account

### **FiFTO Configuration**
- [ ] Angel One integration enabled
- [ ] All credentials entered correctly
- [ ] "Test Connection" successful
- [ ] Authentication status shows "Authenticated"
- [ ] Broker status summary shows "Connected & Authenticated"
- [ ] Test trade placed successfully (small quantity)

### **Live Trading Ready**
- [ ] Market hours confirmed (9:15 AM - 3:30 PM IST)
- [ ] Option symbols verified
- [ ] Lot sizes confirmed (NIFTY: 75, BANKNIFTY: 15)
- [ ] Risk management strategy in place
- [ ] Stop losses planned
- [ ] Live trading checkbox enabled in FiFTO

## 🎯 Quick Start Commands

```bash
# 1. Install dependencies (includes pyotp for TOTP)
pip install -r requirements.txt

# 2. Start FiFTO
python selling.py

# 3. Access web interface
# Open: http://localhost:7861

# 4. Configure Angel One
# Settings → Broker Accounts → AngelOne tab

# 5. Test authentication
# Click "Test Connection & Authenticate"

# 6. Start trading
# Manual Trade Entry → Enable "PLACE LIVE ORDERS" → Add trade
```

---

**⚠️ Important Disclaimer**: Options trading involves substantial risk. Always trade with money you can afford to lose. Test thoroughly with small quantities before scaling up. This software is for educational purposes and provided as-is without warranties.

**🚀 Ready to Trade with Angel One!** 

*Last Updated: August 21, 2025*
