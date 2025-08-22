import matplotlib
matplotlib.use('Agg')  # Set non-interactive backend to prevent GUI issues
import matplotlib.pyplot as plt
import pandas as pd
import requests
import yfinance as yf
import math
import time
from datetime import datetime
import gradio as gr
import json
import os
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import pytz
from collections import defaultdict
import numpy as np
import uuid
import traceback
from urllib.parse import urlencode
import hashlib
import hmac
import base64
import webbrowser
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import live auto trading module
from live_auto_trading import initialize_live_trading, get_live_trade_manager, TradingMode

# --- Configuration ---
# Default Telegram credentials (will be overridden by settings)
DEFAULT_BOT_TOKEN = "7476365992:AAGjDcQcMB7lkiy92VoDnZwixatakhe02DI"
DEFAULT_CHAT_ID = "-1002886512293"

# Centralized data and temporary file directories
DATA_DIR = os.path.join(os.path.expanduser('~'), ".fifto_analyzer_data")
TEMP_DIR = "temp_generated_files"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

TRADES_DB_FILE = os.path.join(DATA_DIR, "active_trades.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "app_settings.json")
HISTORICAL_PNL_FILE = os.path.join(DATA_DIR, "historical_pnl.json")


# --- Global Scheduler Initialization ---
scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Kolkata'))

# In-memory store for 5-minute P/L tracking for the daily graph
daily_pnl_tracker = defaultdict(list)

# --- Initialize Live Auto Trading ---
live_trade_manager = initialize_live_trading(DATA_DIR)


# --- Settings Management ---
def load_settings():
    """Loads settings from the settings file, providing defaults if it doesn't exist."""
    defaults = {
        "update_interval": "15 Mins", 
        "schedules": [],
        "telegram_enabled": True,
        "telegram_bot_token": DEFAULT_BOT_TOKEN,
        "telegram_chat_id": DEFAULT_CHAT_ID,
        "brokers": {
            "flattrade": {
                "enabled": False,
                "client_id": "",
                "api_key": "",
                "secret_key": "",
                "access_token": "",
                "redirect_url": "http://localhost:3001/callback"
            },
            "angelone": {
                "enabled": False,
                "client_id": "",
                "api_key": "",
                "secret_key": "",
                "access_token": "",
                "redirect_url": "http://localhost:3001/callback"
            },
            "zerodha": {
                "enabled": False,
                "client_id": "",
                "api_key": "",
                "secret_key": "",
                "access_token": ""
            }
        }
    }
    if not os.path.exists(SETTINGS_FILE): return defaults
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            for key, value in defaults.items(): settings.setdefault(key, value)
            return settings
    except (json.JSONDecodeError, FileNotFoundError): return defaults

def save_settings(settings):
    """Saves the given settings dictionary to the settings file."""
    with open(SETTINGS_FILE, 'w') as f: json.dump(settings, f, indent=4)

def update_telegram_settings(enabled, bot_token, chat_id):
    """Update Telegram configuration settings"""
    try:
        settings = load_settings()
        settings['telegram_enabled'] = enabled
        if bot_token and bot_token.strip():
            settings['telegram_bot_token'] = bot_token.strip()
        if chat_id and chat_id.strip():
            settings['telegram_chat_id'] = chat_id.strip()
        save_settings(settings)
        
        # Create updated status message
        if enabled:
            if bot_token and chat_id:
                status = "**Status:** ✅ **Enabled** - Telegram notifications are active"
            else:
                status = "**Status:** ⚠️ **Enabled but missing credentials** - Please configure Bot Token and Chat ID"
        else:
            status = "**Status:** ❌ **Disabled** - Telegram notifications are turned off"
        
        return f"Telegram settings updated successfully. Notifications {'enabled' if enabled else 'disabled'}.", status
    except Exception as e:
        return f"Error updating Telegram settings: {e}", "**Status:** ❌ **Error** - Failed to update settings"

def test_telegram_connection():
    """Test Telegram connection with current settings"""
    try:
        test_message = "🔔 Test message from FiFTO - Telegram connection is working!"
        result = send_telegram_message(test_message)
        return result
    except Exception as e:
        return f"Telegram test failed: {e}"

def update_telegram_status_on_toggle(enabled, bot_token, chat_id):
    """Update status display when telegram enabled checkbox is toggled"""
    if enabled:
        if bot_token and chat_id:
            return "**Status:** ✅ **Enabled** - Telegram notifications are active"
        else:
            return "**Status:** ⚠️ **Enabled but missing credentials** - Please configure Bot Token and Chat ID"
    else:
        return "**Status:** ❌ **Disabled** - Telegram notifications are turned off"

def load_telegram_settings_for_ui():
    """Load current Telegram settings for UI components"""
    settings = load_settings()
    enabled = settings.get('telegram_enabled', True)
    bot_token = settings.get('telegram_bot_token', '')
    chat_id = settings.get('telegram_chat_id', '')
    
    # Create status message
    if enabled:
        if bot_token and chat_id:
            status = "**Status:** ✅ **Enabled** - Telegram notifications are active"
        else:
            status = "**Status:** ⚠️ **Enabled but missing credentials** - Please configure Bot Token and Chat ID"
    else:
        status = "**Status:** ❌ **Disabled** - Telegram notifications are turned off"
    
    return enabled, bot_token, chat_id, status

# --- Flattrade API Integration ---

# Flattrade API Configuration
FLATTRADE_BASE_URL = 'https://piconnect.flattrade.in/PiConnectTP'
FLATTRADE_AUTH_URL = 'https://auth.flattrade.in'
FLATTRADE_TOKEN_URL = 'https://authapi.flattrade.in/trade/apitoken'

class FlattradeAPI:
    """Flattrade API client for authentication and trading operations"""
    
    def __init__(self, api_key, api_secret, user_id):
        self.api_key = api_key
        self.api_secret = api_secret
        self.user_id = user_id
        self.access_token = None
        self.session_data = {}
    
    def generate_auth_url(self, redirect_uri='http://localhost:3001/callback'):
        """Generate OAuth authorization URL"""
        params = {
            'app_key': self.api_key,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'state': 'fifto_flattrade_auth'
        }
        auth_url = f"{FLATTRADE_AUTH_URL}/?{urlencode(params)}"
        return auth_url
    
    def get_access_token(self, request_code):
        """Exchange authorization code for access token"""
        try:
            # Generate API secret hash: SHA-256 of (api_key + request_code + api_secret)
            hash_string = self.api_key + request_code + self.api_secret
            hashed_secret = hashlib.sha256(hash_string.encode()).hexdigest()
            
            # Request payload
            payload = {
                'api_key': self.api_key,
                'request_code': request_code,
                'api_secret': hashed_secret
            }
            
            # Make API call
            response = requests.post(FLATTRADE_TOKEN_URL, json=payload, timeout=30)
            response.raise_for_status()
            
            token_data = response.json()
            
            if token_data.get('stat') == 'Ok':
                self.access_token = token_data.get('token')
                self.session_data = {
                    'userId': token_data.get('uid', self.user_id),
                    'jKey': self.access_token,
                    'clientId': token_data.get('clientid', ''),
                    'isAuthenticated': True,
                    'loginTime': datetime.now().isoformat()
                }
                return True, "Authentication successful"
            else:
                error_msg = token_data.get('emsg', 'Authentication failed')
                return False, error_msg
                
        except requests.exceptions.RequestException as e:
            return False, f"Network error: {str(e)}"
        except Exception as e:
            return False, f"Authentication error: {str(e)}"
    
    def make_api_request(self, endpoint, data):
        """Make authenticated API request to Flattrade"""
        if not self.access_token:
            return {'stat': 'Not_Ok', 'emsg': 'Not authenticated'}
        
        try:
            # Prepare request data
            request_data = {
                'uid': self.user_id,
                'actid': self.user_id,
                **data
            }
            
            # Make API call
            url = f"{FLATTRADE_BASE_URL}/{endpoint}"
            payload = f"jData={json.dumps(request_data)}&jKey={self.access_token}"
            
            response = requests.post(
                url,
                data=payload,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=30
            )
            
            # Check if response is successful
            if response.status_code != 200:
                error_text = response.text[:500] if response.text else "No error details"
                return {
                    'stat': 'Not_Ok', 
                    'emsg': f'HTTP {response.status_code}: {error_text}'
                }
            
            response.raise_for_status()
            
            response_data = response.json()
            
            # Log the response for debugging
            if response_data.get('stat') != 'Ok':
                print(f"❌ API Error: {response_data.get('emsg', 'Unknown error')}")
                print(f"📊 Request data: {request_data}")
            
            return response_data
            
        except requests.exceptions.RequestException as e:
            return {'stat': 'Not_Ok', 'emsg': f'Network error: {str(e)}'}
        except Exception as e:
            return {'stat': 'Not_Ok', 'emsg': f'API error: {str(e)}'}
    
    def get_user_details(self):
        """Get user account details"""
        return self.make_api_request('UserDetails', {})
    
    def get_limits(self):
        """Get account limits"""
        return self.make_api_request('Limits', {})
    
    def get_positions(self):
        """Get current positions"""
        return self.make_api_request('PositionBook', {})
    
    def get_orders(self):
        """Get order book"""
        return self.make_api_request('OrderBook', {})
    
    def get_holdings(self):
        """Get holdings"""
        return self.make_api_request('Holdings', {})
    
    def place_order(self, symbol, quantity, price=None, order_type='MKT', 
                   product='NRML', transaction_type='B', exchange='NFO'):
        """Place an order"""
        # Map order_type to Flattrade's expected prctyp
        price_type_map = {
            'MKT': 'MKT',
            'LMT': 'LMT', 
            'LIMIT': 'LMT',
            'MARKET': 'MKT'
        }
        
        prctyp = price_type_map.get(order_type.upper(), 'LMT')
        
        # Map product to Flattrade's expected format
        product_map = {
            'MIS': 'I',
            'NRML': 'M', 
            'CNC': 'C'
        }
        
        prd_code = product_map.get(product, 'M')  # Default to NRML (M)
        
        order_data = {
            'uid': self.user_id,  # User ID is required
            'actid': self.user_id,  # Account ID (same as user ID for most cases)
            'exch': exchange,
            'tsym': symbol,
            'qty': str(quantity),
            'prc': str(price) if price else '0',
            'prd': prd_code,  # I for MIS, M for NRML, C for CNC
            'trantype': transaction_type,  # B for Buy, S for Sell
            'prctyp': prctyp,
            'ret': 'DAY',
            'dscqty': '0',
            'ordersource': 'API'
        }
        
        print(f"📊 Placing order: {symbol} | {transaction_type} | Qty: {quantity} | Price: {price} | Type: {prctyp}")
        
        return self.make_api_request('PlaceOrder', order_data)
    
    def get_option_chain(self, symbol='NIFTY', strike_price='24500', count='10'):
        """Get option chain data"""
        chain_data = {
            'exch': 'NFO',
            'tsym': symbol,
            'strprc': strike_price,
            'cnt': count
        }
        
        return self.make_api_request('GetOptionChain', chain_data)

# Global Flattrade API instance
flattrade_api = None

def initialize_flattrade_api(api_key, api_secret, user_id):
    """Initialize Flattrade API client"""
    global flattrade_api
    flattrade_api = FlattradeAPI(api_key, api_secret, user_id)
    return flattrade_api

def authenticate_flattrade(request_code):
    """Authenticate with Flattrade using authorization code"""
    if not flattrade_api:
        return False, "Flattrade API not initialized"
    
    success, message = flattrade_api.get_access_token(request_code)
    
    if success and flattrade_api.access_token:
        # Save the access token to settings for persistence
        try:
            settings = load_settings()
            if 'brokers' not in settings:
                settings['brokers'] = {}
            if 'flattrade' not in settings['brokers']:
                settings['brokers']['flattrade'] = {}
            
            # Store the access token
            settings['brokers']['flattrade']['access_token'] = flattrade_api.access_token
            
            # Save the updated settings
            save_settings(settings)
            
            print(f"✅ Access token saved to settings for persistence")
            
        except Exception as e:
            print(f"⚠️ Warning: Could not save access token to settings: {e}")
            # Authentication is still successful even if saving fails
    
    return success, message

def get_flattrade_session_data():
    """Get current Flattrade session data"""
    if flattrade_api and flattrade_api.access_token:
        return flattrade_api.session_data
    return None

def initialize_flattrade_for_trading():
    """Initialize Flattrade API for trading if credentials and token are available"""
    global flattrade_api
    
    try:
        # Load broker settings
        settings = load_settings()
        flattrade_config = settings.get('brokers', {}).get('flattrade', {})
        
        if not flattrade_config.get('enabled', False):
            return False, "Flattrade broker not enabled"
        
        # Get credentials
        api_key = flattrade_config.get('api_key', '').strip()
        secret_key = flattrade_config.get('secret_key', '').strip()
        client_id = flattrade_config.get('client_id', '').strip()
        
        if not all([api_key, secret_key, client_id]):
            return False, "Missing Flattrade credentials"
        
        # Initialize API if not already done
        if not flattrade_api:
            flattrade_api = FlattradeAPI(api_key, secret_key, client_id)
        
        # Check if we have a saved access token in settings first
        access_token = flattrade_config.get('access_token', '').strip()
        
        if access_token and (not hasattr(flattrade_api, 'access_token') or not flattrade_api.access_token):
            # Use the saved access token
            flattrade_api.access_token = access_token
            print(f"✅ Loaded saved access token from settings")
            return True, "Flattrade API ready for trading with saved token"
        
        # If API already has a token, we're good
        if hasattr(flattrade_api, 'access_token') and flattrade_api.access_token:
            return True, "Flattrade API ready for trading"
        
        # Fallback: Check for auth code file (for backward compatibility)
        auth_code_file = os.path.join(os.path.expanduser('~'), '.fifto_analyzer_data', 'Flattrade_auth_code.txt')
        
        if os.path.exists(auth_code_file):
            with open(auth_code_file, 'r') as f:
                auth_code = f.read().strip()
            
            # Try to get token with saved auth code
            success, message = flattrade_api.get_access_token(auth_code)
            if success:
                # Save the new token to settings
                flattrade_config['access_token'] = flattrade_api.access_token
                settings['brokers']['flattrade'] = flattrade_config
                save_settings(settings)
                print(f"✅ Generated and saved new access token")
                return True, "Flattrade API ready for trading"
            else:
                return False, f"Failed to authenticate with saved auth code: {message}"
        else:
            return False, "No authentication token found. Please complete OAuth first."
            
    except Exception as e:
        return False, f"Error initializing Flattrade API: {str(e)}"

# --- End Flattrade API Integration ---

# --- Angel One API Integration ---

ANGELONE_BASE_URL = 'https://apiconnect.angelone.in'
ANGELONE_LOGIN_URL = 'https://smartapi.angelone.in/publisher-login'

class AngelOneAPI:
    """Angel One SmartAPI client for authentication and trading operations"""
    
    def __init__(self, api_key, client_id, client_pin, totp_key):
        self.api_key = api_key
        self.client_id = client_id
        self.client_pin = client_pin
        self.totp_key = totp_key
        self.access_token = None
        self.jwt_token = None
        self.refresh_token = None
        self.feed_token = None
        self.session_data = {}
    
    def generate_totp(self):
        """Generate TOTP code for authentication"""
        try:
            import pyotp
            totp = pyotp.TOTP(self.totp_key)
            return totp.now()
        except ImportError:
            raise Exception("pyotp library is required for TOTP generation. Install with: pip install pyotp")
        except Exception as e:
            raise Exception(f"Error generating TOTP: {str(e)}")
    
    def generate_auth_url(self, redirect_uri='http://localhost:3001/callback'):
        """Generate OAuth authorization URL"""
        params = {
            'api_key': self.api_key,
            'state': 'fifto_angelone_auth'
        }
        auth_url = f"{ANGELONE_LOGIN_URL}?{urlencode(params)}"
        return auth_url
    
    def authenticate(self):
        """Authenticate with Angel One using credentials"""
        try:
            # Generate TOTP
            totp_code = self.generate_totp()
            
            # Login request data
            login_data = {
                'clientcode': self.client_id,
                'password': self.client_pin,
                'totp': totp_code
            }
            
            # Required headers for Angel One API
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-UserType': 'USER',
                'X-SourceID': 'WEB',
                'X-ClientLocalIP': '127.0.0.1',
                'X-ClientPublicIP': '127.0.0.1',
                'X-MACAddress': '00:00:00:00:00:00',
                'X-PrivateKey': self.api_key
            }
            
            # Make login request
            url = f"{ANGELONE_BASE_URL}/rest/auth/angelbroking/user/v1/loginByPassword"
            response = requests.post(url, json=login_data, headers=headers, timeout=30)
            
            if response.status_code != 200:
                return False, f"HTTP {response.status_code}: {response.text}"
            
            response_data = response.json()
            
            if response_data.get('status'):
                data = response_data.get('data', {})
                self.jwt_token = data.get('jwtToken')
                self.refresh_token = data.get('refreshToken') 
                self.feed_token = data.get('feedToken')
                self.access_token = self.jwt_token  # Use JWT token as access token
                
                self.session_data = {
                    'clientId': self.client_id,
                    'jwtToken': self.jwt_token,
                    'refreshToken': self.refresh_token,
                    'feedToken': self.feed_token,
                    'isAuthenticated': True,
                    'loginTime': datetime.now().isoformat()
                }
                
                return True, "Authentication successful"
            else:
                error_msg = response_data.get('message', 'Authentication failed')
                return False, error_msg
                
        except Exception as e:
            return False, f"Authentication error: {str(e)}"
    
    def refresh_access_token(self):
        """Refresh access token using refresh token"""
        if not self.refresh_token:
            return False, "No refresh token available"
        
        try:
            refresh_data = {
                'refreshToken': self.refresh_token
            }
            
            headers = {
                'Authorization': f'Bearer {self.jwt_token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-UserType': 'USER',
                'X-SourceID': 'WEB',
                'X-ClientLocalIP': '127.0.0.1',
                'X-ClientPublicIP': '127.0.0.1', 
                'X-MACAddress': '00:00:00:00:00:00',
                'X-PrivateKey': self.api_key
            }
            
            url = f"{ANGELONE_BASE_URL}/rest/auth/angelbroking/jwt/v1/generateTokens"
            response = requests.post(url, json=refresh_data, headers=headers, timeout=30)
            
            if response.status_code == 200:
                response_data = response.json()
                if response_data.get('status'):
                    data = response_data.get('data', {})
                    self.jwt_token = data.get('jwtToken')
                    self.refresh_token = data.get('refreshToken')
                    self.feed_token = data.get('feedToken')
                    self.access_token = self.jwt_token
                    return True, "Token refreshed successfully"
            
            return False, "Failed to refresh token"
            
        except Exception as e:
            return False, f"Token refresh error: {str(e)}"
    
    def make_api_request(self, endpoint, data=None, method='POST'):
        """Make authenticated API request to Angel One"""
        if not self.access_token:
            return {'status': False, 'message': 'Not authenticated'}
        
        try:
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-UserType': 'USER',
                'X-SourceID': 'WEB',
                'X-ClientLocalIP': '127.0.0.1',
                'X-ClientPublicIP': '127.0.0.1',
                'X-MACAddress': '00:00:00:00:00:00',
                'X-PrivateKey': self.api_key
            }
            
            url = f"{ANGELONE_BASE_URL}/rest/secure/angelbroking/{endpoint}"
            
            if method.upper() == 'GET':
                response = requests.get(url, headers=headers, timeout=30)
            else:
                response = requests.post(url, json=data, headers=headers, timeout=30)
            
            if response.status_code != 200:
                return {
                    'status': False,
                    'message': f'HTTP {response.status_code}: {response.text[:500]}'
                }
            
            response_data = response.json()
            
            # Log errors for debugging
            if not response_data.get('status'):
                print(f"❌ Angel One API Error: {response_data.get('message', 'Unknown error')}")
                if data:
                    print(f"📊 Request data: {data}")
            
            return response_data
            
        except requests.exceptions.RequestException as e:
            return {'status': False, 'message': f'Network error: {str(e)}'}
        except Exception as e:
            return {'status': False, 'message': f'API error: {str(e)}'}
    
    def get_profile(self):
        """Get user profile"""
        return self.make_api_request('user/v1/getProfile', method='GET')
    
    def get_funds(self):
        """Get funds and margins"""
        return self.make_api_request('user/v1/getRMS', method='GET')
    
    def get_positions(self):
        """Get current positions"""
        return self.make_api_request('portfolio/v1/getPosition', method='GET')
    
    def get_orders(self):
        """Get order book"""
        return self.make_api_request('order/v1/getOrderBook', method='GET')
    
    def get_holdings(self):
        """Get holdings"""
        return self.make_api_request('portfolio/v1/getAllHolding', method='GET')
    
    def place_order(self, symbol, symboltoken, quantity, price=None, order_type='MARKET', 
                   product='INTRADAY', transaction_type='BUY', exchange='NSE'):
        """Place an order"""
        
        # Map our standard order types to Angel One format
        order_type_map = {
            'MKT': 'MARKET',
            'MARKET': 'MARKET',
            'LMT': 'LIMIT',
            'LIMIT': 'LIMIT',
            'SL': 'STOPLOSS_LIMIT',
            'SL-M': 'STOPLOSS_MARKET'
        }
        
        # Map our standard product types to Angel One format
        product_map = {
            'MIS': 'INTRADAY',
            'NRML': 'CARRYFORWARD',
            'CNC': 'DELIVERY',
            'INTRADAY': 'INTRADAY',
            'DELIVERY': 'DELIVERY',
            'CARRYFORWARD': 'CARRYFORWARD'
        }
        
        angelone_order_type = order_type_map.get(order_type.upper(), 'MARKET')
        angelone_product = product_map.get(product.upper(), 'INTRADAY')
        
        order_data = {
            'variety': 'NORMAL',
            'tradingsymbol': symbol,
            'symboltoken': str(symboltoken),
            'transactiontype': transaction_type.upper(),
            'exchange': exchange.upper(),
            'ordertype': angelone_order_type,
            'producttype': angelone_product,
            'duration': 'DAY',
            'quantity': str(quantity)
        }
        
        # Add price for limit orders
        if angelone_order_type in ['LIMIT', 'STOPLOSS_LIMIT'] and price:
            order_data['price'] = str(price)
        
        print(f"📊 Placing order: {symbol} | {transaction_type} | Qty: {quantity} | Price: {price} | Type: {angelone_order_type}")
        
        return self.make_api_request('order/v1/placeOrder', order_data)
    
    def get_ltp(self, exchange, symbol, symboltoken):
        """Get Last Traded Price"""
        ltp_data = {
            'exchange': exchange,
            'tradingsymbol': symbol,
            'symboltoken': str(symboltoken)
        }
        
        return self.make_api_request('order/v1/getLtpData', ltp_data)
    
    def search_scrip(self, exchange, search_term):
        """Search for instruments"""
        search_data = {
            'exchange': exchange,
            'searchscrip': search_term
        }
        
        return self.make_api_request('order/v1/searchScrip', search_data)
    
    def cancel_order(self, order_id):
        """Cancel an order"""
        cancel_data = {
            'variety': 'NORMAL',
            'orderid': order_id
        }
        
        return self.make_api_request('order/v1/cancelOrder', cancel_data)
    
    def modify_order(self, order_id, quantity=None, price=None, order_type=None):
        """Modify an order"""
        modify_data = {
            'variety': 'NORMAL',
            'orderid': order_id
        }
        
        if quantity:
            modify_data['quantity'] = str(quantity)
        if price:
            modify_data['price'] = str(price)
        if order_type:
            order_type_map = {
                'MKT': 'MARKET',
                'MARKET': 'MARKET',
                'LMT': 'LIMIT',
                'LIMIT': 'LIMIT'
            }
            modify_data['ordertype'] = order_type_map.get(order_type.upper(), 'MARKET')
        
        return self.make_api_request('order/v1/modifyOrder', modify_data)

