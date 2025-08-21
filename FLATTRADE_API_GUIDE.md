# 🔗 Flattrade API Integration Guide

## 📋 Table of Contents
- [API Overview](#api-overview)
- [Authentication](#authentication)
- [Order Management](#order-management)
- [Symbol Format](#symbol-format)
- [Error Handling](#error-handling)
- [Rate Limits](#rate-limits)
- [Code Examples](#code-examples)
- [Best Practices](#best-practices)

## 🌐 API Overview

### Base Information
- **Base URL**: `https://piconnect.flattrade.in/PiConnectTP`
- **Protocol**: HTTPS REST API
- **Authentication**: OAuth 2.0 + HMAC SHA-256
- **Data Format**: JSON
- **Rate Limits**: ~10 requests/second

### Supported Operations
- User authentication
- Order placement (Buy/Sell)
- Order modification/cancellation
- Position retrieval
- Order book access
- Market data (limited)

## 🔐 Authentication

### 1. OAuth Setup
```python
class FlattradeAPI:
    def __init__(self, client_id, api_key, secret_key, redirect_uri):
        self.client_id = client_id
        self.api_key = api_key
        self.secret_key = secret_key
        self.redirect_uri = redirect_uri
        self.base_url = "https://piconnect.flattrade.in/PiConnectTP"
```

### 2. Generate Auth URL
```python
def generate_auth_url(self):
    """Generate OAuth authorization URL"""
    auth_url = f"https://auth.flattrade.in/?app_key={self.api_key}"
    return auth_url
```

### 3. Token Exchange
```python
def get_access_token(self, request_token):
    """Exchange request token for access token"""
    
    # Create API call payload
    payload = f"api_key={self.api_key}&request_token={request_token}&api_secret={self.secret_key}"
    
    # Generate checksum
    checksum = hashlib.sha256(payload.encode()).hexdigest()
    
    data = {
        'api_key': self.api_key,
        'request_token': request_token,
        'checksum': checksum
    }
    
    response = requests.post(f"{self.base_url}/QuickAuth", data=data)
    return response.json()
```

### 4. API Request Authentication
```python
def make_api_request(self, endpoint, data):
    """Make authenticated API request"""
    
    # Add standard fields
    data.update({
        'uid': self.user_id,
        'actid': self.user_id
    })
    
    # Convert to JSON and create jData
    jdata = json.dumps(data)
    
    # Generate jKey (HMAC SHA-256)
    jkey = hmac.new(
        self.secret_key.encode('utf-8'),
        jdata.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # Make request
    payload = f"jData={jdata}&jKey={jkey}"
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    
    response = requests.post(
        f"{self.base_url}/{endpoint}",
        data=payload,
        headers=headers
    )
    
    return response.json()
```

## 📈 Order Management

### 1. Place Order
```python
def place_order(self, symbol, quantity, price=None, order_type='MKT', 
               product='NRML', transaction_type='B', exchange='NFO'):
    """Place a new order"""
    
    # Map order types
    price_type_map = {
        'MKT': 'MKT',      # Market order
        'LMT': 'LMT',      # Limit order
        'LIMIT': 'LMT',
        'MARKET': 'MKT'
    }
    
    # Map product types
    product_map = {
        'MIS': 'I',        # Intraday
        'NRML': 'M',       # Normal (overnight)
        'CNC': 'C'         # Cash and Carry
    }
    
    order_data = {
        'uid': self.user_id,
        'actid': self.user_id,
        'exch': exchange,          # NFO for F&O
        'tsym': symbol,            # Trading symbol
        'qty': str(quantity),
        'prc': str(price) if price else '0',
        'prd': product_map.get(product, 'M'),
        'trantype': transaction_type,  # B=Buy, S=Sell
        'prctyp': price_type_map.get(order_type.upper(), 'LMT'),
        'ret': 'DAY',              # DAY order
        'dscqty': '0',             # Disclosed quantity
        'ordersource': 'API'
    }
    
    return self.make_api_request('PlaceOrder', order_data)
```

### 2. Modify Order
```python
def modify_order(self, order_no, quantity=None, price=None, order_type=None):
    """Modify existing order"""
    
    modify_data = {
        'norenordno': order_no,    # Order number to modify
        'exch': 'NFO',
        'qty': str(quantity) if quantity else None,
        'prc': str(price) if price else None,
        'prctyp': order_type if order_type else None
    }
    
    # Remove None values
    modify_data = {k: v for k, v in modify_data.items() if v is not None}
    
    return self.make_api_request('ModifyOrder', modify_data)
```

### 3. Cancel Order
```python
def cancel_order(self, order_no):
    """Cancel existing order"""
    
    cancel_data = {
        'norenordno': order_no
    }
    
    return self.make_api_request('CancelOrder', cancel_data)
```

### 4. Get Orders
```python
def get_orders(self):
    """Get order book"""
    
    return self.make_api_request('OrderBook', {})

def get_order_history(self, order_no):
    """Get single order history"""
    
    data = {'norenordno': order_no}
    return self.make_api_request('SingleOrdHist', data)
```

### 5. Get Positions
```python
def get_positions(self):
    """Get current positions"""
    
    return self.make_api_request('PositionBook', {})

def get_holdings(self):
    """Get holdings"""
    
    return self.make_api_request('Holdings', {})
```

## 🏷️ Symbol Format

### Options Symbol Format
```
Format: [UNDERLYING][DD][MMM][YY][C/P][STRIKE]
Examples:
- NIFTY21AUG25C24500  (NIFTY Call option)
- NIFTY21AUG25P24500  (NIFTY Put option)
- BANKNIFTY21AUG25C51000 (Bank NIFTY Call)
```

### Symbol Generation
```python
def generate_option_symbol(instrument, expiry_date, option_type, strike):
    """Generate Flattrade option symbol"""
    
    # Parse expiry date
    date_obj = datetime.strptime(expiry_date, '%d-%b-%Y')
    
    # Format components
    day = date_obj.strftime('%d')
    month = date_obj.strftime('%b').upper()
    year = date_obj.strftime('%y')
    
    # Option type mapping
    option_code = 'C' if option_type == 'CE' else 'P'
    
    # Generate symbol
    symbol = f"{instrument}{day}{month}{year}{option_code}{int(strike)}"
    
    return symbol

# Examples
symbol1 = generate_option_symbol("NIFTY", "21-Aug-2025", "CE", 24500)
# Returns: NIFTY21AUG25C24500

symbol2 = generate_option_symbol("BANKNIFTY", "21-Aug-2025", "PE", 51000)
# Returns: BANKNIFTY21AUG25P51000
```

### Exchange Mapping
```python
EXCHANGE_MAP = {
    'NIFTY': 'NFO',        # NSE F&O
    'BANKNIFTY': 'NFO',
    'FINNIFTY': 'NFO',
    'EQUITY': 'NSE',       # NSE Cash
    'BSE_EQUITY': 'BSE'    # BSE Cash
}
```

## ⚠️ Error Handling

### Common API Responses
```python
# Success Response
{
    "stat": "Ok",
    "norenordno": "24082100001",  # Order number
    "requestTime": "12:34:56 21-08-2025"
}

# Error Response
{
    "stat": "Not_Ok",
    "emsg": "Invalid Trading Symbol"
}
```

### Error Codes and Solutions
```python
ERROR_SOLUTIONS = {
    "Invalid Trading Symbol": "Check symbol format: NIFTY21AUG25C24500",
    "Insufficient funds": "Check available margin/balance",
    "RMS:Margin Validation": "Insufficient margin for this trade",
    "Order not found": "Order may be already executed/cancelled",
    "Invalid session": "Re-authenticate with Flattrade",
    "Rate limit exceeded": "Reduce API call frequency"
}

def handle_api_error(response):
    """Handle API error responses"""
    
    if isinstance(response, dict) and response.get('stat') == 'Not_Ok':
        error_msg = response.get('emsg', 'Unknown error')
        
        # Log detailed error
        print(f"❌ API Error: {error_msg}")
        
        # Check for common solutions
        if error_msg in ERROR_SOLUTIONS:
            print(f"💡 Solution: {ERROR_SOLUTIONS[error_msg]}")
        
        return False, error_msg
    
    return True, response
```

## ⚡ Rate Limits

### Rate Limit Guidelines
```python
import time
from collections import deque

class RateLimiter:
    def __init__(self, max_calls=10, time_window=1):
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = deque()
    
    def wait_if_needed(self):
        """Wait if rate limit would be exceeded"""
        now = time.time()
        
        # Remove old calls outside time window
        while self.calls and self.calls[0] <= now - self.time_window:
            self.calls.popleft()
        
        # Wait if too many calls
        if len(self.calls) >= self.max_calls:
            sleep_time = self.time_window - (now - self.calls[0])
            if sleep_time > 0:
                time.sleep(sleep_time)
        
        # Record this call
        self.calls.append(now)

# Usage
rate_limiter = RateLimiter(max_calls=8, time_window=1)

def make_rate_limited_request(endpoint, data):
    rate_limiter.wait_if_needed()
    return make_api_request(endpoint, data)
```

## 💻 Code Examples

### Complete Order Placement Example
```python
def place_iron_condor_fast(ce_sell_strike, pe_sell_strike, 
                          ce_buy_strike, pe_buy_strike, quantity):
    """Place Iron Condor with parallel execution"""
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    # Generate symbols
    base_symbol = "NIFTY21AUG25"
    symbols = {
        'ce_buy': f"{base_symbol}C{ce_buy_strike}",
        'pe_buy': f"{base_symbol}P{pe_buy_strike}",
        'ce_sell': f"{base_symbol}C{ce_sell_strike}",
        'pe_sell': f"{base_symbol}P{pe_sell_strike}"
    }
    
    # Step 1: Place BUY orders (hedges) in parallel
    buy_orders = [
        {'symbol': symbols['ce_buy'], 'transaction_type': 'B'},
        {'symbol': symbols['pe_buy'], 'transaction_type': 'B'}
    ]
    
    results = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        buy_futures = {
            executor.submit(
                api.place_order,
                symbol=order['symbol'],
                quantity=quantity,
                order_type='MKT',
                transaction_type=order['transaction_type'],
                product='NRML'
            ): order for order in buy_orders
        }
        
        for future in as_completed(buy_futures):
            order_info = buy_futures[future]
            result = future.result()
            results.append({
                'order': order_info,
                'response': result
            })
    
    # Step 2: Place SELL orders after hedges
    sell_orders = [
        {'symbol': symbols['ce_sell'], 'transaction_type': 'S'},
        {'symbol': symbols['pe_sell'], 'transaction_type': 'S'}
    ]
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        sell_futures = {
            executor.submit(
                api.place_order,
                symbol=order['symbol'],
                quantity=quantity,
                order_type='MKT',
                transaction_type=order['transaction_type'],
                product='NRML'
            ): order for order in sell_orders
        }
        
        for future in as_completed(sell_futures):
            order_info = sell_futures[future]
            result = future.result()
            results.append({
                'order': order_info,
                'response': result
            })
    
    return results
```

### Position Monitoring
```python
def monitor_positions():
    """Monitor current positions"""
    
    try:
        positions = api.get_positions()
        
        if positions.get('stat') == 'Ok':
            active_positions = []
            
            for position in positions.get('data', []):
                if int(position.get('netqty', 0)) != 0:
                    active_positions.append({
                        'symbol': position.get('tsym'),
                        'quantity': position.get('netqty'),
                        'pnl': position.get('urmtom', 0),
                        'ltp': position.get('lp', 0)
                    })
            
            return active_positions
        else:
            print(f"Error fetching positions: {positions.get('emsg')}")
            return []
            
    except Exception as e:
        print(f"Exception in position monitoring: {e}")
        return []
```

## 🎯 Best Practices

### 1. Connection Management
```python
class FlattradeAPIManager:
    def __init__(self):
        self.api = None
        self.last_auth_time = None
        self.auth_timeout = 3600  # 1 hour
    
    def ensure_authenticated(self):
        """Ensure API is authenticated"""
        now = time.time()
        
        if (self.last_auth_time is None or 
            now - self.last_auth_time > self.auth_timeout):
            success, message = self.api.authenticate()
            if success:
                self.last_auth_time = now
            return success, message
        
        return True, "Already authenticated"
```

### 2. Order Validation
```python
def validate_order_params(symbol, quantity, price, order_type):
    """Validate order parameters before placement"""
    
    errors = []
    
    # Symbol validation
    if not symbol or len(symbol) < 5:
        errors.append("Invalid symbol format")
    
    # Quantity validation
    if quantity <= 0:
        errors.append("Quantity must be positive")
    
    # Price validation for limit orders
    if order_type in ['LMT', 'LIMIT'] and (not price or price <= 0):
        errors.append("Price required for limit orders")
    
    # Lot size validation
    lot_sizes = {'NIFTY': 75, 'BANKNIFTY': 15, 'FINNIFTY': 40}
    for underlying, lot_size in lot_sizes.items():
        if underlying in symbol and quantity % lot_size != 0:
            errors.append(f"{underlying} orders must be in multiples of {lot_size}")
    
    return len(errors) == 0, errors
```

### 3. Retry Logic
```python
def api_call_with_retry(func, max_retries=3, delay=1):
    """Execute API call with retry logic"""
    
    for attempt in range(max_retries):
        try:
            result = func()
            
            if isinstance(result, dict) and result.get('stat') == 'Ok':
                return result
            elif attempt < max_retries - 1:
                print(f"Attempt {attempt + 1} failed, retrying in {delay}s...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                return result
                
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                print(f"Network error, retrying in {delay}s...")
                time.sleep(delay)
                delay *= 2
            else:
                raise e
    
    return None
```

### 4. Logging and Monitoring
```python
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('flattrade_api.log'),
        logging.StreamHandler()
    ]
)

def log_api_call(endpoint, data, response):
    """Log API calls for debugging"""
    
    # Remove sensitive data
    safe_data = data.copy()
    if 'jKey' in safe_data:
        safe_data['jKey'] = '[HIDDEN]'
    
    logging.info(f"API Call: {endpoint}")
    logging.info(f"Request: {safe_data}")
    logging.info(f"Response: {response}")
```

## 🔧 Testing

### Mock API for Testing
```python
class MockFlattradeAPI:
    """Mock API for testing without real trades"""
    
    def __init__(self):
        self.order_counter = 1000
    
    def place_order(self, **kwargs):
        """Mock order placement"""
        self.order_counter += 1
        
        return {
            'stat': 'Ok',
            'norenordno': str(self.order_counter),
            'requestTime': datetime.now().strftime('%H:%M:%S %d-%m-%Y')
        }
    
    def get_positions(self):
        """Mock positions"""
        return {
            'stat': 'Ok',
            'data': [
                {
                    'tsym': 'NIFTY21AUG25C24500',
                    'netqty': '75',
                    'urmtom': '150.00',
                    'lp': '45.50'
                }
            ]
        }

# Use mock for testing
if TESTING_MODE:
    api = MockFlattradeAPI()
else:
    api = FlattradeAPI(client_id, api_key, secret_key, redirect_uri)
```

## 📚 Additional Resources

### Official Documentation
- [Flattrade API Docs](https://flattrade.in/api-documentation)
- [OAuth 2.0 Flow](https://tools.ietf.org/html/rfc6749)

### Python Libraries
- `requests` for HTTP calls
- `hashlib` for HMAC generation
- `concurrent.futures` for parallel execution
- `time` for rate limiting

### Market Data APIs (Alternative)
- Yahoo Finance (`yfinance`)
- Alpha Vantage
- Quandl
- NSE/BSE official APIs

---

**Happy API Integration! 🚀**

*Last Updated: August 21, 2025*
