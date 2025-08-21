from flask import Flask, render_template, jsonify
import requests
import time
from datetime import datetime
import json
import os
import pytz
from collections import defaultdict
import webbrowser
from threading import Timer

# --- Basic Flask App Setup ---
app = Flask(__name__)

# --- Configuration & Data ---
DATA_DIR = os.path.join(os.path.expanduser('~'), ".fifto_analyzer_data")
TRADES_DB_FILE = os.path.join(DATA_DIR, "active_trades.json")

# In-memory data stores
overall_pnl_history = []
pnl_history_by_group = defaultdict(list)

# --- Helper Functions ---
def load_trades():
    if not os.path.exists(TRADES_DB_FILE): return []
    try:
        with open(TRADES_DB_FILE, 'r') as f: return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError): return []

def get_option_chain_data(symbol, retries=3, delay=2):
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
            if i < retries - 1: time.sleep(delay)
    return None

def get_current_ist_time():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

# --- Core Data Processing Logic ---
def get_dashboard_data():
    active_trades = [t for t in load_trades() if t.get('status') == 'Running']
    
    live_data = {
        "groups": defaultdict(lambda: {
            'pnl': 0.0,
            'high_reward_pnl': 0.0,
            'trade_count': 0,
            'pnl_by_reward': {'high': 0.0, 'mid': 0.0, 'low': 0.0, 'manual': 0.0},
            'trades_info': [],
            'is_manual': False
        }),
        "total_pnl": 0.0
    }

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
            live_data['total_pnl'] += pnl
            
            group_key = f"{trade.get('entry_tag', 'Un-tagged')} - {trade.get('instrument')}"
            reward_type = trade.get('reward_type', 'Manual')

            live_data['groups'][group_key]['pnl'] += pnl
            live_data['groups'][group_key]['trade_count'] += 1

            live_data['groups'][group_key]['trades_info'].append({
                'type': reward_type,
                'ce_strike': trade.get('ce_strike'),
                'pe_strike': trade.get('pe_strike')
            })

            if reward_type == 'High Reward':
                live_data['groups'][group_key]['pnl_by_reward']['high'] += pnl
                live_data['groups'][group_key]['high_reward_pnl'] += pnl
            elif reward_type == 'Mid Reward':
                live_data['groups'][group_key]['pnl_by_reward']['mid'] += pnl
            elif reward_type == 'Low Reward':
                live_data['groups'][group_key]['pnl_by_reward']['low'] += pnl
            elif reward_type == 'Manual':
                live_data['groups'][group_key]['is_manual'] = True
                live_data['groups'][group_key]['pnl_by_reward']['manual'] += pnl
    
    return live_data

# --- Web Routes ---
@app.route('/')
def index():
    # This now requires a templates folder with index.html inside it
    return render_template('index.html')

@app.route('/api/dashboard_data')
def api_dashboard_data():
    global overall_pnl_history, pnl_history_by_group
    data = get_dashboard_data()
    current_time = get_current_ist_time()
    time_str = current_time.strftime('%H:%M')

    if overall_pnl_history and overall_pnl_history[0]['time'].split(':')[0] != time_str.split(':')[0]:
        overall_pnl_history = []
        pnl_history_by_group = defaultdict(list)
        
    overall_pnl_history.append({'time': time_str, 'pnl': data['total_pnl']})
    
    for group_name, group_data in data['groups'].items():
        pnl_history_by_group[group_name].append({
            'time': time_str,
            'high': group_data['pnl_by_reward']['high'],
            'mid': group_data['pnl_by_reward']['mid'],
            'low': group_data['pnl_by_reward']['low'],
            'manual': group_data['pnl_by_reward']['manual']
        })
    
    trade_cards = []
    for name, group in data['groups'].items():
        display_pnl = group['pnl'] if group['is_manual'] else group['high_reward_pnl']
        
        trade_cards.append({
            'name': name,
            'pnl': display_pnl,
            'trade_count': group['trade_count'],
            'running_trades': group['trades_info'],
            'is_manual': group['is_manual']
        })

    return jsonify({
        'overall_pnl_history': overall_pnl_history,
        'pnl_history_by_group': pnl_history_by_group,
        'trade_cards': trade_cards,
        'last_updated': current_time.strftime('%d-%b-%Y %I:%M:%S %p')
    })

# --- Main Execution ---
def open_browser():
    webbrowser.open_new_tab('http://127.0.0.1:5000/')

if __name__ == '__main__':
    Timer(1, open_browser).start()
    app.run(debug=False, port=5000)