# Global Angel One API instance
angelone_api = None

def initialize_angelone_api(api_key, client_id, client_pin, totp_key):
    """Initialize Angel One API client"""
    global angelone_api
    angelone_api = AngelOneAPI(api_key, client_id, client_pin, totp_key)
    return angelone_api

def authenticate_angelone():
    """Authenticate with Angel One using credentials"""
    if not angelone_api:
        return False, "Angel One API not initialized"
    
    success, message = angelone_api.authenticate()
    
    if success and angelone_api.access_token:
        # Save the access token to settings for persistence
        try:
            settings = load_settings()
            if 'brokers' not in settings:
                settings['brokers'] = {}
            if 'angelone' not in settings['brokers']:
                settings['brokers']['angelone'] = {}
            
            # Store the tokens
            settings['brokers']['angelone']['access_token'] = angelone_api.access_token
            settings['brokers']['angelone']['jwt_token'] = angelone_api.jwt_token
            settings['brokers']['angelone']['refresh_token'] = angelone_api.refresh_token
            settings['brokers']['angelone']['feed_token'] = angelone_api.feed_token
            
            # Save the updated settings
            save_settings(settings)
            
            print(f"✅ Angel One tokens saved to settings for persistence")
            
        except Exception as e:
            print(f"⚠️ Warning: Could not save Angel One tokens to settings: {e}")
            # Authentication is still successful even if saving fails
    
    return success, message

def get_angelone_session_data():
    """Get current Angel One session data"""
    if angelone_api and angelone_api.access_token:
        return angelone_api.session_data
    return None

def initialize_angelone_for_trading():
    """Initialize Angel One API for trading if credentials and token are available"""
    global angelone_api
    
    try:
        # Load broker settings
        settings = load_settings()
        angelone_config = settings.get('brokers', {}).get('angelone', {})
        
        if not angelone_config.get('enabled', False):
            return False, "Angel One broker not enabled"
        
        # Get credentials
        api_key = angelone_config.get('api_key', '').strip()
        client_id = angelone_config.get('client_id', '').strip()
        client_pin = angelone_config.get('client_pin', '').strip()
        totp_key = angelone_config.get('totp_key', '').strip()
        
        if not all([api_key, client_id, client_pin, totp_key]):
            return False, "Missing Angel One credentials (API Key, Client ID, PIN, TOTP Key required)"
        
        # Initialize API if not already done
        if not angelone_api:
            angelone_api = AngelOneAPI(api_key, client_id, client_pin, totp_key)
        
        # Check if we have saved tokens
        access_token = angelone_config.get('access_token', '').strip()
        jwt_token = angelone_config.get('jwt_token', '').strip()
        refresh_token = angelone_config.get('refresh_token', '').strip()
        
        if access_token and jwt_token:
            # Use the saved tokens
            angelone_api.access_token = access_token
            angelone_api.jwt_token = jwt_token
            angelone_api.refresh_token = refresh_token
            print(f"✅ Loaded saved Angel One tokens from settings")
            
            # Try to refresh the token to ensure it's still valid
            refresh_success, refresh_msg = angelone_api.refresh_access_token()
            if refresh_success:
                # Save the refreshed tokens
                angelone_config['access_token'] = angelone_api.access_token
                angelone_config['jwt_token'] = angelone_api.jwt_token
                angelone_config['refresh_token'] = angelone_api.refresh_token
                settings['brokers']['angelone'] = angelone_config
                save_settings(settings)
                print(f"✅ Angel One tokens refreshed successfully")
            
            return True, "Angel One API ready for trading"
        
        # If no saved tokens, try to authenticate
        success, message = angelone_api.authenticate()
        if success:
            # Save the new tokens to settings
            angelone_config['access_token'] = angelone_api.access_token
            angelone_config['jwt_token'] = angelone_api.jwt_token
            angelone_config['refresh_token'] = angelone_api.refresh_token
            angelone_config['feed_token'] = angelone_api.feed_token
            settings['brokers']['angelone'] = angelone_config
            save_settings(settings)
            print(f"✅ Angel One authenticated and tokens saved")
            return True, "Angel One API ready for trading"
        else:
            return False, f"Angel One authentication failed: {message}"
            
    except Exception as e:
        return False, f"Error initializing Angel One API: {str(e)}"

# --- End Angel One API Integration ---

# --- Broker Management Functions ---
def update_broker_settings(broker_name, enabled, client_id, api_key, secret_key):
    """Update broker configuration settings"""
    try:
        settings = load_settings()
        if 'brokers' not in settings:
            settings['brokers'] = {}
        if broker_name not in settings['brokers']:
            settings['brokers'][broker_name] = {}
            
        settings['brokers'][broker_name]['enabled'] = enabled
        settings['brokers'][broker_name]['client_id'] = client_id.strip() if client_id else ""
        settings['brokers'][broker_name]['api_key'] = api_key.strip() if api_key else ""
        settings['brokers'][broker_name]['secret_key'] = secret_key.strip() if secret_key else ""
        
        save_settings(settings)
        return f"{broker_name.title()} settings updated successfully. {'Enabled' if enabled else 'Disabled'}."
    except Exception as e:
        return f"Error updating {broker_name} settings: {e}"

def load_broker_settings_for_ui(broker_name):
    """Load broker settings for UI components"""
    settings = load_settings()
    broker_config = settings.get('brokers', {}).get(broker_name, {})
    
    enabled = broker_config.get('enabled', False)
    client_id = broker_config.get('client_id', '')
    api_key = broker_config.get('api_key', '')
    secret_key = broker_config.get('secret_key', '')
    access_token = broker_config.get('access_token', '')
    
    # Create status message
    if enabled:
        if client_id and api_key and secret_key:
            if access_token:
                status = f"**Status:** ✅ **Connected** - {broker_name.title()} is authenticated and ready"
            else:
                status = f"**Status:** ⚠️ **Configured but not authenticated** - Please complete authentication"
        else:
            status = f"**Status:** ⚠️ **Enabled but missing credentials** - Please configure all required fields"
    else:
        status = f"**Status:** ❌ **Disabled** - {broker_name.title()} integration is turned off"
    
    return enabled, client_id, api_key, secret_key, status

def update_angelone_settings(enabled, client_id, api_key, client_pin, totp_key, redirect_url=None):
    """Update Angel One specific configuration settings"""
    try:
        settings = load_settings()
        if 'brokers' not in settings:
            settings['brokers'] = {}
        if 'angelone' not in settings['brokers']:
            settings['brokers']['angelone'] = {}
            
        settings['brokers']['angelone']['enabled'] = enabled
        settings['brokers']['angelone']['client_id'] = client_id.strip() if client_id else ""
        settings['brokers']['angelone']['api_key'] = api_key.strip() if api_key else ""
        settings['brokers']['angelone']['client_pin'] = client_pin.strip() if client_pin else ""
        settings['brokers']['angelone']['totp_key'] = totp_key.strip() if totp_key else ""
        
        # Add redirect URL for web authentication (optional)
        if redirect_url is not None:
            settings['brokers']['angelone']['redirect_url'] = redirect_url.strip() if redirect_url else "http://localhost:3001/callback"
        
        save_settings(settings)
        return f"AngelOne settings updated successfully. {'Enabled' if enabled else 'Disabled'}."
    except Exception as e:
        return f"Error updating AngelOne settings: {e}"

def load_angelone_settings_for_ui():
    """Load Angel One settings for UI components"""
    settings = load_settings()
    angelone_config = settings.get('brokers', {}).get('angelone', {})
    
    enabled = angelone_config.get('enabled', False)
    client_id = angelone_config.get('client_id', '')
    api_key = angelone_config.get('api_key', '')
    client_pin = angelone_config.get('client_pin', '')
    totp_key = angelone_config.get('totp_key', '')
    redirect_url = angelone_config.get('redirect_url', 'http://localhost:3001/callback')
    access_token = angelone_config.get('access_token', '')
    
    # Create status message
    if enabled:
        required_fields = [client_id, api_key, client_pin, totp_key]
        if all(required_fields):
            if access_token:
                status = f"**Status:** ✅ **Connected** - AngelOne is authenticated and ready"
            else:
                status = f"**Status:** ⚠️ **Configured but not authenticated** - Please test connection"
        else:
            missing_fields = []
            if not client_id: missing_fields.append("Client ID")
            if not api_key: missing_fields.append("API Key")
            if not client_pin: missing_fields.append("Login PIN")
            if not totp_key: missing_fields.append("TOTP Key")
            status = f"**Status:** ⚠️ **Missing fields:** {', '.join(missing_fields)}"
    else:
        status = f"**Status:** ❌ **Disabled** - AngelOne integration is turned off"
    
    # Auth status
    auth_status = "**Authentication Status:** Not authenticated"
    if access_token:
        auth_status = "**Authentication Status:** ✅ Authenticated and ready for trading"
    
    return enabled, client_id, api_key, client_pin, totp_key, redirect_url, status, auth_status

def test_angelone_connection():
    """Test Angel One connection and authenticate"""
    try:
        settings = load_settings()
        angelone_config = settings.get('brokers', {}).get('angelone', {})
        
        if not angelone_config.get('enabled', False):
            return "❌ AngelOne integration is disabled. Please enable it first."
        
        # Get credentials
        api_key = angelone_config.get('api_key', '').strip()
        client_id = angelone_config.get('client_id', '').strip()
        client_pin = angelone_config.get('client_pin', '').strip()
        totp_key = angelone_config.get('totp_key', '').strip()
        
        if not all([api_key, client_id, client_pin, totp_key]):
            return "❌ Missing required credentials. Please fill all fields (Client ID, API Key, PIN, TOTP Key)."
        
        # Initialize Angel One API
        initialize_angelone_api(api_key, client_id, client_pin, totp_key)
        
        # Attempt authentication
        success, message = authenticate_angelone()
        
        if success:
            return f"✅ AngelOne authentication successful! {message}"
        else:
            return f"❌ AngelOne authentication failed: {message}"
            
    except Exception as e:
        return f"❌ Error testing AngelOne connection: {str(e)}"

def authenticate_angelone_manual():
    """Manual Angel One authentication"""
    try:
        # Check if API is initialized
        if not angelone_api:
            return "❌ AngelOne API not initialized. Please save settings first."
        
        # Attempt authentication
        success, message = authenticate_angelone()
        
        if success:
            return f"✅ Manual authentication successful! {message}"
        else:
            return f"❌ Manual authentication failed: {message}"
            
    except Exception as e:
        return f"❌ Error in manual authentication: {str(e)}"

def generate_flattrade_oauth_url(api_key):
    """Generate Flattrade OAuth URL for authentication using correct API format"""
    try:
        if not api_key or not api_key.strip():
            return """❌ **API Key Required for OAuth URL Generation**
            
**Missing:** API Key field is empty

**📋 Instructions:**
1. Enter your **Flattrade API Key** in the API Key field above
2. This API Key will be used as the `app_key` parameter for OAuth
3. Do NOT use the Client ID field for OAuth - only API Key is needed
4. Click "Generate OAuth URL" again after entering the API Key

**🔧 Troubleshooting:**
- Make sure you're using the API Key, not Client ID
- API Key format: Usually 32 characters long
- Client ID is only used for account identification, not OAuth
"""
        
        # Flattrade OAuth parameters (following their official format)
        # Updated to match exact Flattrade API requirements
        params = {
            'app_key': api_key.strip(),  # Flattrade uses 'app_key' not 'client_id'
            'redirect_uri': 'http://localhost:3001/callback',
            'response_type': 'code',
            'state': 'fifto_flattrade_auth',
            'scope': 'trade'  # Adding scope for comprehensive access
        }
        
        # Flattrade OAuth endpoint - verified current format
        base_url = 'https://auth.flattrade.in/'
        query_string = urlencode(params)
        oauth_url = f'{base_url}?{query_string}'
        
        print(f'[OK] OAuth URL generated successfully:')
        print(f'📋 URL: {oauth_url}')
        print(f'🔑 API Key: {api_key}')
        print(f'📍 Redirect URI: http://localhost:3001/callback')
        print(f'🎯 OAuth Endpoint: {base_url}')
        print(f'📊 Parameters: {params}')
        
        return f"""🔗 **Flattrade OAuth URL Generated Successfully!**

**🌐 OAuth URL:**
```
{oauth_url}
```

**� Instructions:**
1. 🟢 **Start OAuth Server** - Click the button above to start the callback server
2. 🔗 **Click the URL** - Open the OAuth URL above in your browser
3. 🔐 **Login to Flattrade** - Complete the authentication process
4. ✅ **Verify Auth Code** - Click "Check Authorization Code" after login

**🔧 OAuth Configuration:**
- **API Key:** `{api_key}`
- **Redirect URI:** `http://localhost:3001/callback`
- **Response Type:** `code`
- **State:** `fifto_flattrade_auth`

**💡 Note:** This URL is valid for immediate use. Complete the authentication process promptly.
"""
    
    except Exception as e:
        error_msg = f'❌ Error generating OAuth URL: {str(e)}'
        print(error_msg)
        return error_msg

def check_flattrade_auth_code():
    """Check for Flattrade authorization code and authenticate"""
    try:
        # Check for authorization code file
        auth_code_file = os.path.join(os.path.expanduser('~'), '.fifto_analyzer_data', 'Flattrade_auth_code.txt')
        
        if not os.path.exists(auth_code_file):
            return """❌ **No Authorization Code Found**
            
**📝 Instructions:**
1. Make sure you completed the OAuth authentication process
2. Check that the OAuth server received the callback
3. Try the authentication process again if needed

**🔧 If you're having issues:**
- Ensure the OAuth server is running on port 3001
- Check your firewall settings
- Verify the redirect URL is correct
"""
        
        # Read the authorization code
        with open(auth_code_file, 'r') as f:
            request_code = f.read().strip()
        
        if not request_code:
            return "❌ **Empty authorization code found. Please try authentication again.**"
        
        # Get current Flattrade settings
        settings = load_settings()
        flattrade_config = settings.get('brokers', {}).get('flattrade', {})
        
        if not flattrade_config.get('enabled', False):
            return "❌ **Flattrade broker is not enabled. Please enable it first.**"
        
        api_key = flattrade_config.get('api_key', '')
        api_secret = flattrade_config.get('secret_key', '')
        user_id = flattrade_config.get('client_id', '')
        
        if not all([api_key, api_secret, user_id]):
            return "❌ **Missing Flattrade credentials. Please check your broker settings.**"
        
        # Initialize Flattrade API
        flattrade_api = initialize_flattrade_api(api_key, api_secret, user_id)
        
        # Authenticate with the authorization code
        success, message = authenticate_flattrade(request_code)
        
        if success:
            # Clean up the authorization code file
            try:
                os.remove(auth_code_file)
            except:
                pass
            
            # Get user details to verify authentication
            session_data = get_flattrade_session_data()
            
            if session_data:
                return f"""✅ **Flattrade Authentication Successful!**

**👤 User Details:**
- **User ID:** `{session_data.get('userId', 'N/A')}`
- **Client ID:** `{session_data.get('clientId', 'N/A')}`
- **Login Time:** `{session_data.get('loginTime', 'N/A')}`

**🎯 Status:** Ready for live trading operations

**💡 Next Steps:**
- You can now use Flattrade for live trading
- All API functions are now available
- Authentication will remain valid for the session

**🔧 Technical Details:**
- **Authorization Code:** `{request_code[:10]}...` (processed)
- **Session Token:** Active
- **API Status:** Connected
"""
            else:
                return f"""✅ **Flattrade Authentication Successful!**

**🎯 Status:** Ready for live trading operations

**💡 Next Steps:**
- You can now use Flattrade for live trading
- All API functions are now available
- Authentication will remain valid for the session

**🔧 Technical Details:**
- **Authorization Code:** `{request_code[:10]}...` (processed)
- **Session Token:** Active
- **API Status:** Connected
"""
        else:
            return f"""❌ **Authentication Failed**

**Error:** {message}

**🔧 Troubleshooting:**
1. Verify your API credentials are correct
2. Ensure the authorization code is valid (not expired)
3. Check your internet connection
4. Try generating a new OAuth URL and authenticating again

**📋 Authorization Code:** `{request_code[:20]}...`
"""
    
    except Exception as e:
        return f"""❌ **Error Checking Authorization Code**

**Error Details:** {str(e)}

**🔧 Please try:**
1. Restart the OAuth authentication process
2. Check your broker settings
3. Ensure all credentials are correctly entered
4. Contact support if the issue persists
"""

def generate_angelone_oauth_url(api_key):
    """Generate Angel One Publisher Login URL for web-based authentication"""
    try:
        if not api_key or not api_key.strip():
            return """❌ **API Key Required for Publisher Login URL Generation**
            
**Missing:** API Key field is empty

**📋 Instructions:**
1. Enter your **Angel One API Key** in the API Key field above
2. This API Key will be used for the Publisher Login URL
3. Click "Generate Publisher Login URL" again after entering the API Key

**🔧 Troubleshooting:**
- Make sure you're using the API Key from SmartAPI portal
- API Key format: Usually alphanumeric string
- This enables web-based authentication similar to Flattrade
"""
        
        # Angel One Publisher Login parameters
        params = {
            'api_key': api_key.strip(),
            'state': 'fifto_angelone_auth'  # State parameter for tracking
        }
        
        # Angel One Publisher Login endpoint
        base_url = 'https://smartapi.angelone.in/publisher-login'
        query_string = urlencode(params)
        publisher_url = f'{base_url}?{query_string}'
        
        print(f'✅ Angel One Publisher Login URL generated successfully:')
        print(f'📋 URL: {publisher_url}')
        print(f'🔑 API Key: {api_key}')
        print(f'🎯 Publisher Login Endpoint: {base_url}')
        print(f'📊 Parameters: {params}')
        
        return f"""🔗 **Angel One Publisher Login URL Generated Successfully!**

**🌐 Publisher Login URL:**
```
{publisher_url}
```

**📋 Instructions:**
1. 🟢 **Configure Redirect URL** - Set your app's redirect URL to: `http://localhost:3001/callback`
2. 🔗 **Click the URL** - Open the Publisher Login URL above in your browser
3. 🔐 **Login to Angel One** - Complete the authentication process
4. ✅ **Check Callback** - Click "Check Callback Status" after login

**🔧 Configuration Notes:**
- **API Key:** `{api_key}`
- **State:** `fifto_angelone_auth`
- **Expected Redirect:** Your configured redirect URL in SmartAPI portal

**⚠️ Important:** 
- This requires your SmartAPI app to have a redirect URL configured
- The redirect URL must match what's set in your Angel One app settings
- For web-based authentication similar to Flattrade OAuth flow

**💡 Alternative:** You can still use direct API authentication with TOTP instead.
"""
    
    except Exception as e:
        error_msg = f'❌ Error generating Publisher Login URL: {str(e)}'
        print(error_msg)
        return error_msg

def check_angelone_callback_status():
    """Check for Angel One callback parameters and process authentication"""
    try:
        # Check for callback data file
        callback_file = os.path.join(os.path.expanduser('~'), '.fifto_analyzer_data', 'Angelone_auth_callback.txt')
        
        if not os.path.exists(callback_file):
            return """❌ **No Callback Data Found**
            
**📝 Instructions:**
1. Make sure you completed the Publisher Login authentication process
2. Check that the callback was received at your redirect URL
3. Try the authentication process again if needed

**🔧 If you're having issues:**
- Ensure your SmartAPI app has the correct redirect URL configured
- Check that the redirect URL matches: `http://localhost:3001/callback`
- Verify you're using the correct API key
- Make sure you clicked the generated Publisher Login URL

**💡 Note:** 
This web-based authentication is optional. You can use direct API authentication with TOTP instead.
"""
        
        # Read the callback data
        with open(callback_file, 'r') as f:
            callback_data = f.read().strip()
        
        if not callback_data:
            return "❌ **Empty callback data found. Please try authentication again.**"
        
        # Parse callback data (expecting auth_token and feed_token)
        try:
            callback_info = json.loads(callback_data)
            auth_token = callback_info.get('auth_token', '')
            feed_token = callback_info.get('feed_token', '')
            state = callback_info.get('state', '')
            
            if not auth_token:
                return "❌ **No auth_token found in callback. Please try authentication again.**"
            
            # Verify state parameter
            if state != 'fifto_angelone_auth':
                return f"❌ **State mismatch. Expected 'fifto_angelone_auth', got '{state}'**"
            
            # Update Angel One settings with tokens
            settings = load_settings()
            if 'brokers' not in settings:
                settings['brokers'] = {}
            if 'angelone' not in settings['brokers']:
                settings['brokers']['angelone'] = {}
            
            # Store the tokens (these would be equivalent to access tokens)
            settings['brokers']['angelone']['auth_token'] = auth_token
            settings['brokers']['angelone']['feed_token'] = feed_token
            settings['brokers']['angelone']['web_authenticated'] = True
            
            save_settings(settings)
            
            # Clean up the callback file
            try:
                os.remove(callback_file)
            except:
                pass
            
            return f"""✅ **Angel One Web Authentication Successful!**

**🎯 Status:** Ready for API operations

**🔧 Authentication Details:**
- **Auth Token:** `{auth_token[:20]}...` (received)
- **Feed Token:** `{feed_token[:20] if feed_token else 'Not provided'}...`
- **State:** `{state}` ✅ Verified
- **Method:** Web-based Publisher Login

**💡 Next Steps:**
- You can now use Angel One for API operations
- Web authentication tokens have been stored
- You may still need to configure TOTP for trading operations

**📋 Note:** 
Some Angel One API operations may still require direct API authentication with TOTP.
This web authentication provides session tokens for basic API access.
"""
            
        except json.JSONDecodeError:
            return f"""❌ **Invalid callback data format**

**Error:** Could not parse callback information

**🔧 Troubleshooting:**
1. Ensure the callback URL is correctly configured
2. Try the authentication process again
3. Check that your SmartAPI app settings are correct

**📋 Callback Data:** `{callback_data[:50]}...`
"""
    
    except Exception as e:
        return f"""❌ **Error Checking Callback Status**

**Error Details:** {str(e)}

**🔧 Please try:**
1. Restart the Publisher Login authentication process
2. Check your SmartAPI app configuration
3. Ensure all credentials are correctly entered
4. Contact support if the issue persists
"""

def test_broker_connection(broker_name):
    """Test broker connection with current settings"""
    try:
        settings = load_settings()
        broker_config = settings.get('brokers', {}).get(broker_name, {})
        
        if not broker_config.get('enabled', False):
            return f"❌ **{broker_name.title()} is disabled.** Please enable it first in broker settings."
        
        if broker_name == "flattrade":
            api_key = broker_config.get('api_key', '')
            api_secret = broker_config.get('secret_key', '')
            client_id = broker_config.get('client_id', '')
            
            if not all([api_key, api_secret, client_id]):
                return "❌ **Missing Flattrade credentials.** Please check Client ID, API Key, and Secret Key."
            
            # Test API connectivity
            try:
                import socket
                socket.create_connection(("auth.flattrade.in", 443), timeout=10)
                
                return f"""✅ **Flattrade Connection Test Successful**

**🌐 Network Status:** Connected to Flattrade servers
**🔑 Credentials:** API Key and Secret configured
**👤 Client ID:** `{client_id}`
**📡 API Endpoint:** Reachable
**🔐 OAuth Endpoint:** Accessible

**📋 Next Steps:**
1. Click "Generate OAuth URL" to get authentication link
2. Complete OAuth authentication process
3. Check authorization code after login
4. Start live trading operations

**✅ Ready for OAuth authentication!**
"""
            except Exception as e:
                return f"""❌ **Connection Test Failed**

**Network Error:** {str(e)}

**🔧 Troubleshooting:**
- Check your internet connection
- Verify firewall settings
- Ensure Flattrade services are operational
- Try again in a few moments
"""
        
        else:
            return f"⚠️ **Connection test for {broker_name.title()} not implemented yet.**"
    
    except Exception as e:
        return f"❌ **Test failed:** {str(e)}"
        api_key = broker_config.get('api_key', '')
        secret_key = broker_config.get('secret_key', '')
        
        if not all([client_id, api_key, secret_key]):
            return f"Missing credentials for {broker_name.title()}. Please configure all required fields."
        
        # For now, just validate that credentials are provided
        # In a real implementation, you would make an API call to test the connection
        return f"✅ {broker_name.title()} credentials are configured. Note: Live connection testing requires broker API implementation."
        
    except Exception as e:
        return f"Error testing {broker_name} connection: {e}"

def get_broker_status_summary():
    """Get summary of all broker connection statuses"""
    settings = load_settings()
    brokers = settings.get('brokers', {})
    
    summary = "**Broker Status Summary:**\n\n"
    for broker_name, config in brokers.items():
        enabled = config.get('enabled', False)
        
        # Check credentials based on broker type
        if broker_name == 'angelone':
            has_credentials = all([
                config.get('client_id', ''),
                config.get('api_key', ''),
                config.get('client_pin', ''),
                config.get('totp_key', '')
            ])
        else:
            has_credentials = all([
                config.get('client_id', ''),
                config.get('api_key', ''),
                config.get('secret_key', '')
            ])
        
        access_token = config.get('access_token', '')
        
        if enabled and has_credentials:
            if access_token:
                status = "✅ Connected & Authenticated"
            else:
                status = "⚠️ Configured but not authenticated"
        elif enabled:
            status = "⚠️ Missing Credentials"
        else:
            status = "❌ Disabled"
            
        summary += f"- **{broker_name.title()}:** {status}\n"
    
    # Add instructions for live trading
    summary += "\n**For Live Trading:**\n"
    summary += "- Enable at least one broker\n"
    summary += "- Complete authentication\n"
    summary += "- Enable 'PLACE LIVE ORDERS' checkbox\n"
    
    return summary

def start_oauth_server_instructions():
    """Provide instructions for starting the OAuth server"""
    return """
🚀 **OAuth Authentication Instructions:**

1. **Start the OAuth Server:**
   - Open a new terminal/command prompt
   - Navigate to the FiFTO folder
   - Run: `python flattrade_oauth_server.py`
   - Keep this running during authentication

2. **Configure Credentials:**
   - Enter your Client ID above
   - Click "Generate OAuth URL" 
   - Click the generated URL to authenticate

3. **Complete Authentication:**
   - Login to Flattrade in the browser
   - Authorize the application
   - Return here and check for the authorization code

**Note:** The OAuth server runs on `http://localhost:3001/callback`
"""


# --- Helper & Data Functions ---
def load_trades():
    if not os.path.exists(TRADES_DB_FILE): return []
    try:
        with open(TRADES_DB_FILE, 'r') as f: return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError): return []

def save_trades(trades):
    with open(TRADES_DB_FILE, 'w') as f: json.dump(trades, f, indent=4)

def load_historical_pnl():
    if not os.path.exists(HISTORICAL_PNL_FILE): return []
    try:
        with open(HISTORICAL_PNL_FILE, 'r') as f: return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError): return []

def save_historical_pnl(history):
    with open(HISTORICAL_PNL_FILE, 'w') as f: json.dump(history, f, indent=4)

def get_option_chain_data(symbol, retries=3, delay=2):
    """Fetches option chain data with a retry mechanism."""
    session = requests.Session()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36', 'Accept': 'application/json, text/javascript, */*; q=0.01', 'Accept-Language': 'en-US,en;q=0.9'}
    api_url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    for i in range(retries):
        try:
            session.get(f"https://www.nseindia.com/get-quotes/derivatives?symbol={symbol}", headers=headers, timeout=15)
            time.sleep(1)
            response = session.get(api_url, headers=headers, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Attempt {i+1}/{retries} failed for {symbol}: {e}")
            if i < retries - 1:
                time.sleep(delay)
    print(f"All retries failed for {symbol}.")
    return None

def send_telegram_message(message, image_paths=None):
    """Send message to Telegram with optional image attachments"""
    settings = load_settings()
    
    # Check if Telegram is enabled
    if not settings.get('telegram_enabled', True):
        return "Telegram notifications are disabled in settings."
    
    BOT_TOKEN = settings.get('telegram_bot_token', DEFAULT_BOT_TOKEN)
    CHAT_ID = settings.get('telegram_chat_id', DEFAULT_CHAT_ID)
    
    if not BOT_TOKEN or not CHAT_ID or BOT_TOKEN == "YOUR_BOT_TOKEN" or CHAT_ID == "YOUR_CHAT_ID":
        return "Telegram credentials not configured properly."
        
    try:
        if image_paths:
            if isinstance(image_paths, str): image_paths = [image_paths]
            image_paths = [path for path in image_paths if path and os.path.exists(path)]
            if not image_paths: return send_telegram_message(message) if message else "No valid images to send."
            if len(image_paths) > 1:
                url, files, media = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup", {}, []
                for i, path in enumerate(image_paths):
                    file_name = os.path.basename(path)
                    files[file_name] = open(path, 'rb')
                    photo_media = {'type': 'photo', 'media': f'attach://{file_name}'}
                    if i == 0 and message: photo_media['caption'] = message
                    media.append(photo_media)
                response = requests.post(url, data={'chat_id': CHAT_ID, 'media': json.dumps(media)}, files=files)
                for f in files.values(): f.close()
            elif len(image_paths) == 1:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                with open(image_paths[0], 'rb') as img:
                    response = requests.post(url, data={'chat_id': CHAT_ID, 'caption': message}, files={'photo': img})
        else:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            response = requests.post(url, data={'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'Markdown'})
        response.raise_for_status()
        return "Message sent to Telegram."
    except requests.exceptions.RequestException as e: return f"Failed to send to Telegram: {e}"

# --- Charting Functions ---
def generate_analysis(instrument_name, calculation_type, selected_expiry_str, hedge_premium_percentage, progress=gr.Progress(track_tqdm=True)):
    if not selected_expiry_str or "Loading..." in selected_expiry_str or "Error" in selected_expiry_str: return None, None, None, "Expiry Date has not loaded. Please wait or re-select the Index to try again.", None, None, None, None
    try:
        TICKERS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
        lot_size = 75 if instrument_name == "NIFTY" else 15
        strike_increment = 50 if instrument_name == "NIFTY" else 100

        progress(0.2, desc=f"Calculating {calculation_type} zones...")
        df_zones = yf.Ticker(TICKERS[instrument_name]).history(period="6mo" if calculation_type == "Weekly" and instrument_name == "NIFTY" else "5y", interval="1d")
        if df_zones.empty: return None, None, None, "Failed to calculate zones.", None, None, None, None
        df_zones.index, agg_df = pd.to_datetime(df_zones.index), df_zones.resample('W' if calculation_type == "Weekly" else 'ME').agg({'Open': 'first', 'High': 'max', 'Low': 'min'}).dropna()
        base, rng5, rng10 = agg_df['Open'], (agg_df['High'] - agg_df['Low']).rolling(5).mean(), (agg_df['High'] - agg_df['Low']).rolling(10).mean()
        latest_zones = pd.DataFrame({'u1': base + 0.5*rng5, 'u2': base + 0.5*rng10, 'l1': base - 0.5*rng5, 'l2': base - 0.5*rng10}).dropna().iloc[-1]
        supply_zone, demand_zone = round(max(latest_zones['u1'], latest_zones['u2']), 2), round(min(latest_zones['l1'], latest_zones['l2']), 2)

        progress(0.4, desc="Fetching option chain...")
        option_chain_data = get_option_chain_data(instrument_name)
        if not option_chain_data: return None, None, None, "Error fetching option chain.", None, None, None, None
        current_price, expiry_label = option_chain_data['records']['underlyingValue'], datetime.strptime(selected_expiry_str, '%d-%b-%Y').strftime("%d-%b")
        ce_prices, pe_prices = {}, {}
        for item in option_chain_data['records']['data']:
            if item.get("expiryDate") == selected_expiry_str:
                if item.get("CE"): ce_prices[item['strikePrice']] = item["CE"]["lastPrice"]
                if item.get("PE"): pe_prices[item['strikePrice']] = item["PE"]["lastPrice"]
        ce_high = math.ceil(supply_zone / strike_increment) * strike_increment
        strikes_ce = [ce_high, ce_high + strike_increment, ce_high + (2 * strike_increment)]
        pe_high = math.floor(demand_zone / strike_increment) * strike_increment
        candidate_puts = sorted([s for s in pe_prices if s < pe_high and pe_prices.get(s, 0) > 0], key=lambda s: pe_prices.get(s, 0), reverse=True)
        pe_mid, pe_low = (candidate_puts[0] if candidate_puts else pe_high - strike_increment), (candidate_puts[1] if len(candidate_puts) > 1 else (candidate_puts[0] if candidate_puts else pe_high - strike_increment) - strike_increment)
        strikes_pe = [pe_high, pe_mid, pe_low]
        temp_df = pd.DataFrame({"CE Strike": strikes_ce, "CE Price": [ce_prices.get(s, 0.0) for s in strikes_ce], "PE Strike": strikes_pe, "PE Price": [pe_prices.get(s, 0.0) for s in strikes_pe]})
        hedge_premium_decimal, strikes_ce_hedge, strikes_pe_hedge = hedge_premium_percentage / 100.0, [], []
        for _, row in temp_df.iterrows():
            strikes_ce_hedge.append(find_hedge_strike(row['CE Strike'], row['CE Price'] * hedge_premium_decimal, ce_prices, 'CE') or row['CE Strike'] + 1000)
            strikes_pe_hedge.append(find_hedge_strike(row['PE Strike'], row['PE Price'] * hedge_premium_decimal, pe_prices, 'PE') or row['PE Strike'] - 1000)

        df = pd.DataFrame({"Entry": ["High Reward", "Mid Reward", "Low Reward"], "CE Strike": strikes_ce, "CE Price": [ce_prices.get(s, 0.0) for s in strikes_ce], "PE Strike": strikes_pe, "PE Price": [pe_prices.get(s, 0.0) for s in strikes_pe], "CE Hedge Strike": strikes_ce_hedge, "CE Hedge Price": [ce_prices.get(s, 0.0) for s in strikes_ce_hedge], "PE Hedge Strike": strikes_pe_hedge, "PE Hedge Price": [pe_prices.get(s, 0.0) for s in strikes_pe_hedge]})

        df["Sell Premium"] = df["CE Price"] + df["PE Price"]
        df["Hedge Premium"] = df["CE Hedge Price"] + df["PE Hedge Price"]
        df["Net Premium"] = df["Sell Premium"] - df["Hedge Premium"]
        df["Target"] = (df["Sell Premium"] * 0.85 * lot_size).round(2)
        df["Stoploss"] = (df["Sell Premium"] * 0.85 * lot_size).round(2)

        display_df = df[['Entry', 'CE Strike', 'CE Price', 'PE Strike', 'PE Price']].copy()
        display_df['CE Price'] = df['CE Price'].round(2)
        display_df['PE Price'] = df['PE Price'].round(2)
        display_df['Net Credit'] = df['Net Premium'].round(2)
        display_df['Target/SL (₹)'] = df['Target']
        display_df = display_df[['Entry', 'CE Strike', 'CE Price', 'PE Strike', 'PE Price', 'Net Credit', 'Target/SL (₹)']]

        # Set matplotlib backend to non-GUI for threading compatibility
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend
        
        fig, ax = plt.subplots(figsize=(14, 5))
        fig.patch.set_facecolor('#e0f7fa')
        ax.axis('off')

        title = f"FiFTO - {calculation_type} {instrument_name} SELL Positions"
        summary_filename = os.path.join(TEMP_DIR, f"{instrument_name}_{calculation_type}_Summary.png")

        fig.suptitle(title, fontsize=16, fontweight='bold', y=0.98)
        ax.text(0.5, 0.85, f"{instrument_name}: {current_price}\nExpiry: {expiry_label}\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", transform=ax.transAxes, ha='center', va='center', fontsize=12, family='monospace')

        table = plt.table(cellText=display_df.values, colLabels=display_df.columns, colColours=['#C0392B'] * len(display_df.columns), cellLoc='center', loc='center')
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1.2, 2.0)
        for (row, col), cell in table.get_celld().items():
            if row == 0: cell.get_text().set_color('white')
        fig.text(0.5, 0.01, "Disclaimer: For educational purposes only. Not SEBI registered.", ha='center', va='bottom', fontsize=7, color='grey', style='italic')
        plt.savefig(summary_filename, dpi=300, bbox_inches='tight'); plt.close(fig)

        hedge_filename = generate_hedge_image(df, instrument_name, expiry_label)
        payoff_filename = generate_payoff_chart(df, lot_size, current_price, instrument_name, calculation_type, expiry_label)
        analysis_data = {"instrument": instrument_name, "expiry": selected_expiry_str, "lot_size": lot_size, "df_data": df.to_dict('records')}

        return summary_filename, hedge_filename, payoff_filename, f"Charts generated for {instrument_name}.", analysis_data, summary_filename, hedge_filename, payoff_filename
    except Exception as e:
        traceback.print_exc()
        return None, None, None, f"An error occurred: {e}", None, None, None, None

def generate_pl_update_image(data_for_image, timestamp):
    num_lines = 1 + sum(len(trades) + 1.5 for _, trades in data_for_image['tags'].items())
    fig_height = max(3, num_lines * 0.4)
    fig, ax = plt.subplots(figsize=(8, fig_height), facecolor='#F4F6F6')
    ax.axis('off')
    fig.text(0.5, 0.95, data_for_image['title'], ha='center', va='top', fontsize=18, fontweight='bold', color='#17202A')
    y_pos, line_height = 0.85, 1.0 / (num_lines + 2)
    for tag, trades in sorted(data_for_image['tags'].items()):
        ax.text(0.1, y_pos, f"{tag}", ha='left', va='top', fontsize=14, fontweight='bold', color='#2980B9')
        y_pos -= line_height * 1.2
        for trade in trades:
            pnl_color = '#27AE60' if trade['pnl'] >= 0 else '#C0392B'
            ax.text(0.15, y_pos, f"• {trade['reward_type']}:", ha='left', va='top', fontsize=13, color='#212F3D')
            ax.text(0.85, y_pos, f"₹{trade['pnl']:,.2f}", ha='right', va='top', fontsize=13, color=pnl_color, family='monospace', weight='bold')
            y_pos -= line_height
        y_pos -= (line_height / 2)
    fig.text(0.98, 0.02, timestamp.strftime("%d-%b-%Y %I:%M:%S %p"), ha='right', va='bottom', fontsize=9, color='#566573')
    filepath = os.path.join(TEMP_DIR, f"pl_update_{uuid.uuid4().hex}.png")
    plt.savefig(filepath, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor(), pad_inches=0.1); plt.close(fig)
    return filepath

def generate_payoff_chart(strategies_df, lot_size, current_price, instrument_name, zone_label, expiry_label):
    plt.style.use('seaborn-v0_8-whitegrid')
    fig, ax = plt.subplots(figsize=(10, 6))
    price_range = np.linspace(current_price * 0.90, current_price * 1.10, 500)
    max_profit = 0
    for strategy in strategies_df.to_dict('records'):
        if strategy['Entry'] == 'High Reward':
            ce_strike, pe_strike, sell_premium = strategy['CE Strike'], strategy['PE Strike'], strategy['Sell Premium']
            pnl = (sell_premium - np.maximum(price_range - ce_strike, 0) - np.maximum(pe_strike - price_range, 0)) * lot_size
            ax.plot(price_range, pnl, label="Sell Payoff (Naked)", linewidth=2.5)
            be_upper, be_lower = ce_strike + sell_premium, pe_strike - sell_premium
            max_profit = sell_premium * lot_size
            ax.fill_between(price_range, pnl, 0, where=(pnl >= 0), facecolor='green', alpha=0.3, interpolate=True, label='Profit')
            ax.fill_between(price_range, pnl, 0, where=(pnl <= 0), facecolor='red', alpha=0.3, interpolate=True, label='Loss')
            ax.axvline(x=be_lower, color='grey', linestyle='--', label=f'Lower BEP: {be_lower:,.0f}'), ax.axvline(x=be_upper, color='grey', linestyle='--', label=f'Upper BEP: {be_upper:,.0f}')
            ax.annotate(f'Max Profit: ₹{max_profit:,.2f}', xy=(current_price, max_profit), xytext=(current_price, max_profit * 0.5), ha='center', va='center', fontsize=11, fontweight='bold', bbox=dict(boxstyle="round,pad=0.5", fc="lightgreen", alpha=0.9))
            break
    ax.set_title(f"Sell Positions Payoff Graph for Expiry: {expiry_label}", fontsize=14), fig.suptitle(f"FiFTO {zone_label} Selling - {instrument_name}", fontsize=16, fontweight='bold')
    ax.axhline(y=0, color='black', linestyle='-', lw=1.0), ax.set_xlabel("Stock Price at Expiration", fontsize=12), ax.set_ylabel("Profit / Loss (₹)", fontsize=12)
    ax.set_ylim(-3 * max_profit if max_profit > 0 else -25000, 1.5 * max_profit if max_profit > 0 else 25000)
    ax.legend(), fig.text(0.99, 0.01, f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', horizontalalignment='right', verticalalignment='bottom', fontsize=8, color='gray')
    filename = os.path.join(TEMP_DIR, f"payoff_{uuid.uuid4().hex}.png")
    plt.savefig(filename, dpi=300, bbox_inches='tight'); plt.close(fig)
    return filename

def find_hedge_strike(sold_strike, target_premium, options_data, option_type):
    candidates = []
    for strike, price in options_data.items():
        is_valid_direction = (option_type == 'CE' and strike > sold_strike) or (option_type == 'PE' and strike < sold_strike)
        if is_valid_direction and strike % 100 == 0: candidates.append({'strike': strike, 'price': price, 'diff': abs(price - target_premium)})
    return min(candidates, key=lambda x: x['diff'])['strike'] if candidates else None

def generate_hedge_image(df, instrument_name, expiry_label):
    hedge_df = df[['Entry', 'CE Hedge Strike', 'CE Hedge Price', 'PE Hedge Strike', 'PE Hedge Price']].copy()
    hedge_df.rename(columns={'Entry': 'Strategy', 'CE Hedge Strike': 'Buy Call Strike', 'CE Hedge Price': 'Price (CE)', 'PE Hedge Strike': 'Buy Put Strike', 'PE Hedge Price': 'Price (PE)'}, inplace=True)
    fig, ax = plt.subplots(figsize=(10, 4), facecolor='#F0F3F4')
    ax.axis('off'), fig.suptitle(f"{instrument_name} - Hedge Positions to BUY", fontsize=16, fontweight='bold', y=0.95)
    ax.text(0.5, 0.82, f"For Expiry: {expiry_label}", transform=ax.transAxes, ha='center', va='center', fontsize=12, family='monospace')
    table = plt.table(cellText=hedge_df.values, colLabels=hedge_df.columns, colColours=['#27AE60'] * len(hedge_df.columns), cellLoc='center', loc='center')
    table.auto_set_font_size(False); table.set_fontsize(11); table.scale(1.2, 2.0)
    for (row, col), cell in table.get_celld().items():
        if row == 0: cell.get_text().set_color('white')
    fig.text(0.5, 0.01, "These positions are intended to hedge the primary sell trades.", ha='center', va='bottom', fontsize=8, color='grey', style='italic')
    filename = os.path.join(TEMP_DIR, f"hedge_{uuid.uuid4().hex}.png")
    plt.savefig(filename, dpi=300, bbox_inches='tight'); plt.close(fig)
    return filename

def add_trades_to_db(analysis_data):
    if not analysis_data or not analysis_data['df_data']: return "No analysis data to add."
    trades, new_trades_added, entry_tag = load_trades(), 0, f"{datetime.now().strftime('%b-%d %A')} Selling"
    for entry in analysis_data['df_data']:
        trade_id = f"{analysis_data['instrument']}_{analysis_data['expiry']}_{entry['Entry'].replace(' ', '')}_{entry_tag.replace(' ', '')}"
        if any(t['id'] == trade_id for t in trades): continue
        trades.append({"id": trade_id, "start_time": datetime.now().strftime("%Y-%m-%d %H:%M"), "instrument": analysis_data['instrument'], "expiry": analysis_data['expiry'], "reward_type": entry['Entry'], "ce_strike": entry['CE Strike'], "pe_strike": entry['PE Strike'], "ce_hedge_strike": entry['CE Hedge Strike'], "pe_hedge_strike": entry['PE Hedge Strike'], "initial_net_premium": entry['Net Premium'], "target_amount": entry['Target'], "stoploss_amount": entry['Stoploss'], "status": "Running", "entry_tag": entry_tag})
        new_trades_added += 1
    if new_trades_added > 0: save_trades(trades); return f"Added {new_trades_added} new trade(s) tagged as '{entry_tag}'."
    return "No new trades were added. They may already exist."

def add_to_analysis(analysis_data): return add_trades_to_db(analysis_data), gr.DataFrame(value=load_trades_for_display())

def add_automated_trades_to_active_analysis(analysis_data, auto_trade_result):
    """
    Add only the successfully executed automated trades to Active Analysis
    This ensures automated trades are tracked with existing target/stoploss monitoring
    """
    if not analysis_data or not analysis_data.get('df_data'):
        return "No analysis data to add."
    
    if auto_trade_result.get('status') != 'success':
        return "No successful automated trades to add."
    
    # Get list of successfully executed strategies
    executed_strategies = []
    for result in auto_trade_result.get('results', []):
        if isinstance(result, dict) and result.get('status') == 'success':
            executed_strategies.append(result.get('strategy'))
        elif isinstance(result, str) and 'success' in result.lower():
            # If result is a string containing success, extract strategy name
            executed_strategies.append(result)
    
    if not executed_strategies:
        return "No successful automated trades found."
    
    # Add only executed strategies to Active Analysis
    trades = load_trades()
    new_trades_added = 0
    entry_tag = f"{datetime.now().strftime('%b-%d %A')} Auto-Trading"
    
    for entry in analysis_data['df_data']:
        strategy_name = entry['Entry']
        
        # Only add if this strategy was successfully executed
        if strategy_name not in executed_strategies:
            continue
            
        trade_id = f"{analysis_data['instrument']}_{analysis_data['expiry']}_{strategy_name.replace(' ', '')}_{entry_tag.replace(' ', '')}"
        
        # Check if trade already exists
        if any(t['id'] == trade_id for t in trades):
            continue
            
        # Add trade to Active Analysis
        trade_entry = {
            "id": trade_id,
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "instrument": analysis_data['instrument'],
            "expiry": analysis_data['expiry'],
            "reward_type": strategy_name,
            "ce_strike": entry['CE Strike'],
            "pe_strike": entry['PE Strike'],
            "ce_hedge_strike": entry['CE Hedge Strike'],
            "pe_hedge_strike": entry['PE Hedge Strike'],
            "initial_net_premium": entry['Net Premium'],
            "target_amount": entry['Target'],
            "stoploss_amount": entry['Stoploss'],
            "status": "Running",
            "entry_tag": entry_tag,
            "automated_trade": True  # Flag to identify automated trades
        }
        
        trades.append(trade_entry)
        new_trades_added += 1
    
    if new_trades_added > 0:
        save_trades(trades)
        return f"Added {new_trades_added} automated trade(s) to Active Analysis with tag '{entry_tag}'."
    
    return "No new automated trades were added to Active Analysis."

def load_trades_for_display():
    trades = load_trades()
    if not trades: return pd.DataFrame(columns=["ID", "Tag", "Start Time", "Status", "Initial Net Credit (₹)", "Target Profit (₹)", "Stoploss (₹)"])
    df = pd.DataFrame(trades)
    df['lot_size'] = df['instrument'].apply(lambda x: 75 if x == 'NIFTY' else 15)
    df.loc[:, 'initial_amount'] = (df.get('initial_net_premium', 0) * df['lot_size']).round()
    df['start_time'], df['entry_tag'] = df.get('start_time', 'N/A'), df.get('entry_tag', 'N/A')
    display_df = df[['id', 'entry_tag', 'start_time', 'status', 'initial_amount', 'target_amount', 'stoploss_amount']].copy()
    display_df.rename(columns={'id': 'ID', 'entry_tag': 'Tag', 'start_time': 'Start Time', 'status': 'Status', 'initial_amount': 'Initial Net Credit (₹)', 'target_amount': 'Target Profit (₹)', 'stoploss_amount': 'Stoploss (₹)'}, inplace=True)
    return display_df

def close_trade_group_by_tag(tag_to_close, current_df):
    if not tag_to_close:
        return current_df, "Please select a group from the dropdown first."
    all_trades = load_trades()
    trades_in_group = [t for t in all_trades if t.get('entry_tag') == tag_to_close and t.get('status') == 'Running']

    if not trades_in_group:
        return load_trades_for_display(), f"No running trades found for group '{tag_to_close}'."

    instrument = trades_in_group[0]['instrument']
    lot_size = 75 if instrument == "NIFTY" else 15
    chain = get_option_chain_data(instrument)
    telegram_msg = f"🔔 *Manual Group Square-Off: {tag_to_close}* 🔔\n\n"
    group_final_pnl = 0.0
    closed_trades_details = []

    for trade in trades_in_group:
        current_prices = {'ce': 0.0, 'pe': 0.0, 'ce_hedge': 0.0, 'pe_hedge': 0.0}
        if chain:
            for item in chain['records']['data']:
                if item['expiryDate'] == trade['expiry']:
                    if item['strikePrice'] == trade['ce_strike'] and item.get('CE'): current_prices['ce'] = item['CE']['lastPrice']
                    if item['strikePrice'] == trade['pe_strike'] and item.get('PE'): current_prices['pe'] = item['PE']['lastPrice']
                    if 'ce_hedge_strike' in trade and item['strikePrice'] == trade.get('ce_hedge_strike') and item.get('CE'): current_prices['ce_hedge'] = item['CE']['lastPrice']
                    if 'pe_hedge_strike' in trade and item['strikePrice'] == trade.get('pe_hedge_strike') and item.get('PE'): current_prices['pe_hedge'] = item['PE']['lastPrice']

        current_net_premium = (current_prices['ce'] + current_prices['pe']) - (current_prices['ce_hedge'] + current_prices['pe_hedge'])
        pnl = (trade.get('initial_net_premium', 0) - current_net_premium) * lot_size
        group_final_pnl += pnl

        trade_copy = trade.copy()
        trade_copy['final_pnl'] = pnl
        trade_copy['closing_prices'] = current_prices
        trade_copy['status'] = 'Closed'
        trade_copy['end_time'] = datetime.now(pytz.timezone('Asia/Kolkata')).isoformat()
        closed_trades_details.append(trade_copy)

        telegram_msg += (f"*Trade: {trade['reward_type']}* (P/L: `₹{pnl:,.2f}`)\n"
                         f"  - SELL CE {trade['ce_strike']}: `₹{current_prices['ce']:.2f}` | BUY CE {trade.get('ce_hedge_strike','N/A')}: `₹{current_prices['ce_hedge']:.2f}`\n"
                         f"  - SELL PE {trade['pe_strike']}: `₹{current_prices['pe']:.2f}` | BUY PE {trade.get('pe_hedge_strike','N/A')}: `₹{current_prices['pe_hedge']:.2f}`\n\n")

    telegram_msg += f"*Total P/L for this group: ₹{group_final_pnl:,.2f}*"
    send_telegram_message(telegram_msg)

    history = load_historical_pnl()
    history.extend(closed_trades_details)
    save_historical_pnl(history)

    remaining_trades = [t for t in all_trades if t.get('entry_tag') != tag_to_close]
    save_trades(remaining_trades)

    return load_trades_for_display(), f"Closed group '{tag_to_close}' with final P/L ₹{group_final_pnl:,.2f}. Performance logged to history."

def clear_all_trades():
    all_trades = load_trades()
    if not all_trades: return load_trades_for_display(), "Trade list is already empty."

    all_tags = list(set(t.get('entry_tag') for t in all_trades if t.get('status') == 'Running'))
    final_message = []
    for tag in all_tags:
        _, msg = close_trade_group_by_tag(tag, None)
        final_message.append(msg)
    save_trades([])
    if not final_message:
        return load_trades_for_display(), "No running trades found to clear."
    return load_trades_for_display(), "Cleared all trades. Summary:\n" + "\n".join(final_message)


def clear_old_trades():
    all_trades = load_trades()
    if not all_trades: return load_trades_for_display(), "No trades to clear."
    now = datetime.now(pytz.timezone('Asia/Kolkata'))
    active_trades = [t for t in all_trades if t.get('status') == 'Running' and datetime.strptime(t['expiry'], '%d-%b-%Y').date() >= now.date()]
    removed_count = len(all_trades) - len(active_trades)
    if removed_count == 0: message = "No old trades found to clear."
    else: save_trades(active_trades); message = f"Successfully cleared {removed_count} old/completed trade(s)."
    return load_trades_for_display(), message

def check_for_ts_hits():
    all_trades = load_trades()
    active_trades = [t for t in all_trades if t.get('status') == 'Running']
    if not active_trades:
        return

    trades_to_keep = []
    closed_trades_log = []
    something_was_closed = False
    alert_was_sent = False
    today_str = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
    non_running_trades = [t for t in all_trades if t.get('status') != 'Running']

    trades_by_instrument = defaultdict(list)
    for trade in active_trades:
        trades_by_instrument[trade['instrument']].append(trade)

    for instrument, trades in trades_by_instrument.items():
        chain = get_option_chain_data(instrument)
        lot_size = 75 if instrument == "NIFTY" else 15

        if not chain:
            trades_to_keep.extend(trades)
            continue

        for trade in trades:
            current_prices = {'ce': 0.0, 'pe': 0.0, 'ce_hedge': 0.0, 'pe_hedge': 0.0}
            for item in chain['records']['data']:
                if item['expiryDate'] == trade['expiry']:
                    if item['strikePrice'] == trade['ce_strike'] and item.get('CE'): current_prices['ce'] = item['CE']['lastPrice']
                    if item['strikePrice'] == trade['pe_strike'] and item.get('PE'): current_prices['pe'] = item['PE']['lastPrice']
                    if 'ce_hedge_strike' in trade and item['strikePrice'] == trade['ce_hedge_strike'] and item.get('CE'): current_prices['ce_hedge'] = item['CE']['lastPrice']
                    if 'pe_hedge_strike' in trade and item['strikePrice'] == trade['pe_hedge_strike'] and item.get('PE'): current_prices['pe_hedge'] = item['PE']['lastPrice']

            pnl = (trade.get('initial_net_premium', 0) - ((current_prices['ce'] + current_prices['pe']) - (current_prices['ce_hedge'] + current_prices['pe_hedge']))) * lot_size

            trade_closed = False
            alert_reason = ""

            if trade['target_amount'] != 0 and pnl >= trade['target_amount']:
                alert_reason = f"✅✅✅ TARGET HIT ✅✅✅"
                trade_closed = True
            elif trade['stoploss_amount'] != 0 and pnl <= -trade['stoploss_amount']:
                alert_reason = f"❌❌❌ STOP-LOSS HIT ❌❌❌"
                trade_closed = True
            
            if not trade_closed and trade.get('alert_sent_today') != today_str:
                proximity_alert_msg = ""
                if trade['target_amount'] != 0:
                    diff_to_target = trade['target_amount'] - pnl
                    if 0 < diff_to_target <= 500:
                        proximity_alert_msg = (
                            f"⚠️ *Proximity Alert: Nearing Target*\n\n"
                            f"**Trade:** {trade['instrument']} - {trade['reward_type']}\n"
                            f"**Tag:** {trade.get('entry_tag', 'N/A')}\n"
                            f"**Current P/L:** `₹{pnl:,.2f}`\n"
                            f"**Target:** `₹{trade['target_amount']:,.2f}` (Diff: `₹{diff_to_target:,.2f}`)"
                        )
                
                if not proximity_alert_msg and trade['stoploss_amount'] != 0:
                    diff_to_sl = pnl - (-trade['stoploss_amount'])
                    if 0 < diff_to_sl <= 500:
                        proximity_alert_msg = (
                            f"⚠️ *Proximity Alert: Nearing Stop-Loss*\n\n"
                            f"**Trade:** {trade['instrument']} - {trade['reward_type']}\n"
                            f"**Tag:** {trade.get('entry_tag', 'N/A')}\n"
                            f"**Current P/L:** `₹{pnl:,.2f}`\n"
                            f"**Stop-Loss:** `₹{-trade['stoploss_amount']:,.2f}` (Diff: `₹{diff_to_sl:,.2f}`)"
                        )
                
                if proximity_alert_msg:
                    send_telegram_message(proximity_alert_msg)
                    trade['alert_sent_today'] = today_str 
                    alert_was_sent = True

            if trade_closed:
                something_was_closed = True
                trade_copy = trade.copy()
                trade_copy['final_pnl'] = pnl
                trade_copy['closing_prices'] = current_prices
                trade_copy['status'] = 'Target Hit' if alert_reason.startswith('✅') else 'SL Hit'
                trade_copy['end_time'] = datetime.now(pytz.timezone('Asia/Kolkata')).isoformat()
                closed_trades_log.append(trade_copy)
                msg = (
                    f"*{alert_reason}*\n\n"
                    f"**Trade Closed:** {trade['instrument']} - {trade['reward_type']}\n"
                    f"**Tag:** {trade['entry_tag']}\n"
                    f"**Expiry:** {trade['expiry']}\n"
                    f"--- *Legs* ---\n"
                    f"  - SELL CE {trade['ce_strike']} @ {current_prices['ce']:.2f}\n"
                    f"  - SELL PE {trade['pe_strike']} @ {current_prices['pe']:.2f}\n"
                    f"  - BUY CE {trade.get('ce_hedge_strike','N/A')} @ {current_prices['ce_hedge']:.2f}\n"
                    f"  - BUY PE {trade.get('pe_hedge_strike','N/A')} @ {current_prices['pe_hedge']:.2f}\n"
                    f"---------------------\n"
                    f"**Final P/L: ₹{pnl:,.2f}**"
                )
                print(msg)
                send_telegram_message(msg)
            
            if not trade_closed:
                trades_to_keep.append(trade)

    if something_was_closed or alert_was_sent:
        save_trades(trades_to_keep + non_running_trades)

    if something_was_closed:
        history = load_historical_pnl()
        history.extend(closed_trades_log)
        save_historical_pnl(history)

def send_pl_summary(is_eod_report=False):
    now, trades = datetime.now(pytz.timezone('Asia/Kolkata')), load_trades()
    active_trades = [t for t in trades if t.get('status') == 'Running' and datetime.strptime(t['expiry'], '%d-%b-%Y').date() >= now.date()]
    if not active_trades: return
    trade_groups, eod_summary_data = defaultdict(lambda: defaultdict(list)), []
    for trade in active_trades: trade_groups[f"{trade['instrument']}_{trade['expiry']}"][trade.get('entry_tag', 'General')].append(trade)
    for group_key, tagged_trades in trade_groups.items():
        instrument, expiry = group_key.split('_')
        chain = get_option_chain_data(instrument)
        lot_size = 75 if instrument == "NIFTY" else 15

        if not chain: continue
        pl_data_for_image, any_trade_updated = {'title': f"{instrument} {expiry} P/L Update", 'tags': defaultdict(list)}, False
        for tag, trades_in_group in sorted(tagged_trades.items()):
            for trade in trades_in_group:
                current_ce, current_pe, current_ce_hedge, current_pe_hedge = 0.0, 0.0, 0.0, 0.0
                for item in chain['records']['data']:
                    if item['expiryDate'] == trade['expiry']:
                        if item['strikePrice'] == trade['ce_strike'] and item.get('CE'): current_ce = item['CE']['lastPrice']
                        if item['strikePrice'] == trade['pe_strike'] and item.get('PE'): current_pe = item['PE']['lastPrice']
                        if item['strikePrice'] == trade['ce_hedge_strike'] and item.get('CE'): current_ce_hedge = item['CE']['lastPrice']
                        if item['strikePrice'] == trade['pe_hedge_strike'] and item.get('PE'): current_pe_hedge = item['PE']['lastPrice']
                if current_ce == 0.0 and current_pe == 0.0: continue
                pnl = (trade.get('initial_net_premium', 0) - ((current_ce + current_pe) - (current_ce_hedge + current_pe_hedge))) * lot_size
                any_trade_updated = True
                if is_eod_report: eod_summary_data.append(f"-> {trade['id']}: P/L: ₹{pnl:.2f}")
                else: pl_data_for_image['tags'][tag].append({'reward_type': trade['reward_type'], 'pnl': pnl})
        if not is_eod_report and any_trade_updated:
            image_path = generate_pl_update_image(pl_data_for_image, now)
            send_telegram_message(message="", image_paths=[image_path])
            os.remove(image_path)
    if is_eod_report and eod_summary_data: send_telegram_message(f"--- EOD Summary ({now.strftime('%d-%b-%Y %I:%M %p')}) ---\n" + "\n".join(eod_summary_data))

def track_pnl_history():
    """Scheduled to run every 5 mins to record P/L for the EOD graph."""
    global daily_pnl_tracker
    active_trades = [t for t in load_trades() if t.get('status') == 'Running']
    if not active_trades:
        return

    now = datetime.now(pytz.timezone('Asia/Kolkata'))
    
    if now.hour == 9 and now.minute < 20 and daily_pnl_tracker:
        print("Clearing daily P/L tracker for the new day.")
        daily_pnl_tracker.clear()

    time_str = now.strftime('%H:%M')
    trades_by_instrument = defaultdict(list)
    for trade in active_trades:
        trades_by_instrument[trade['instrument']].append(trade)

    for instrument, trades in trades_by_instrument.items():
        chain = get_option_chain_data(instrument)
        if not chain: continue
        lot_size = 75 if instrument == "NIFTY" else 15
        for trade in trades:
            current_ce, current_pe, current_ce_hedge, current_pe_hedge = 0.0, 0.0, 0.0, 0.0
            for item in chain['records']['data']:
                 if item['expiryDate'] == trade['expiry']:
                    if item['strikePrice'] == trade['ce_strike'] and item.get('CE'): current_ce = item['CE']['lastPrice']
                    if item['strikePrice'] == trade['pe_strike'] and item.get('PE'): current_pe = item['PE']['lastPrice']
                    if 'ce_hedge_strike' in trade and item['strikePrice'] == trade['ce_hedge_strike'] and item.get('CE'): current_ce_hedge = item['CE']['lastPrice']
                    if 'pe_hedge_strike' in trade and item['strikePrice'] == trade['pe_hedge_strike'] and item.get('PE'): current_pe_hedge = item['PE']['lastPrice']
            
            pnl = (trade.get('initial_net_premium', 0) - ((current_ce + current_pe) - (current_ce_hedge + current_pe_hedge))) * lot_size
            
            trade_key = f"{trade.get('entry_tag', 'Untagged')} - {trade.get('reward_type', 'Manual')}"
            daily_pnl_tracker[trade_key].append({'time': time_str, 'pnl': pnl})
    print(f"P/L history captured at {time_str}")

def send_daily_pnl_graph_to_telegram():
    """Scheduled for EOD. Plots the tracked 5-min P/L data and sends to Telegram."""
    global daily_pnl_tracker
    if not daily_pnl_tracker:
        print("EOD: No P/L data tracked today to generate a graph.")
        return

    plt.style.use('seaborn-v0_8-darkgrid')
    fig, ax = plt.subplots(figsize=(12, 7))

    for trade_key, history in daily_pnl_tracker.items():
        df = pd.DataFrame(history)
        if not df.empty:
            ax.plot(df['time'], df['pnl'], label=trade_key, marker='o', markersize=3, linestyle='-')

    ax.set_title(f"Intraday P/L Fluctuation - {datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d-%b-%Y')}", fontsize=16)
    ax.set_ylabel("Profit / Loss (₹)")
    ax.set_xlabel("Time (IST)")
    ax.legend(title="Trades", bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.xticks(rotation=45)
    plt.tight_layout(rect=[0, 0, 0.85, 1]) 
    
    filepath = os.path.join(TEMP_DIR, f"daily_pnl_graph_{uuid.uuid4().hex}.png")
    plt.savefig(filepath, dpi=150)
    plt.close(fig)

    send_telegram_message(message="📈 Here is today's P/L performance graph.", image_paths=[filepath])
    os.remove(filepath)
    daily_pnl_tracker.clear()
    
def run_scheduled_analysis(schedule_id, index, calc_type):
    print(f"--- Running scheduled job '{schedule_id}' at {datetime.now(pytz.timezone('Asia/Kolkata'))} ---")
    hedge_perc, data = 10.0, get_option_chain_data(index)
    if not (data and 'records' in data and 'expiryDates' in data['records']): print(f"Scheduled run '{schedule_id}' failed: Could not fetch option chain."); return
    future_expiries = sorted([d for d in data['records']['expiryDates'] if datetime.strptime(d, "%d-%b-%Y").date() >= datetime.now().date()], key=lambda x: datetime.strptime(x, "%d-%b-%Y"))
    if not future_expiries: print(f"Scheduled run '{schedule_id}' failed: No future expiry dates found."); return
    summary_path, hedge_path, payoff_path, _, analysis_data, _, _, _ = generate_analysis(index, calc_type, future_expiries[0], hedge_perc)
    
    # --- Live Auto Trading Integration (BEFORE adding to DB) ---
    live_trade_executed = False
    if live_trade_manager and live_trade_manager.config.enabled:
        print("--- Executing Live Auto Trading ---")
        schedule_info = {"id": schedule_id, "index": index, "calc_type": calc_type}
        auto_trade_result = live_trade_manager.execute_automated_strategy(analysis_data, schedule_info)
        print(f"Auto Trade Result: {auto_trade_result}")
        
        if auto_trade_result.get('status') == 'success':
            live_trade_executed = True
            # Add successfully executed trades to Active Analysis
            add_msg = add_automated_trades_to_active_analysis(analysis_data, auto_trade_result)
            
            # Send auto trading status to Telegram
            auto_msg = f"🔴 *Live Auto Trading Executed* 🔴\n\n"
            auto_msg += f"Mode: {live_trade_manager.config.trading_mode.value.upper()}\n"
            for result in auto_trade_result.get('results', []):
                if isinstance(result, dict):
                    strategy_name = result.get('strategy', 'Unknown')
                    status = result.get('status', 'Unknown')
                    auto_msg += f"• {strategy_name}: {status}\n"
                else:
                    # If result is a string, use it directly
                    auto_msg += f"• {str(result)}\n"
            auto_msg += f"\n{add_msg}"
            send_telegram_message(auto_msg)
        elif auto_trade_result.get('status') not in ['disabled', 'no_strategies']:
            send_telegram_message(f"⚠️ Auto Trading Issue: {auto_trade_result.get('message')}")
    else:
        print("--- Live Auto Trading Disabled ---")
    
    # --- Traditional behavior: Add all strategies to DB if live trading not executed ---
    if not live_trade_executed:
        add_msg = add_trades_to_db(analysis_data)
        print(add_msg)
    
    # Send charts and notification
    send_daily_chart_to_telegram(summary_path, hedge_path, payoff_path, analysis_data)
    send_telegram_message(f"🤖 *Auto-Generation Successful ({index})* 🤖\n\n{add_msg}")

def sync_scheduler_with_settings():
    print("--- Syncing scheduler with settings... ---")
    settings, day_map = load_settings(), {"Monday": "mon", "Tuesday": "tue", "Wednesday": "wed", "Thursday": "thu", "Friday": "fri"}
    for job in scheduler.get_jobs():
        if job.id.startswith('auto_generate_job_'): scheduler.remove_job(job.id)
    for schedule_item in settings.get('schedules', []):
        if schedule_item.get('enabled', False) and schedule_item.get('days') and schedule_item.get('time'):
            try:
                hour, minute = map(int, schedule_item['time'].split(':'))
                day_str = ",".join([day_map[d] for d in schedule_item['days']])
                job_id = f"auto_generate_job_{schedule_item['id']}"
                scheduler.add_job(run_scheduled_analysis, 'cron', day_of_week=day_str, hour=hour, minute=minute, id=job_id, timezone=pytz.timezone('Asia/Kolkata'), misfire_grace_time=600, args=[schedule_item['id'], schedule_item['index'], schedule_item['calc_type']])
                print(f"Scheduled job '{job_id}' for {schedule_item['index']} on {day_str} at {schedule_item['time']}")
            except Exception as e: print(f"Could not load schedule '{schedule_item.get('id')}': {e}")

def load_schedules_for_display():
    df_data = [[s.get('id'), s.get('enabled', False), ", ".join(s.get('days', [])), s.get('time'), s.get('index'), s.get('calc_type')] for s in load_settings().get('schedules', [])]
    return pd.DataFrame(df_data, columns=['ID', 'Enabled', 'Days', 'Time', 'Index', 'Calculation'])

def add_new_schedule(days, time_str, index, calc_type):
    if not all([days, time_str, index, calc_type]): return load_schedules_for_display(), "All fields are required."
    settings = load_settings()
    settings['schedules'].append({"id": uuid.uuid4().hex[:8], "enabled": True, "days": days, "time": time_str, "index": index, "calc_type": calc_type})
    save_settings(settings); sync_scheduler_with_settings()
    return load_schedules_for_display(), f"Successfully added new schedule for {index}."

def delete_schedule_by_id(schedule_id_to_delete):
    if not schedule_id_to_delete: return load_schedules_for_display(), "Please select a schedule to delete."
    settings = load_settings()
    initial_len = len(settings['schedules'])
    settings['schedules'] = [s for s in settings['schedules'] if s.get('id') != schedule_id_to_delete]
    if len(settings['schedules']) == initial_len: return load_schedules_for_display(), f"Error: Could not find schedule ID {schedule_id_to_delete}."
    save_settings(settings); sync_scheduler_with_settings()
    return load_schedules_for_display(), f"Successfully deleted schedule {schedule_id_to_delete}."

def update_expiry_dates(index_name):
    data = get_option_chain_data(index_name)
    if data and 'records' in data and 'expiryDates' in data['records']:
        future_expiries = sorted([d for d in data['records']['expiryDates'] if datetime.strptime(d, "%d-%b-%Y").date() >= datetime.now().date()], key=lambda x: datetime.strptime(x, "%d-%b-%Y"))
        return gr.Dropdown(choices=future_expiries, value=future_expiries[0] if future_expiries else None, interactive=True)
    return gr.Dropdown(choices=["Error: Could not fetch dates."], value="Error: Could not fetch dates.", interactive=False)

def update_monitoring_interval(interval_str):
    try:
        job_id = 'pl_summary'
        if interval_str == "Disable": scheduler.pause_job(job_id); msg = "P/L summary disabled."
        else:
            value, unit = map(str.strip, interval_str.split())
            if unit == 'Mins': scheduler.reschedule_job(job_id, trigger='interval', minutes=int(value))
            elif unit == 'Hour': scheduler.reschedule_job(job_id, trigger='interval', hours=int(value))
            scheduler.resume_job(job_id); msg = f"P/L summary interval set to {interval_str}."
        current_settings = load_settings(); current_settings['update_interval'] = interval_str; save_settings(current_settings)
        print(msg); return msg
    except Exception as e: return f"Failed to update schedule: {e}"

def send_daily_chart_to_telegram(summary_path, hedge_path, payoff_path, analysis_data):
    if not analysis_data: return "Generate charts first."
    title = f"📊 **{datetime.now().strftime('%b-%d %A')} Selling Summary**"
    message_lines = [title] + [f"- {entry['Entry']} Reward (Net Credit): ₹{entry['Net Premium'] * analysis_data['lot_size']:.2f}" for entry in analysis_data['df_data']]
    send_telegram_message("\n".join(message_lines), image_paths=[summary_path, hedge_path, payoff_path])
    return "Message with all 3 charts sent to Telegram."

def cleanup_temp_files():
    """Removes old files from the temporary directory."""
    if os.path.exists(TEMP_DIR):
        print(f"Cleaning up temporary directory: {TEMP_DIR}")
        for filename in os.listdir(TEMP_DIR):
            file_path = os.path.join(TEMP_DIR, filename)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            except Exception as e:
                print(f"Error deleting old temp file {file_path}: {e}")

def add_manual_trade_to_db(instrument, expiry_str, tag,
                           ce_strike, ce_price, pe_strike, pe_price,
                           ce_hedge_strike, ce_hedge_price, pe_hedge_strike, pe_hedge_price,
                           target_percentage,
                           target_amount,
                           sl_amount,
                           send_telegram,
                           live_trading,
                           quantity):
    try:
        # 1. VALIDATION FOR SINGLE LEG: Check if at least one leg is provided.
        if not (ce_strike and ce_price) and not (pe_strike and pe_price):
            return "You must enter at least one full leg (e.g., CE Strike and CE Price).", load_trades_for_display()

        if not all([instrument, expiry_str, tag]):
            return "Instrument, Expiry, and Tag are required.", load_trades_for_display()

        try:
            datetime.strptime(expiry_str, '%d-%b-%Y')
        except (ValueError, TypeError):
            return "Error: Expiry date must be in 'dd-Mon-yyyy' format (e.g., 14-Aug-2025).", load_trades_for_display()

        # 2. DATA CONVERSION & DEFAULTS for potentially empty fields
        ce_strike = int(ce_strike) if ce_strike else 0
        ce_price = float(ce_price) if ce_price else 0.0
        pe_strike = int(pe_strike) if pe_strike else 0
        pe_price = float(pe_price) if pe_price else 0.0

        ce_hedge_strike = int(ce_hedge_strike) if ce_hedge_strike else 0
        ce_hedge_price = float(ce_hedge_price) if ce_hedge_price else 0.0
        pe_hedge_strike = int(pe_hedge_strike) if pe_hedge_strike else 0
        pe_hedge_price = float(pe_hedge_price) if pe_hedge_price else 0.0

        # 3. TARGET/SL CALCULATION LOGIC
        lot_size = 75 if instrument == "NIFTY" else 15
        initial_net_premium = (ce_price + pe_price) - (ce_hedge_price + pe_hedge_price)
        combined_premium = ce_price + pe_price
        
        # Calculate the default using percentage
        target_sl_from_pct = abs(combined_premium * (float(target_percentage) / 100) * lot_size) if target_percentage else 0
        
        # Prioritize the explicitly provided amount over the percentage calculation
        final_target = float(target_amount) if target_amount and float(target_amount) > 0 else target_sl_from_pct
        final_stoploss = float(sl_amount) if sl_amount and float(sl_amount) > 0 else target_sl_from_pct

        # --- CREATE AND SAVE TRADE ---
        trades = load_trades()
        reward_type = "Manual"
        trade_id = f"{instrument}_{expiry_str}_{reward_type}_{tag.replace(' ', '')}_{int(time.time())}"

        new_trade = {
            "id": trade_id, "start_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "instrument": instrument, "expiry": expiry_str, "reward_type": reward_type,
            "ce_strike": ce_strike, "pe_strike": pe_strike,
            "ce_hedge_strike": ce_hedge_strike, "pe_hedge_strike": pe_hedge_strike,
            "initial_net_premium": initial_net_premium,
            "initial_prices": { "ce": ce_price, "pe": pe_price, "ce_hedge": ce_hedge_price, "pe_hedge": pe_hedge_price },
            "target_amount": final_target,
            "stoploss_amount": final_stoploss,
            "status": "Running", "entry_tag": tag
        }
        trades.append(new_trade)
        save_trades(trades)

        # --- LIVE TRADING: PLACE ACTUAL BROKER ORDERS ---
        order_results = []
        if live_trading:
            try:
                # Determine which broker to use
                active_broker = None
                broker_api = None
                
                # Try Flattrade first
                success, message = initialize_flattrade_for_trading()
                if success and flattrade_api:
                    active_broker = "Flattrade"
                    broker_api = flattrade_api
                else:
                    # Try Angel One if Flattrade is not available
                    success, message = initialize_angelone_for_trading()
                    if success and angelone_api:
                        active_broker = "AngelOne"
                        broker_api = angelone_api
                
                if not active_broker or not broker_api:
                    order_results.append(f"❌ No broker available for trading. Flattrade: {message}")
                else:
                    order_results.append(f"📡 Using {active_broker} for live trading")
                    
                    lot_size = 75 if instrument == "NIFTY" else 15
                    total_quantity = int(quantity) * lot_size
                    
                    # Generate option symbols based on broker
                    month_map = {
                        'Jan': 'JAN', 'Feb': 'FEB', 'Mar': 'MAR', 'Apr': 'APR', 'May': 'MAY', 'Jun': 'JUN',
                        'Jul': 'JUL', 'Aug': 'AUG', 'Sep': 'SEP', 'Oct': 'OCT', 'Nov': 'NOV', 'Dec': 'DEC'
                    }
                    
                    # Parse expiry date for symbol generation
                    try:
                        expiry_date = datetime.strptime(expiry_str, '%d-%b-%Y')
                        year_short = str(expiry_date.year)[-2:]
                        month_code = month_map.get(expiry_date.strftime('%b'))
                        day = expiry_date.strftime('%d')
                        
                        if active_broker == "Flattrade":
                            # Flattrade symbol format: SYMBOL + DD + MMM + YY + C/P + STRIKE
                            symbol_base = f"{instrument}{day}{month_code}{year_short}"
                        else:  # Angel One
                            # Angel One symbol format: SYMBOL + DD + MMM + YY + CE/PE
                            symbol_base = f"{instrument}{day}{month_code}{year_short}"
                        
                        order_results.append(f"📊 Trading {total_quantity} qty ({quantity} lots × {lot_size})")
                        print(f"🔧 Symbol base: {symbol_base}")
                        
                        # FAST PARALLEL ORDER PLACEMENT
                        # Step 1: Prepare all orders
                        hedge_orders = []
                        sell_orders = []
                        
                        # Prepare CE HEDGE BUY order
                        if ce_hedge_strike > 0 and ce_hedge_price > 0:
                            if active_broker == "Flattrade":
                                ce_hedge_symbol = f"{symbol_base}C{int(ce_hedge_strike)}"
                            else:  # Angel One
                                ce_hedge_symbol = f"{symbol_base}{int(ce_hedge_strike)}CE"
                            
                            hedge_orders.append({
                                'type': 'CE_HEDGE_BUY',
                                'symbol': ce_hedge_symbol,
                                'quantity': total_quantity,
                                'transaction_type': 'BUY' if active_broker == "AngelOne" else 'B',
                                'product': 'CARRYFORWARD' if active_broker == "AngelOne" else 'NRML'
                            })
                        
                        # Prepare PE HEDGE BUY order
                        if pe_hedge_strike > 0 and pe_hedge_price > 0:
                            if active_broker == "Flattrade":
                                pe_hedge_symbol = f"{symbol_base}P{int(pe_hedge_strike)}"
                            else:  # Angel One
                                pe_hedge_symbol = f"{symbol_base}{int(pe_hedge_strike)}PE"
                            
                            hedge_orders.append({
                                'type': 'PE_HEDGE_BUY',
                                'symbol': pe_hedge_symbol,
                                'quantity': total_quantity,
                                'transaction_type': 'BUY' if active_broker == "AngelOne" else 'B',
                                'product': 'CARRYFORWARD' if active_broker == "AngelOne" else 'NRML'
                            })
                        
                        # Prepare CE SELL order
                        if ce_strike > 0 and ce_price > 0:
                            if active_broker == "Flattrade":
                                ce_symbol = f"{symbol_base}C{int(ce_strike)}"
                            else:  # Angel One
                                ce_symbol = f"{symbol_base}{int(ce_strike)}CE"
                            
                            sell_orders.append({
                                'type': 'CE_SELL',
                                'symbol': ce_symbol,
                                'quantity': total_quantity,
                                'transaction_type': 'SELL' if active_broker == "AngelOne" else 'S',
                                'product': 'CARRYFORWARD' if active_broker == "AngelOne" else 'NRML'
                            })
                        
                        # Prepare PE SELL order
                        if pe_strike > 0 and pe_price > 0:
                            if active_broker == "Flattrade":
                                pe_symbol = f"{symbol_base}P{int(pe_strike)}"
                            else:  # Angel One
                                pe_symbol = f"{symbol_base}{int(pe_strike)}PE"
                            
                            sell_orders.append({
                                'type': 'PE_SELL',
                                'symbol': pe_symbol,
                                'quantity': total_quantity,
                                'transaction_type': 'SELL' if active_broker == "AngelOne" else 'S',
                                'product': 'CARRYFORWARD' if active_broker == "AngelOne" else 'NRML'
                            })
                        
                        # Step 2: Place HEDGE orders in parallel FIRST
                        if hedge_orders:
                            print(f"📊 Placing {len(hedge_orders)} HEDGE orders in parallel...")
                            start_time = time.time()
                            
                            with ThreadPoolExecutor(max_workers=4) as executor:
                                hedge_futures = {}
                                
                                for order in hedge_orders:
                                    if active_broker == "Flattrade":
                                        future = executor.submit(
                                            flattrade_api.place_order,
                                            symbol=order['symbol'],
                                            quantity=order['quantity'],
                                            price=None,
                                            order_type='MKT',
                                            transaction_type=order['transaction_type'],
                                            product=order['product']
                                        )
                                    else:  # Angel One
                                        # For Angel One, we need symboltoken - using placeholder for now
                                        future = executor.submit(
                                            angelone_api.place_order,
                                            symbol=order['symbol'],
                                            symboltoken="0",  # Would need to fetch actual symboltoken
                                            quantity=order['quantity'],
                                            price=None,
                                            order_type='MARKET',
                                            transaction_type=order['transaction_type'],
                                            product=order['product'],
                                            exchange='NFO'
                                        )
                                    
                                    hedge_futures[future] = order
                                
                                for future in as_completed(hedge_futures):
                                    order_info = hedge_futures[future]
                                    try:
                                        result = future.result()
                                        
                                        if active_broker == "Flattrade":
                                            if result and result.get('stat') == 'Ok':
                                                order_results.append(f"✅ {order_info['type']}: {order_info['symbol']} @ Market Price (Order ID: {result.get('norenordno', 'N/A')})")
                                            else:
                                                order_results.append(f"❌ {order_info['type']} Failed: {result.get('emsg', 'Unknown error') if result else 'No response'}")
                                        else:  # Angel One
                                            if result and result.get('status'):
                                                order_id = result.get('data', {}).get('orderid', 'N/A')
                                                order_results.append(f"✅ {order_info['type']}: {order_info['symbol']} @ Market Price (Order ID: {order_id})")
                                            else:
                                                order_results.append(f"❌ {order_info['type']} Failed: {result.get('message', 'Unknown error') if result else 'No response'}")
                                    except Exception as e:
                                        order_results.append(f"❌ {order_info['type']} Failed: {str(e)}")
                            
                            hedge_time = time.time() - start_time
                            print(f"✅ HEDGE orders completed in {hedge_time:.2f} seconds")
                        
                        # Step 3: Place SELL orders in parallel AFTER hedges
                        if sell_orders:
                            print(f"📊 Placing {len(sell_orders)} SELL orders in parallel...")
                            start_time = time.time()
                            
                            with ThreadPoolExecutor(max_workers=4) as executor:
                                sell_futures = {}
                                
                                for order in sell_orders:
                                    if active_broker == "Flattrade":
                                        future = executor.submit(
                                            flattrade_api.place_order,
                                            symbol=order['symbol'],
                                            quantity=order['quantity'],
                                            price=None,
                                            order_type='MKT',
                                            transaction_type=order['transaction_type'],
                                            product=order['product']
                                        )
                                    else:  # Angel One
                                        future = executor.submit(
                                            angelone_api.place_order,
                                            symbol=order['symbol'],
                                            symboltoken="0",  # Would need to fetch actual symboltoken
                                            quantity=order['quantity'],
                                            price=None,
                                            order_type='MARKET',
                                            transaction_type=order['transaction_type'],
                                            product=order['product'],
                                            exchange='NFO'
                                        )
                                    
                                    sell_futures[future] = order
                                
                                for future in as_completed(sell_futures):
                                    order_info = sell_futures[future]
                                    try:
                                        result = future.result()
                                        
                                        if active_broker == "Flattrade":
                                            if result and result.get('stat') == 'Ok':
                                                order_results.append(f"✅ {order_info['type']}: {order_info['symbol']} @ Market Price (Order ID: {result.get('norenordno', 'N/A')})")
                                            else:
                                                order_results.append(f"❌ {order_info['type']} Failed: {result.get('emsg', 'Unknown error') if result else 'No response'}")
                                        else:  # Angel One
                                            if result and result.get('status'):
                                                order_id = result.get('data', {}).get('orderid', 'N/A')
                                                order_results.append(f"✅ {order_info['type']}: {order_info['symbol']} @ Market Price (Order ID: {order_id})")
                                            else:
                                                order_results.append(f"❌ {order_info['type']} Failed: {result.get('message', 'Unknown error') if result else 'No response'}")
                                    except Exception as e:
                                        order_results.append(f"❌ {order_info['type']} Failed: {str(e)}")
                            
                            sell_time = time.time() - start_time
                            print(f"✅ SELL orders completed in {sell_time:.2f} seconds")
                        
                    except Exception as symbol_error:
                        order_results.append(f"❌ Symbol generation error: {str(symbol_error)}")
                        
            except Exception as order_error:
                order_results.append(f"❌ Live trading error: {str(order_error)}")

        # --- TELEGRAM NOTIFICATION ---
        if send_telegram:
            trade_desc = []
            if ce_strike > 0: trade_desc.append(f"SELL CE {ce_strike}")
            if pe_strike > 0: trade_desc.append(f"SELL PE {pe_strike}")
            
            hedge_desc = []
            if ce_hedge_strike > 0: hedge_desc.append(f"BUY CE {ce_hedge_strike}")
            if pe_hedge_strike > 0: hedge_desc.append(f"BUY PE {pe_hedge_strike}")
            hedge_str = f"**Hedge:** {' & '.join(hedge_desc) if hedge_desc else 'None'}"
            
            msg = (f"🔔 *Manual Intraday Trade Added* 🔔\n\n"
                   f"**Tag:** {tag}\n"
                   f"**Instrument:** {instrument} ({expiry_str})\n"
                   f"**Trade:** {' & '.join(trade_desc)}\n"
                   f"{hedge_str}\n"
                   f"**Net Credit:** ₹{initial_net_premium:.2f} (Points)\n"
                   f"**Target:** ₹{final_target:,.2f}\n"
                   f"**Stop-Loss:** ₹{final_stoploss:,.2f}")
            send_telegram_message(msg)

        # Prepare success message
        success_msg = f"Successfully added manual trade '{trade_id}'."
        if live_trading and order_results:
            success_msg += f"\n\n📱 LIVE TRADING RESULTS:\n" + "\n".join(order_results)
        
        return success_msg, load_trades_for_display()
    except Exception as e:
        traceback.print_exc()
        return f"An error occurred: {e}", load_trades_for_display()

def generate_new_historical_ui():
    history = load_historical_pnl()
    if not history:
        no_data_html = "<div style='text-align: center; padding: 50px; font-size: 1.5em; color: #888;'>No Historical P&L Data Found</div>"
        return no_data_html, None, no_data_html

    df = pd.DataFrame(history)
    df['end_time'] = pd.to_datetime(df['end_time'])
    df = df.sort_values('end_time')

    total_pnl = df['final_pnl'].sum()
    total_trades = len(df)
    winning_trades = df[df['final_pnl'] > 0]
    losing_trades = df[df['final_pnl'] < 0]
    
    win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0
    avg_profit = winning_trades['final_pnl'].mean() if len(winning_trades) > 0 else 0
    avg_loss = losing_trades['final_pnl'].mean() if len(losing_trades) > 0 else 0
    
    total_profit = winning_trades['final_pnl'].sum()
    total_loss = abs(losing_trades['final_pnl'].sum())
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')

    def format_pnl(value):
        color = '#27AE60' if value >= 0 else '#C0392B'
        return f"<span style='color:{color};'>₹{value:,.2f}</span>"

    kpi_html = f"""
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px;">
        <div style="background-color: #f4f6f6; padding: 16px; border-radius: 8px; text-align: center;">
            <h4 style="margin: 0 0 8px 0; color: #566573;">Total P&L</h4>
            <p style="margin: 0; font-size: 1.8em; font-weight: bold;">{format_pnl(total_pnl)}</p>
        </div>
        <div style="background-color: #f4f6f6; padding: 16px; border-radius: 8px; text-align: center;">
            <h4 style="margin: 0 0 8px 0; color: #566573;">Win Rate</h4>
            <p style="margin: 0; font-size: 1.8em; font-weight: bold;">{win_rate:.2f}%</p>
        </div>
        <div style="background-color: #f4f6f6; padding: 16px; border-radius: 8px; text-align: center;">
            <h4 style="margin: 0 0 8px 0; color: #566573;">Profit Factor</h4>
            <p style="margin: 0; font-size: 1.8em; font-weight: bold;">{profit_factor:.2f}</p>
        </div>
        <div style="background-color: #f4f6f6; padding: 16px; border-radius: 8px; text-align: center;">
            <h4 style="margin: 0 0 8px 0; color: #566573;">Avg. Profit / Loss</h4>
            <p style="margin: 0; font-size: 1.2em; font-weight: bold;">{format_pnl(avg_profit)} / {format_pnl(avg_loss)}</p>
        </div>
    </div>
    """

    df['cumulative_pnl'] = df['final_pnl'].cumsum()
    plt.style.use('seaborn-v0_8-whitegrid')
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(df['end_time'], df['cumulative_pnl'], marker='o', linestyle='-', color='#2980B9')
    ax.fill_between(df['end_time'], df['cumulative_pnl'], 0, alpha=0.1, color='#2980B9')
    ax.set_title('Cumulative P&L Over Time', fontsize=16, fontweight='bold')
    ax.set_ylabel('Cumulative Profit / Loss (₹)')
    fig.autofmt_xdate()
    plt.tight_layout()
    filepath = os.path.join(TEMP_DIR, f"cumulative_pnl_{uuid.uuid4().hex}.png")
    plt.savefig(filepath, dpi=150); plt.close(fig)

    display_df = df.copy()
    display_df['end_time'] = display_df['end_time'].dt.strftime('%Y-%m-%d %H:%M')
    display_df = display_df[['end_time', 'entry_tag', 'instrument', 'reward_type', 'ce_strike', 'pe_strike', 'initial_net_premium', 'final_pnl']]
    display_df.rename(columns={
        'end_time': 'Closed At', 'entry_tag': 'Tag', 'instrument': 'Instrument', 'reward_type': 'Type',
        'ce_strike': 'CE Sell', 'pe_strike': 'PE Sell', 'initial_net_premium': 'Net Credit (Pts)', 'final_pnl': 'Final P&L (₹)'
    }, inplace=True)
    
    def style_pnl_column(pnl):
        color = '#27AE60' if pnl >= 0 else '#C0392B'
        return f'<span style="color: {color}; font-weight: bold;">{pnl:,.2f}</span>'
    display_df['Final P&L (₹)'] = display_df['Final P&L (₹)'].apply(style_pnl_column)

    table_html = display_df.to_html(index=False, escape=False, classes='styled-table')
    
    style = """
    <style>
        .styled-table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
        .styled-table thead th { background-color: #2c3e50; color: white; text-align: left; padding: 12px 15px; }
        .styled-table tbody td { padding: 12px 15px; border-bottom: 1px solid #ddd; }
        .styled-table tbody tr:nth-of-type(even) { background-color: #f3f3f3; }
        .styled-table tbody tr:last-of-type { border-bottom: 2px solid #2c3e50; }
    </style>
    """
    
    return kpi_html, filepath, style + table_html

def clear_pnl_history_new_ui():
    save_historical_pnl([])
    no_data_html = "<div style='text-align: center; padding: 50px; font-size: 1.5em; color: #888;'>No Historical P&L Data Found</div>"
    return "Successfully cleared all P&L history.", no_data_html, None, no_data_html

# --- Live Auto Trading UI Functions ---
def update_auto_trade_settings(enabled, mode, high_reward, mid_reward, low_reward, 
                             use_existing_targets, auto_square_off, 
                             position_multiplier, max_positions):
    """Update live auto trading configuration"""
    try:
        if not live_trade_manager:
            return "Error: Live trade manager not initialized", get_auto_trade_status()
        
        # Update configuration
        live_trade_manager.config.enabled = enabled
        live_trade_manager.config.trading_mode = TradingMode.LIVE if mode == "live" else TradingMode.PAPER
        live_trade_manager.config.strategies = {
            "High Reward": high_reward,
            "Mid Reward": mid_reward,
            "Low Reward": low_reward
        }
        live_trade_manager.config.use_existing_targets = use_existing_targets
        live_trade_manager.config.auto_square_off = auto_square_off
        live_trade_manager.config.position_size_multiplier = position_multiplier
        live_trade_manager.config.max_positions_per_strategy = int(max_positions)
        
        # Save configuration
        live_trade_manager.save_config()
        
        # Start/stop position monitoring based on settings
        if enabled and auto_square_off:
            live_trade_manager.start_position_monitoring()
        else:
            live_trade_manager.stop_position_monitoring()
        
        status_msg = f"Auto trading settings saved successfully. Mode: {mode.upper()}"
        if enabled:
            enabled_strategies = [k for k, v in live_trade_manager.config.strategies.items() if v]
            status_msg += f"\nEnabled strategies: {', '.join(enabled_strategies) if enabled_strategies else 'None'}"
        
        return status_msg, get_auto_trade_status()
        
    except Exception as e:
        return f"Error saving auto trade settings: {str(e)}", get_auto_trade_status()

def get_auto_trade_status():
    """Get current auto trading status for display"""
    try:
        if not live_trade_manager:
            return "**Status:** Live trade manager not available"
        
        status = live_trade_manager.get_automation_status()
        
        status_md = "**Live Auto Trading Status**\n\n"
        status_md += f"• **Enabled:** {'✅ Yes' if status['enabled'] else '❌ No'}\n"
        status_md += f"• **Mode:** {status['trading_mode'].upper()}\n"
        
        enabled_strategies = [k for k, v in status['active_strategies'].items() if v]
        status_md += f"• **Active Strategies:** {', '.join(enabled_strategies) if enabled_strategies else 'None'}\n"
        status_md += f"• **Active Positions:** {status['active_positions_count']}\n"
        status_md += f"• **Total Positions:** {status['total_positions']}\n"
        status_md += f"• **Total P/L:** ₹{status['total_pnl']:.2f}\n"
        status_md += f"• **Auto Square-off:** {'✅ Enabled' if status['auto_square_off_enabled'] else '❌ Disabled'}\n"
        status_md += f"• **Use Generated Targets:** {'✅ Yes' if status['use_existing_targets'] else '❌ No'}"
        
        return status_md
        
    except Exception as e:
        return f"**Status:** Error loading status - {str(e)}"

def load_auto_trade_settings_for_ui():
    """Load auto trade settings for UI initialization"""
    try:
        if not live_trade_manager:
            return False, "live", True, False, False, True, True, 1.0, 1
        
        config = live_trade_manager.config
        strategies = config.strategies or {}
        return (
            config.enabled,
            config.trading_mode.value,
            strategies.get("High Reward", True),
            strategies.get("Mid Reward", False), 
            strategies.get("Low Reward", False),
            config.use_existing_targets,
            config.auto_square_off,
            config.position_size_multiplier,
            config.max_positions_per_strategy
        )
    except Exception as e:
        print(f"Error loading auto trade settings: {e}")
        return False, "live", True, False, False, True, True, 1.0, 1

def build_ui():
    with gr.Blocks(title="FiFTO Analyzer") as demo:
        gr.Markdown("# FiFTO WEEKLY SELLING")
        analysis_data_state, summary_filepath_state, hedge_filepath_state, payoff_filepath_state = gr.State(), gr.State(), gr.State(), gr.State()

        with gr.Tabs():
            with gr.TabItem("Trade Generator"):
                with gr.Group():
                    with gr.Row():
                        index_dropdown = gr.Dropdown(["NIFTY", "BANKNIFTY"], label="Select Index", value="NIFTY")
                        calc_dropdown = gr.Dropdown(["Weekly", "Monthly"], label="Select Calculation Type", value="Weekly")
                        expiry_dropdown = gr.Dropdown(label="Select Expiry Date", info="Loading...", interactive=False)
                    with gr.Row(): hedge_premium_slider = gr.Slider(5.0, 25.0, 10.0, step=1.0, label="Hedge (Buy) Premium as % of Sold Premium")
                    with gr.Row(): run_button, reset_button = gr.Button("Generate Charts", variant="primary"), gr.Button("Reset / Stop")
                status_textbox_gen = gr.Textbox(label="Status", interactive=False)
                with gr.Tabs():
                    with gr.TabItem("Analysis Summary Chart"): output_summary_image = gr.Image(show_label=False)
                    with gr.TabItem("Hedge (BUY) Chart"): output_hedge_image = gr.Image(show_label=False)
                    with gr.TabItem("Payoff Chart (SELL only)"): output_payoff_image = gr.Image(show_label=False)
                with gr.Row(): add_button, telegram_button = gr.Button("Add to Analysis"), gr.Button("Send to Telegram")

            with gr.TabItem("Manual Trade Entry"):
                gr.Markdown("## Manually Add an Intraday Trade")
                gr.Markdown("⚠️ **WARNING**: Enable live trading only if you want to place actual orders with your broker!")
                with gr.Row():
                    manual_instrument = gr.Dropdown(["NIFTY", "BANKNIFTY"], label="Select Index", value="NIFTY")
                    manual_expiry = gr.Dropdown(label="Select Expiry Date", info="Loading...", interactive=False)
                    manual_tag = gr.Textbox(label="Trade Group / Tag", value="Intraday Selling")
                
                # MODIFIED: Added amount fields for Target and SL
                with gr.Row():
                    manual_target_percentage = gr.Number(label="Target/SL by Percentage (%)", value=85)
                    manual_target_amount = gr.Number(label="Target by Amount (₹)")
                    manual_sl_amount = gr.Number(label="Stop-Loss by Amount (₹)")

                # MODIFIED: Labels now say (Optional)
                with gr.Row():
                    manual_ce_strike = gr.Number(label="CE Strike (Optional)")
                    manual_ce_price = gr.Number(label="CE Entry Price (Optional)")
                    manual_pe_strike = gr.Number(label="PE Strike (Optional)")
                    manual_pe_price = gr.Number(label="PE Entry Price (Optional)")
                
                with gr.Row():
                    manual_ce_hedge_strike = gr.Number(label="CE Hedge Strike (Optional)")
                    manual_ce_hedge_price = gr.Number(label="CE Hedge Price (Optional)")
                    manual_pe_hedge_strike = gr.Number(label="PE Hedge Strike (Optional)")
                    manual_pe_hedge_price = gr.Number(label="PE Hedge Price (Optional)")
                
                with gr.Row():
                    manual_telegram_check = gr.Checkbox(label="Send Telegram notification when adding?", value=True)
                    manual_live_trading = gr.Checkbox(label="🔴 PLACE LIVE ORDERS WITH BROKER", value=False)
                
                with gr.Row():
                    manual_quantity = gr.Number(label="Quantity (Lots)", value=1, minimum=1)
                    manual_add_button = gr.Button("Add Manual Trade", variant="primary")
                manual_status_textbox = gr.Textbox(label="Status", interactive=False)

            with gr.TabItem("Active Analysis"):
                def update_active_analysis_tab():
                    trades_df_data = load_trades_for_display()
                    all_trades = load_trades()
                    active_tags = sorted(list(set(t.get('entry_tag') for t in all_trades if t.get('entry_tag') and t.get('status') == 'Running')))
                    dropdown_update = gr.Dropdown(choices=active_tags, label="Select Active Trade Group to Close", interactive=bool(active_tags))
                    return trades_df_data, dropdown_update

                gr.Markdown("### Manual Trade Management")
                analysis_df = gr.DataFrame(label="Monitored Trades", interactive=False)
                status_active_analysis = gr.Textbox(label="Status", interactive=False)
                with gr.Row():
                    group_close_dropdown = gr.Dropdown(choices=[], label="Select Active Trade Group to Close", interactive=False)
                    group_close_button = gr.Button("Close Selected Group", variant="primary")
                with gr.Row():
                    clear_old_trades_button, clear_all_trades_button = gr.Button("Clear Old Trades"), gr.Button("Clear ALL Trades", variant="stop")

                dummy_trigger = gr.Number(value=0, visible=False)
                def sixty_second_timer_loop():
                    while True:
                        time.sleep(60)
                        yield time.time()
                dummy_trigger.change(fn=update_active_analysis_tab, inputs=None, outputs=[analysis_df, group_close_dropdown])
                demo.load(fn=update_active_analysis_tab, inputs=None, outputs=[analysis_df, group_close_dropdown])
                demo.load(fn=sixty_second_timer_loop, inputs=None, outputs=[dummy_trigger])

                group_close_button.click(fn=close_trade_group_by_tag, inputs=[group_close_dropdown, analysis_df], outputs=[analysis_df, status_active_analysis]).then(fn=update_active_analysis_tab, outputs=[analysis_df, group_close_dropdown])
                clear_old_trades_button.click(fn=clear_old_trades, outputs=[analysis_df, status_active_analysis]).then(fn=update_active_analysis_tab, outputs=[analysis_df, group_close_dropdown])
                clear_all_trades_button.click(fn=clear_all_trades, outputs=[analysis_df, status_active_analysis]).then(fn=update_active_analysis_tab, outputs=[analysis_df, group_close_dropdown])
                
                manual_add_button.click(
                    fn=add_manual_trade_to_db, 
                    inputs=[
                        manual_instrument, manual_expiry, manual_tag, 
                        manual_ce_strike, manual_ce_price, manual_pe_strike, manual_pe_price, 
                        manual_ce_hedge_strike, manual_ce_hedge_price, manual_pe_hedge_strike, manual_pe_hedge_price,
                        manual_target_percentage,
                        manual_target_amount, # New Input
                        manual_sl_amount,     # New Input
                        manual_telegram_check,
                        manual_live_trading,  # New Input
                        manual_quantity       # New Input
                    ], 
                    outputs=[manual_status_textbox, analysis_df]
                ).then(fn=update_active_analysis_tab, outputs=[analysis_df, group_close_dropdown])

            with gr.TabItem("Historical P&L Tracker"):
                gr.Markdown("## Historical Trade Performance")
                history_status_box = gr.Textbox(label="Status", interactive=False)
                kpi_display = gr.Markdown()
                history_chart = gr.Image(label="Cumulative P&L Chart", interactive=False)
                history_html_table = gr.HTML()
                with gr.Row():
                    refresh_history_btn = gr.Button("Refresh History")
                    clear_history_btn = gr.Button("Clear All P&L History", variant="stop")

                demo.load(fn=generate_new_historical_ui, outputs=[kpi_display, history_chart, history_html_table])
                refresh_history_btn.click(fn=generate_new_historical_ui, outputs=[kpi_display, history_chart, history_html_table])
                clear_history_btn.click(fn=clear_pnl_history_new_ui, outputs=[history_status_box, kpi_display, history_chart, history_html_table])

            with gr.TabItem("Broker Accounts"):
                gr.Markdown("## Live Broker Account Configuration")
                gr.Markdown("Configure your broker accounts for live trading. Currently supports Flattrade, AngelOne, and Zerodha.")
                
                broker_status_display = gr.Markdown(value="**Loading broker status...**")
                
                with gr.Tabs():
                    # Flattrade Configuration
                    with gr.TabItem("Flattrade"):
                        gr.Markdown("""
                        ### Flattrade Configuration
                        **Setup Instructions:**
                        1. Login to your Flattrade account
                        2. Go to API section and create a new API app
                        3. Get your Client ID, API Key, and Secret Key
                        4. Use the OAuth authentication for secure access
                        """)
                        
                        flattrade_enabled = gr.Checkbox(label="Enable Flattrade Integration", value=True)
                        flattrade_client_id = gr.Textbox(label="Client ID", placeholder="Enter your Flattrade Client ID", value="FT003862")
                        flattrade_api_key = gr.Textbox(label="API Key", placeholder="Enter your Flattrade API Key", type="password", value="2475be2c2a5843fa8da2f53c55330619")
                        flattrade_secret_key = gr.Textbox(label="Secret Key", placeholder="Enter your Flattrade Secret Key", type="password", value="2025.dd4e574e686741f7b62e162c06e7bc5a6c9e9036bcafa3a5")
                        
                        with gr.Row():
                            save_flattrade_btn = gr.Button("Save Flattrade Settings", variant="primary")
                            test_flattrade_btn = gr.Button("Test Connection", variant="secondary")
                        
                        gr.Markdown("### OAuth Authentication")
                        gr.Markdown("**🔐 Secure One-Click Authentication**")
                        gr.Markdown("⚠️ **Important:** OAuth URL uses the **API Key** field above (not Client ID)")
                        
                        with gr.Accordion("OAuth Setup Instructions", open=False):
                            oauth_instructions = gr.Markdown(value=start_oauth_server_instructions())
                        
                        flattrade_redirect_url = gr.Textbox(
                            label="Redirect URL", 
                            value="http://localhost:3001/callback",
                            info="🔑 OAuth will use API Key as app_key parameter"
                        )
                        
                        with gr.Row():
                            generate_oauth_btn = gr.Button("Generate OAuth URL", variant="secondary")
                            check_auth_code_btn = gr.Button("Check Authorization Code", variant="secondary")
                        
                        oauth_url_display = gr.Markdown(value="**OAuth URL will appear here after clicking Generate OAuth URL**")
                        auth_code_status = gr.Markdown(value="**Authorization status will appear here**")
                        
                        flattrade_status = gr.Markdown(value="**Status:** Loading...")
                    
                    # AngelOne Configuration  
                    with gr.TabItem("AngelOne"):
                        gr.Markdown("""
                        ### AngelOne (Angel Broking SmartAPI) Configuration
                        **Setup Instructions:**
                        1. Login to your AngelOne account and go to [SmartAPI Portal](https://smartapi.angelbroking.com/)
                        2. Create a new app to get API credentials
                        3. Setup TOTP (Google Authenticator) in your AngelOne account
                        4. Get your Client ID, API Key, Login PIN, and TOTP Secret Key
                        
                        **Required Fields:**
                        - **Client ID**: Your AngelOne trading account ID
                        - **API Key**: From SmartAPI app registration  
                        - **Login PIN**: Your AngelOne account PIN (4 or 6 digits)
                        - **TOTP Secret**: Secret key from TOTP setup (for 2FA)
                        """)
                        
                        angelone_enabled = gr.Checkbox(label="Enable AngelOne Integration", value=False)
                        angelone_client_id = gr.Textbox(
                            label="Client ID", 
                            placeholder="Enter your AngelOne Client ID (e.g., A123456)",
                            info="Your AngelOne trading account ID"
                        )
                        angelone_api_key = gr.Textbox(
                            label="API Key", 
                            placeholder="Enter your AngelOne API Key", 
                            type="password",
                            info="API Key from SmartAPI app registration"
                        )
                        angelone_client_pin = gr.Textbox(
                            label="Login PIN", 
                            placeholder="Enter your AngelOne login PIN", 
                            type="password",
                            info="Your AngelOne account PIN (4 or 6 digits)"
                        )
                        angelone_totp_key = gr.Textbox(
                            label="TOTP Secret Key", 
                            placeholder="Enter your TOTP secret key", 
                            type="password",
                            info="Secret key from TOTP setup (Google Authenticator)"
                        )
                        
                        with gr.Accordion("Web Authentication (Optional)", open=False):
                            gr.Markdown("""
                            **Alternative Web-based Authentication:**
                            For users who prefer web-based login similar to Flattrade OAuth flow.
                            This is optional - you can use either direct API login or web authentication.
                            """)
                            angelone_redirect_url = gr.Textbox(
                                label="Redirect URL", 
                                value="http://localhost:3001/callback",
                                info="URL for web-based authentication callback"
                            )
                            with gr.Row():
                                generate_angelone_oauth_btn = gr.Button("Generate Publisher Login URL", variant="secondary")
                                check_angelone_callback_btn = gr.Button("Check Callback Status", variant="secondary")
                            
                            angelone_oauth_url_display = gr.Markdown(value="**Publisher Login URL will appear here**")
                            angelone_callback_status = gr.Markdown(value="**Callback status will appear here**")
                        
                        with gr.Row():
                            save_angelone_btn = gr.Button("Save AngelOne Settings", variant="primary")
                            test_angelone_btn = gr.Button("Test Connection & Authenticate", variant="secondary")
                            authenticate_angelone_btn = gr.Button("Manual Authentication", variant="secondary")
                        
                        gr.Markdown("### Authentication Status")
                        angelone_auth_status = gr.Markdown(value="**Authentication Status:** Not authenticated")
                        
                        with gr.Accordion("AngelOne API Information", open=False):
                            gr.Markdown("""
                            **AngelOne SmartAPI Features:**
                            - ✅ Market Orders & Limit Orders
                            - ✅ Real-time market data
                            - ✅ Portfolio & positions tracking
                            - ✅ NIFTY/BANKNIFTY options trading
                            - ✅ Automated TOTP authentication
                            - ✅ Session management with token refresh
                            
                            **Symbol Format Examples:**
                            - Equity: `RELIANCE-EQ`, `SBIN-EQ`
                            - F&O: `NIFTY25AUG25F`, `NIFTY25AUG25100CE`
                            - Currency: `USDINR25AUGFUT`
                            
                            **Product Types:**
                            - INTRADAY (MIS) - Intraday trading
                            - DELIVERY (CNC) - Cash & Carry  
                            - CARRYFORWARD (NRML) - Normal (F&O)
                            """)
                        
                        angelone_status = gr.Markdown(value="**Status:** Loading...")
                    
                    # Zerodha Configuration
                    with gr.TabItem("Zerodha"):
                        gr.Markdown("""
                        ### Zerodha (Kite Connect) Configuration
                        **Setup Instructions:**
                        1. Login to your Zerodha account
                        2. Go to Kite Connect and create a new app
                        3. Get your Client ID, API Key, and Secret Key
                        4. Complete the authentication process
                        """)
                        
                        zerodha_enabled = gr.Checkbox(label="Enable Zerodha Integration", value=False)
                        zerodha_client_id = gr.Textbox(label="Client ID", placeholder="Enter your Zerodha Client ID")
                        zerodha_api_key = gr.Textbox(label="API Key", placeholder="Enter your Zerodha API Key", type="password")
                        zerodha_secret_key = gr.Textbox(label="Secret Key", placeholder="Enter your Zerodha Secret Key", type="password")
                        
                        with gr.Row():
                            save_zerodha_btn = gr.Button("Save Zerodha Settings", variant="primary")
                            test_zerodha_btn = gr.Button("Test Connection", variant="secondary")
                        
                        zerodha_status = gr.Markdown(value="**Status:** Loading...")

            with gr.TabItem("Settings"):
                selected_schedule_id_hidden = gr.Textbox(visible=False)
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### Telegram Configuration")
                        gr.Markdown("""
                        **Setup Instructions:**
                        1. Create a Telegram bot by messaging @BotFather
                        2. Get your Bot Token from @BotFather  
                        3. Get your Chat ID by messaging @userinfobot
                        4. Enter the credentials below and test the connection
                        
                        **Note:** When disabled, no Telegram notifications will be sent.
                        """)
                        telegram_enabled = gr.Checkbox(
                            label="Enable Telegram Notifications", 
                            value=True,
                            info="Master switch for all Telegram alerts"
                        )
                        telegram_bot_token = gr.Textbox(
                            label="Bot Token", 
                            value="",
                            placeholder="Enter your Telegram bot token",
                            type="password"
                        )
                        telegram_chat_id = gr.Textbox(
                            label="Chat ID", 
                            value="",
                            placeholder="Enter your Telegram chat ID"
                        )
                        with gr.Row():
                            save_telegram_button = gr.Button("Save Telegram Settings", variant="primary")
                            test_telegram_button = gr.Button("Test Connection", variant="secondary")
                        
                        telegram_status_display = gr.Markdown(value="**Status:** Loading...", visible=True)
                        
                        gr.Markdown("### P/L Summary Monitoring")
                        settings_interval = gr.Radio(['15 Mins', '30 Mins', '1 Hour', 'Disable'], label="P/L Summary Frequency", value=lambda: load_settings()['update_interval'])
                        save_monitoring_button = gr.Button("Save P/L Settings", variant="primary")
                    with gr.Column(scale=2):
                        gr.Markdown("### Auto-Generation Schedules")
                        schedules_df = gr.DataFrame(load_schedules_for_display, label="Saved Schedules", interactive=True)
                        with gr.Accordion("Add New Schedule", open=False):
                            with gr.Row():
                                new_schedule_days = gr.CheckboxGroup(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], label="Run on Days")
                                new_schedule_time = gr.Textbox(label="Run at Time (24H format, e.g., 09:20)", value="09:20")
                            with gr.Row():
                                new_schedule_index = gr.Dropdown(["NIFTY", "BANKNIFTY"], label="Index", value="NIFTY")
                                new_schedule_calc = gr.Dropdown(["Weekly", "Monthly"], label="Calculation", value="Weekly")
                        with gr.Row():
                            add_schedule_button, delete_schedule_button = gr.Button("Add Schedule", variant="primary"), gr.Button("Delete Selected Schedule", interactive=False)
                        
                        # --- Live Auto Trading Section ---
                        gr.Markdown("### 🔴 Live Auto Trading")
                        gr.Markdown("""
                        **Automated Trading Integration with Auto-Generation Schedules**
                        
                        When enabled, this feature will automatically execute trades after scheduled strategy generation.
                        
                        ⚠️ **WARNING**: Live mode places real orders with your broker. Paper mode is for testing only.
                        """)
                        
                        with gr.Row():
                            auto_trade_enabled = gr.Checkbox(
                                label="Enable Auto Trading", 
                                value=False,
                                info="Master switch for automated trading"
                            )
                            auto_trade_mode = gr.Radio(
                                ["paper", "live"], 
                                label="Trading Mode", 
                                value="live",
                                info="Paper = simulation, Live = real orders"
                            )
                        
                        with gr.Row():
                            auto_trade_high_reward = gr.Checkbox(
                                label="High Reward Strategy", 
                                value=True,
                                info="Default enabled - highest profit potential"
                            )
                            auto_trade_mid_reward = gr.Checkbox(
                                label="Mid Reward Strategy", 
                                value=False,
                                info="Medium risk/reward ratio"
                            )
                            auto_trade_low_reward = gr.Checkbox(
                                label="Low Reward Strategy", 
                                value=False,
                                info="Conservative approach"
                            )
                        
                        with gr.Row():
                            auto_trade_use_existing_targets = gr.Checkbox(
                                label="Use Generated Target/SL", 
                                value=True,
                                info="Use strategy's calculated target/stoploss (recommended)"
                            )
                            auto_trade_auto_square_off = gr.Checkbox(
                                label="Auto Square-off", 
                                value=True,
                                info="Automatically close positions when target/SL hit"
                            )
                        
                        with gr.Row():
                            auto_trade_position_multiplier = gr.Number(
                                label="Position Size Multiplier", 
                                value=1.0,
                                minimum=0.1,
                                maximum=5.0,
                                step=0.1,
                                info="Multiply default lot size (1.0 = normal size)"
                            )
                            auto_trade_max_positions = gr.Number(
                                label="Max Positions per Strategy", 
                                value=1,
                                minimum=1,
                                maximum=5,
                                step=1,
                                info="Limit concurrent positions per strategy"
                            )
                        
                        with gr.Row():
                            save_auto_trade_button = gr.Button("Save Auto Trading Settings", variant="primary")
                            auto_trade_status_button = gr.Button("View Status", variant="secondary")
                        
                        auto_trade_status_display = gr.Markdown(value="**Status:** Loading...", visible=True)
                settings_status_box = gr.Textbox(label="Status", interactive=False)

        def handle_schedule_select(schedules_df, evt: gr.SelectData):
            if evt.index is None or evt.index[0] is None: return "", gr.Button(interactive=False)
            return schedules_df.iloc[evt.index[0]]['ID'], gr.Button(interactive=True)

        run_event = run_button.click(fn=generate_analysis, inputs=[index_dropdown, calc_dropdown, expiry_dropdown, hedge_premium_slider], outputs=[output_summary_image, output_hedge_image, output_payoff_image, status_textbox_gen, analysis_data_state, summary_filepath_state, hedge_filepath_state, payoff_filepath_state])
        add_button.click(fn=add_to_analysis, inputs=[analysis_data_state], outputs=[status_textbox_gen, analysis_df]).then(fn=update_active_analysis_tab, outputs=[analysis_df, group_close_dropdown])
        telegram_button.click(fn=send_daily_chart_to_telegram, inputs=[summary_filepath_state, hedge_filepath_state, payoff_filepath_state, analysis_data_state], outputs=[status_textbox_gen])
        reset_button.click(lambda: (None, None, None, "Process stopped.", None, None, None, None), outputs=[output_summary_image, output_hedge_image, output_payoff_image, status_textbox_gen, analysis_data_state, summary_filepath_state, hedge_filepath_state, payoff_filepath_state], cancels=[run_event])

        index_dropdown.change(fn=update_expiry_dates, inputs=index_dropdown, outputs=expiry_dropdown)
        manual_instrument.change(fn=update_expiry_dates, inputs=manual_instrument, outputs=manual_expiry)

        save_monitoring_button.click(fn=update_monitoring_interval, inputs=[settings_interval], outputs=[settings_status_box])
        save_telegram_button.click(fn=update_telegram_settings, inputs=[telegram_enabled, telegram_bot_token, telegram_chat_id], outputs=[settings_status_box, telegram_status_display])
        test_telegram_button.click(fn=test_telegram_connection, outputs=[settings_status_box])
        telegram_enabled.change(fn=update_telegram_status_on_toggle, inputs=[telegram_enabled, telegram_bot_token, telegram_chat_id], outputs=[telegram_status_display])
        add_schedule_button.click(fn=add_new_schedule, inputs=[new_schedule_days, new_schedule_time, new_schedule_index, new_schedule_calc], outputs=[schedules_df, settings_status_box])
        schedules_df.select(fn=handle_schedule_select, inputs=[schedules_df], outputs=[selected_schedule_id_hidden, delete_schedule_button])
        delete_schedule_button.click(fn=delete_schedule_by_id, inputs=[selected_schedule_id_hidden], outputs=[schedules_df, settings_status_box]).then(lambda: ("", gr.Button(interactive=False)), outputs=[selected_schedule_id_hidden, delete_schedule_button])

        # Live Auto Trading Event Handlers
        save_auto_trade_button.click(
            fn=update_auto_trade_settings,
            inputs=[
                auto_trade_enabled, auto_trade_mode, auto_trade_high_reward, 
                auto_trade_mid_reward, auto_trade_low_reward, auto_trade_use_existing_targets,
                auto_trade_auto_square_off, auto_trade_position_multiplier, auto_trade_max_positions
            ],
            outputs=[settings_status_box, auto_trade_status_display]
        )
        
        auto_trade_status_button.click(
            fn=lambda: get_auto_trade_status(),
            outputs=[auto_trade_status_display]
        )
        
        # Load auto trade settings on startup
        demo.load(
            fn=lambda: (*load_auto_trade_settings_for_ui(), get_auto_trade_status()),
            outputs=[
                auto_trade_enabled, auto_trade_mode, auto_trade_high_reward,
                auto_trade_mid_reward, auto_trade_low_reward, auto_trade_use_existing_targets,
                auto_trade_auto_square_off, auto_trade_position_multiplier, 
                auto_trade_max_positions, auto_trade_status_display
            ]
        )

        # Broker Account Event Handlers
        save_flattrade_btn.click(
            fn=lambda enabled, client_id, api_key, secret_key: update_broker_settings("flattrade", enabled, client_id, api_key, secret_key),
            inputs=[flattrade_enabled, flattrade_client_id, flattrade_api_key, flattrade_secret_key],
            outputs=[settings_status_box]
        ).then(
            fn=lambda: load_broker_settings_for_ui("flattrade"),
            outputs=[flattrade_enabled, flattrade_client_id, flattrade_api_key, flattrade_secret_key, flattrade_status]
        )
        
        test_flattrade_btn.click(
            fn=lambda: test_broker_connection("flattrade"),
            outputs=[settings_status_box]
        )
        
        generate_oauth_btn.click(
            fn=lambda client_id, redirect_url, api_key: generate_flattrade_oauth_url(api_key),
            inputs=[flattrade_client_id, flattrade_redirect_url, flattrade_api_key],
            outputs=[oauth_url_display]
        )
        
        check_auth_code_btn.click(
            fn=check_flattrade_auth_code,
            outputs=[auth_code_status]
        )
        
        save_angelone_btn.click(
            fn=lambda enabled, client_id, api_key, client_pin, totp_key, redirect_url: update_angelone_settings(enabled, client_id, api_key, client_pin, totp_key, redirect_url),
            inputs=[angelone_enabled, angelone_client_id, angelone_api_key, angelone_client_pin, angelone_totp_key, angelone_redirect_url],
            outputs=[settings_status_box]
        ).then(
            fn=load_angelone_settings_for_ui,
            outputs=[angelone_enabled, angelone_client_id, angelone_api_key, angelone_client_pin, angelone_totp_key, angelone_redirect_url, angelone_status, angelone_auth_status]
        )
        
        test_angelone_btn.click(
            fn=test_angelone_connection,
            outputs=[settings_status_box]
        )
        
        generate_angelone_oauth_btn.click(
            fn=lambda client_id, api_key, redirect_url: generate_angelone_oauth_url(api_key),
            inputs=[angelone_client_id, angelone_api_key, angelone_redirect_url],
            outputs=[angelone_oauth_url_display]
        )
        
        check_angelone_callback_btn.click(
            fn=check_angelone_callback_status,
            outputs=[angelone_callback_status]
        )
        
        authenticate_angelone_btn.click(
            fn=authenticate_angelone_manual,
            outputs=[angelone_auth_status]
        )
        
        save_zerodha_btn.click(
            fn=lambda enabled, client_id, api_key, secret_key: update_broker_settings("zerodha", enabled, client_id, api_key, secret_key),
            inputs=[zerodha_enabled, zerodha_client_id, zerodha_api_key, zerodha_secret_key],
            outputs=[settings_status_box]
        ).then(
            fn=lambda: load_broker_settings_for_ui("zerodha"),
            outputs=[zerodha_enabled, zerodha_client_id, zerodha_api_key, zerodha_secret_key, zerodha_status]
        )
        
        test_zerodha_btn.click(
            fn=lambda: test_broker_connection("zerodha"),
            outputs=[settings_status_box]
        )

        demo.load(fn=update_expiry_dates, inputs=index_dropdown, outputs=expiry_dropdown)
        demo.load(fn=update_expiry_dates, inputs=manual_instrument, outputs=manual_expiry)
        demo.load(fn=load_telegram_settings_for_ui, outputs=[telegram_enabled, telegram_bot_token, telegram_chat_id, telegram_status_display])
        
        # Load broker settings on demo start
        demo.load(fn=lambda: load_broker_settings_for_ui("flattrade"), outputs=[flattrade_enabled, flattrade_client_id, flattrade_api_key, flattrade_secret_key, flattrade_status])
        demo.load(fn=load_angelone_settings_for_ui, outputs=[angelone_enabled, angelone_client_id, angelone_api_key, angelone_client_pin, angelone_totp_key, angelone_redirect_url, angelone_status, angelone_auth_status])
        demo.load(fn=lambda: load_broker_settings_for_ui("zerodha"), outputs=[zerodha_enabled, zerodha_client_id, zerodha_api_key, zerodha_secret_key, zerodha_status])
        demo.load(fn=get_broker_status_summary, outputs=[broker_status_display])

    return demo

if __name__ == "__main__":
    cleanup_temp_files()
    sync_scheduler_with_settings()
    app_settings = load_settings()
    interval_str, job_kwargs, is_paused = app_settings.get("update_interval", "15 Mins"), {}, False
    if interval_str == "Disable": is_paused, job_kwargs['minutes'] = True, 15
    else: value, unit = map(str.strip, interval_str.split()); job_kwargs['minutes' if unit == 'Mins' else 'hours'] = int(value)
    
    scheduler.add_job(send_pl_summary, 'interval', **job_kwargs, id='pl_summary')
    scheduler.add_job(check_for_ts_hits, 'interval', minutes=1, id='ts_checker')
    scheduler.add_job(lambda: send_pl_summary(is_eod_report=True), 'cron', day_of_week='mon-fri', hour=15, minute=45, id='eod_report')
    scheduler.add_job(track_pnl_history, 'interval', minutes=5, id='pnl_tracker_5min')
    scheduler.add_job(send_daily_pnl_graph_to_telegram, 'cron', day_of_week='mon-fri', hour=15, minute=50, id='eod_graph_report')
    
    scheduler.start()
    if is_paused: scheduler.pause_job('pl_summary')
    atexit.register(lambda: scheduler.shutdown())
    
    demo = build_ui()
    demo.launch(inbrowser=True, server_name="0.0.0.0", server_port=7861)