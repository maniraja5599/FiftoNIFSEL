"""
Live Automated Trading Module for FiFTO Selling Strategy
Integrates with existing Auto-Generation Schedules and adds live trading capabilities
"""

import json
import os
import time
import threading
from datetime import datetime, timedelta
import requests
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from enum import Enum

class TradingMode(Enum):
    PAPER = "paper"  # Without live - simulation only
    LIVE = "live"    # With live broker execution

class AutoTradeStatus(Enum):
    DISABLED = "disabled"
    ENABLED = "enabled"
    PAUSED = "paused"

@dataclass
class AutoTradeConfig:
    """Configuration for automated trading"""
    enabled: bool = False
    trading_mode: TradingMode = TradingMode.LIVE  # Changed to LIVE mode for real trading
    preferred_broker: str = 'flattrade'  # 'flattrade' or 'angelone'
    strategies: Optional[Dict[str, bool]] = None  # {"High Reward": True, "Mid Reward": False, "Low Reward": False}
    use_existing_targets: bool = True  # Use generated target/SL, don't create separate
    auto_square_off: bool = True  # Auto close positions when target/SL hit
    position_size_multiplier: float = 1.0  # Multiply lot size
    max_positions_per_strategy: int = 1  # Limit concurrent positions
    
    def __post_init__(self):
        if self.strategies is None:
            # Default: Only High Reward enabled
            self.strategies = {
                "High Reward": True,
                "Mid Reward": False, 
                "Low Reward": False
            }

class LiveTradeManager:
    """Manages live automated trading"""
    
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.config_file = os.path.join(data_dir, "auto_trade_config.json")
        self.positions_file = os.path.join(data_dir, "auto_positions.json")
        self.trade_log_file = os.path.join(data_dir, "auto_trade_log.json")
        
        self.config = self.load_config()
        self.active_positions = self.load_positions()
        self.trade_monitor_thread = None
        self.stop_monitoring = False
        
    def load_config(self) -> AutoTradeConfig:
        """Load automation configuration"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    data = json.load(f)
                
                # Handle enum conversion for trading_mode
                if 'trading_mode' in data and isinstance(data['trading_mode'], str):
                    data['trading_mode'] = TradingMode(data['trading_mode'])
                
                return AutoTradeConfig(**data)
            except Exception as e:
                print(f"Error loading auto-trade config: {e}")
        
        return AutoTradeConfig()
    
    def save_config(self):
        """Save automation configuration"""
        config_dict = {
            'enabled': self.config.enabled,
            'trading_mode': self.config.trading_mode.value,
            'strategies': self.config.strategies,
            'use_existing_targets': self.config.use_existing_targets,
            'auto_square_off': self.config.auto_square_off,
            'position_size_multiplier': self.config.position_size_multiplier,
            'max_positions_per_strategy': self.config.max_positions_per_strategy
        }
        
        with open(self.config_file, 'w') as f:
            json.dump(config_dict, f, indent=4)
    
    def load_positions(self) -> List[Dict]:
        """Load active automated positions"""
        if os.path.exists(self.positions_file):
            try:
                with open(self.positions_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading auto positions: {e}")
        return []
    
    def save_positions(self):
        """Save active automated positions"""
        with open(self.positions_file, 'w') as f:
            json.dump(self.active_positions, f, indent=4)
    
    def log_trade_action(self, action: str, details: Dict):
        """Log trading actions"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'details': details
        }
        
        # Load existing logs
        logs = []
        if os.path.exists(self.trade_log_file):
            try:
                with open(self.trade_log_file, 'r') as f:
                    logs = json.load(f)
            except:
                logs = []
        
        logs.append(log_entry)
        
        # Keep only last 1000 entries
        if len(logs) > 1000:
            logs = logs[-1000:]
        
        with open(self.trade_log_file, 'w') as f:
            json.dump(logs, f, indent=4)
    
    def execute_automated_strategy(self, analysis_data: Dict, schedule_info: Dict):
        """
        Execute automated trading based on generated strategy analysis
        This is called after Auto-Generation Schedules creates strategies
        """
        if not self.config.enabled:
            return {"status": "disabled", "message": "Automated trading is disabled"}
        
        try:
            # Get enabled strategies only
            strategies_config = self.config.strategies or {}
            enabled_strategies = [
                strategy for strategy in analysis_data.get('df_data', [])
                if strategies_config.get(strategy['Entry'], False)
            ]
            
            if not enabled_strategies:
                return {"status": "no_strategies", "message": "No strategies enabled for automation"}
            
            results = []
            for strategy in enabled_strategies:
                # Check position limits
                current_positions = len([
                    p for p in self.active_positions 
                    if p['strategy_type'] == strategy['Entry'] and p['status'] == 'active'
                ])
                
                if current_positions >= self.config.max_positions_per_strategy:
                    results.append({
                        "strategy": strategy['Entry'],
                        "status": "skipped",
                        "reason": f"Max positions limit ({self.config.max_positions_per_strategy}) reached"
                    })
                    continue
                
                # Execute strategy
                if self.config.trading_mode == TradingMode.LIVE:
                    result = self._execute_live_strategy(strategy, analysis_data, schedule_info)
                else:
                    result = self._execute_paper_strategy(strategy, analysis_data, schedule_info)
                
                results.append(result)
                
            return {"status": "success", "results": results}
            
        except Exception as e:
            error_msg = f"Error in automated strategy execution: {str(e)}"
            self.log_trade_action("error", {"error": error_msg})
            return {"status": "error", "message": error_msg}
    
    def _execute_live_strategy(self, strategy: Dict, analysis_data: Dict, schedule_info: Dict) -> Dict:
        """Execute strategy with live broker orders"""
        try:
            # 1. Check broker connection and authentication
            broker_status = self._check_broker_connection()
            if not broker_status['connected']:
                return {
                    "strategy": strategy['Entry'],
                    "status": "error", 
                    "reason": f"Broker not connected: {broker_status['message']}"
                }
            
            # 2. Calculate position size (number of lots)
            # Use 1 lot as default, multiplied by position_size_multiplier 
            num_lots = int(1 * self.config.position_size_multiplier)
            
            # 3. FAST PARALLEL ORDER PLACEMENT - Execute all orders simultaneously
            all_orders_result = self._place_all_orders_parallel(strategy, num_lots, analysis_data)
            if not all_orders_result['success']:
                return {
                    "strategy": strategy['Entry'],
                    "status": "error",
                    "reason": f"Order placement failed: {all_orders_result['message']}"
                }
            
            # 5. Create position entry
            position = self._create_position_entry(strategy, analysis_data, schedule_info, 
                                                 all_orders_result['order_ids'], [])
            self.active_positions.append(position)
            self.save_positions()
            
            # 6. Log successful execution
            self.log_trade_action("live_strategy_executed", {
                "strategy": strategy['Entry'],
                "position_id": position['id'],
                "all_orders": all_orders_result['order_ids'],
                "successful_orders": all_orders_result.get('successful_orders', []),
                "failed_orders": all_orders_result.get('failed_orders', []),
                "position_size": num_lots
            })
            
            return {
                "strategy": strategy['Entry'],
                "status": "success",
                "position_id": position['id'],
                "message": f"Live orders placed successfully (Parallel execution)"
            }
            
        except Exception as e:
            return {
                "strategy": strategy['Entry'],
                "status": "error",
                "reason": f"Live execution error: {str(e)}"
            }
    
    def _execute_paper_strategy(self, strategy: Dict, analysis_data: Dict, schedule_info: Dict) -> Dict:
        """Execute strategy in paper trading mode"""
        try:
            # Create simulated position entry
            position = self._create_position_entry(strategy, analysis_data, schedule_info, 
                                                 paper_mode=True)
            self.active_positions.append(position)
            self.save_positions()
            
            # Log paper execution
            self.log_trade_action("paper_strategy_executed", {
                "strategy": strategy['Entry'],
                "position_id": position['id'],
                "mode": "paper_trading"
            })
            
            return {
                "strategy": strategy['Entry'],
                "status": "success",
                "position_id": position['id'],
                "message": f"Paper trade position created"
            }
            
        except Exception as e:
            return {
                "strategy": strategy['Entry'],
                "status": "error",
                "reason": f"Paper execution error: {str(e)}"
            }
    
    def _create_position_entry(self, strategy: Dict, analysis_data: Dict, schedule_info: Dict, 
                             hedge_order_ids: Optional[List[str]] = None, 
                             main_order_ids: Optional[List[str]] = None, 
                             paper_mode: bool = False) -> Dict:
        """Create position entry for tracking"""
        
        position_id = f"AUTO_{strategy['Entry'].replace(' ', '')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Use existing target/SL from strategy (as per requirement)
        target_amount = strategy.get('Target', 0) if self.config.use_existing_targets else 0
        stoploss_amount = strategy.get('Stoploss', 0) if self.config.use_existing_targets else 0
        
        position = {
            'id': position_id,
            'created_at': datetime.now().isoformat(),
            'strategy_type': strategy['Entry'],
            'instrument': analysis_data.get('instrument', 'NIFTY'),
            'expiry': analysis_data.get('expiry'),
            'status': 'active',
            'trading_mode': self.config.trading_mode.value,
            
            # Strategy details
            'ce_strike': strategy['CE Strike'],
            'pe_strike': strategy['PE Strike'],
            'ce_hedge_strike': strategy['CE Hedge Strike'],
            'pe_hedge_strike': strategy['PE Hedge Strike'],
            'initial_net_premium': strategy['Net Premium'],
            
            # Target/SL from existing strategy
            'target_amount': target_amount,
            'stoploss_amount': stoploss_amount,
            
            # Order tracking
            'hedge_order_ids': hedge_order_ids or [],
            'main_order_ids': main_order_ids or [],
            'is_paper_trade': paper_mode,
            
            # Schedule info
            'schedule_id': schedule_info.get('id'),
            'auto_generated': True,
            
            # Position monitoring
            'last_pnl': 0.0,
            'max_profit': 0.0,
            'max_drawdown': 0.0
        }
        
        return position
    
    def start_position_monitoring(self):
        """Start monitoring active positions for auto square-off"""
        if self.trade_monitor_thread and self.trade_monitor_thread.is_alive():
            return
        
        self.stop_monitoring = False
        self.trade_monitor_thread = threading.Thread(target=self._monitor_positions_loop)
        self.trade_monitor_thread.daemon = True
        self.trade_monitor_thread.start()
        
        print("Position monitoring started")
    
    def stop_position_monitoring(self):
        """Stop position monitoring"""
        self.stop_monitoring = True
        if self.trade_monitor_thread:
            self.trade_monitor_thread.join(timeout=5)
        print("Position monitoring stopped")
    
    def _monitor_positions_loop(self):
        """Main loop for monitoring positions"""
        while not self.stop_monitoring:
            try:
                if self.config.enabled and self.config.auto_square_off:
                    self._check_auto_square_off()
                time.sleep(30)  # Check every 30 seconds
            except Exception as e:
                print(f"Error in position monitoring: {e}")
                time.sleep(60)  # Wait longer on error
    
    def _check_auto_square_off(self):
        """Check if any positions need to be squared off"""
        for position in self.active_positions:
            if position['status'] != 'active':
                continue
                
            try:
                # Calculate current P/L
                current_pnl = self._calculate_position_pnl(position)
                position['last_pnl'] = current_pnl
                
                # Update max profit/drawdown
                position['max_profit'] = max(position.get('max_profit', 0), current_pnl)
                if current_pnl < 0:
                    position['max_drawdown'] = min(position.get('max_drawdown', 0), current_pnl)
                
                # Check target hit
                if (position['target_amount'] > 0 and 
                    current_pnl >= position['target_amount']):
                    
                    self._square_off_position(position, 'target_hit', current_pnl)
                    
                # Check stoploss hit  
                elif (position['stoploss_amount'] > 0 and 
                      current_pnl <= -position['stoploss_amount']):
                    
                    self._square_off_position(position, 'stoploss_hit', current_pnl)
                    
            except Exception as e:
                print(f"Error checking position {position['id']}: {e}")
        
        # Save updated positions
        self.save_positions()
    
    def _square_off_position(self, position: Dict, reason: str, final_pnl: float):
        """Square off a position"""
        try:
            if position['trading_mode'] == TradingMode.LIVE.value:
                # Close live position
                close_result = self._close_live_position(position)
                if not close_result['success']:
                    print(f"Failed to close live position {position['id']}: {close_result['message']}")
                    return
            
            # Update position status
            position['status'] = 'closed'
            position['close_reason'] = reason
            position['final_pnl'] = final_pnl
            position['closed_at'] = datetime.now().isoformat()
            
            # Log square-off
            self.log_trade_action("position_squared_off", {
                "position_id": position['id'],
                "strategy": position['strategy_type'],
                "reason": reason,
                "final_pnl": final_pnl,
                "target": position['target_amount'],
                "stoploss": position['stoploss_amount']
            })
            
            print(f"Position {position['id']} squared off: {reason}, P/L: ₹{final_pnl:.2f}")
            
        except Exception as e:
            print(f"Error squaring off position {position['id']}: {e}")
    
    def _calculate_position_pnl(self, position: Dict) -> float:
        """Calculate current P/L for a position"""
        # This should integrate with your existing P/L calculation logic
        # For now, return a placeholder
        return 0.0
    
    def _check_broker_connection(self) -> Dict:
        """Check if broker is connected and authenticated"""
        try:
            # Import the broker connection check functions from selling.py
            import sys
            import os
            
            # Add current directory to path to import from selling.py
            current_dir = os.path.dirname(os.path.abspath(__file__))
            if current_dir not in sys.path:
                sys.path.insert(0, current_dir)
            
            # Import selling module functions
            try:
                from selling import flattrade_api, angelone_api, initialize_flattrade_for_trading, initialize_angelone_for_trading
                
                # Check preferred broker first
                if self.config.preferred_broker == 'flattrade':
                    # Check Flattrade connection
                    if flattrade_api and flattrade_api.access_token:
                        # Test the session with a simple API call
                        try:
                            test_result = flattrade_api.make_api_request('UserDetails', {})
                            if test_result and test_result.get('stat') == 'Ok':
                                return {"connected": True, "broker": "flattrade", "message": "Flattrade connected"}
                            else:
                                # Session might be expired, try to reinitialize
                                success, message = initialize_flattrade_for_trading()
                                if success:
                                    return {"connected": True, "broker": "flattrade", "message": "Flattrade session refreshed"}
                                else:
                                    return {"connected": False, "message": f"Flattrade session expired: {message}"}
                        except Exception as e:
                            return {"connected": False, "message": f"Flattrade session test failed: {e}"}
                    else:
                        # Try to initialize
                        success, message = initialize_flattrade_for_trading()
                        if success:
                            return {"connected": True, "broker": "flattrade", "message": "Flattrade initialized"}
                        else:
                            return {"connected": False, "message": f"Flattrade: {message}"}
                
                elif self.config.preferred_broker == 'angelone':
                    # Check Angel One connection
                    if angelone_api and angelone_api.access_token:
                        return {"connected": True, "broker": "angelone", "message": "Angel One connected"}
                    else:
                        # Try to initialize
                        success, message = initialize_angelone_for_trading()
                        if success:
                            return {"connected": True, "broker": "angelone", "message": "Angel One initialized"}
                        else:
                            return {"connected": False, "message": f"Angel One: {message}"}
                
                # If preferred broker fails, try the other one
                if flattrade_api and flattrade_api.access_token:
                    return {"connected": True, "broker": "flattrade", "message": "Flattrade connected (fallback)"}
                elif angelone_api and angelone_api.access_token:
                    return {"connected": True, "broker": "angelone", "message": "Angel One connected (fallback)"}
                
                return {"connected": False, "message": "No broker connected. Please authenticate first."}
                
            except ImportError as e:
                return {"connected": False, "message": f"Failed to import broker modules: {e}"}
                
        except Exception as e:
            return {"connected": False, "message": f"Broker connection check failed: {e}"}
    
    def _place_order_with_retry(self, api, broker, symbol, quantity, price, order_type, product, transaction_type, exchange):
        """Place order with session expiry retry logic"""
        try:
            # First attempt
            if broker == 'flattrade':
                result = getattr(api, 'place_order')(
                    symbol, quantity, price, order_type, product, transaction_type, exchange
                )
            else:  # angelone
                result = {"stat": "Not_Ok", "emsg": "Angel One requires symbol token lookup (not implemented yet)"}
            
            # Check if session expired
            if result and isinstance(result, dict):
                error_msg = result.get('emsg', '')
                if 'Session Expired' in error_msg or 'Invalid Session Key' in error_msg:
                    print(f"🔄 Session expired, attempting to refresh...")
                    
                    # Try to refresh session
                    if broker == 'flattrade':
                        # Clear the current expired token first
                        api.access_token = None
                        
                        # Try to get a fresh token using auth code
                        auth_code_file = os.path.join(os.path.expanduser('~'), '.fifto_analyzer_data', 'Flattrade_auth_code.txt')
                        if os.path.exists(auth_code_file):
                            with open(auth_code_file, 'r') as f:
                                auth_code = f.read().strip()
                            
                            # Get new access token
                            success, message = api.get_access_token(auth_code)
                            if success:
                                print(f"✅ Session refreshed successfully")
                                
                                # Save the new token to settings
                                try:
                                    from selling import load_settings, save_settings
                                    settings = load_settings()
                                    if 'brokers' not in settings:
                                        settings['brokers'] = {}
                                    if 'flattrade' not in settings['brokers']:
                                        settings['brokers']['flattrade'] = {}
                                    
                                    settings['brokers']['flattrade']['access_token'] = api.access_token
                                    save_settings(settings)
                                    print(f"✅ New token saved to settings")
                                except Exception as save_error:
                                    print(f"⚠️ Warning: Could not save new token: {save_error}")
                                
                                # Retry the order with refreshed session
                                result = getattr(api, 'place_order')(
                                    symbol, quantity, price, order_type, product, transaction_type, exchange
                                )
                            else:
                                # Auth code expired or invalid
                                print(f"❌ Session refresh failed: Auth code expired or invalid")
                                return {"stat": "Not_Ok", "emsg": "Session expired. Auth code has expired (typically 5-10 minutes). Please re-authenticate: 1) Go to Broker Settings, 2) Click 'Start OAuth Authentication', 3) Complete login process."}
                        else:
                            print(f"❌ No auth code file found for session refresh")
                            return {"stat": "Not_Ok", "emsg": "Session expired and no auth code available. Please re-authenticate: 1) Go to Broker Settings, 2) Click 'Start OAuth Authentication', 3) Complete login process."}
                    else:  # angelone
                        from selling import initialize_angelone_for_trading
                        success, message = initialize_angelone_for_trading()
                        if success:
                            print(f"✅ Session refreshed successfully")
                            # Retry logic for angelone would go here
                            result = {"stat": "Not_Ok", "emsg": "Angel One retry not implemented yet"}
                        else:
                            print(f"❌ Session refresh failed: {message}")
                            return {"stat": "Not_Ok", "emsg": f"Session refresh failed: {message}"}
            
            return result
            
        except Exception as e:
            return {"stat": "Not_Ok", "emsg": f"Order placement error: {str(e)}"}
    
    def _place_all_orders_parallel(self, strategy: Dict, num_lots: int, analysis_data: Dict) -> Dict:
        """Place all orders in parallel for fastest execution"""
        import threading
        import time
        
        try:
            # Get broker info
            broker_status = self._check_broker_connection()
            if not broker_status['connected']:
                return {"success": False, "message": broker_status['message'], "order_ids": []}
            
            broker = broker_status['broker']
            
            # Import correct API
            if broker == 'flattrade':
                from selling import flattrade_api
                api = flattrade_api
            else:
                from selling import angelone_api
                api = angelone_api
            
            if not api:
                return {"success": False, "message": f"{broker} API not available", "order_ids": []}
            
            # Extract order data
            ce_hedge_strike = strategy.get('CE Hedge Strike')
            pe_hedge_strike = strategy.get('PE Hedge Strike')
            ce_strike = strategy.get('CE Strike')
            pe_strike = strategy.get('PE Strike')
            instrument = analysis_data.get('instrument', 'NIFTY')
            expiry = analysis_data.get('expiry', '')
            
            # Calculate quantities
            lot_size = 75 if instrument == 'NIFTY' else 35
            total_quantity = lot_size * num_lots
            
            # Format expiry
            expiry_formatted = self._format_expiry_for_symbol(expiry)
            print(f"🔍 DEBUG: Parallel orders expiry formatting - Input: {expiry}, Output: {expiry_formatted}")
            
            # Prepare all orders
            orders_to_place = []
            
            # Hedge orders (Buy for protection)
            if ce_hedge_strike and ce_hedge_strike != 'None':
                ce_hedge_symbol = f"{instrument}{expiry_formatted}C{int(ce_hedge_strike)}"
                orders_to_place.append({
                    'type': 'CE_HEDGE',
                    'symbol': ce_hedge_symbol,
                    'transaction': 'B',
                    'quantity': total_quantity
                })
            
            if pe_hedge_strike and pe_hedge_strike != 'None':
                pe_hedge_symbol = f"{instrument}{expiry_formatted}P{int(pe_hedge_strike)}"
                orders_to_place.append({
                    'type': 'PE_HEDGE',
                    'symbol': pe_hedge_symbol,
                    'transaction': 'B',
                    'quantity': total_quantity
                })
            
            # Main selling orders
            if ce_strike and ce_strike != 'None':
                ce_sell_symbol = f"{instrument}{expiry_formatted}C{int(ce_strike)}"
                orders_to_place.append({
                    'type': 'CE_SELL',
                    'symbol': ce_sell_symbol,
                    'transaction': 'S',
                    'quantity': total_quantity
                })
            
            if pe_strike and pe_strike != 'None':
                pe_sell_symbol = f"{instrument}{expiry_formatted}P{int(pe_strike)}"
                orders_to_place.append({
                    'type': 'PE_SELL',
                    'symbol': pe_sell_symbol,
                    'transaction': 'S',
                    'quantity': total_quantity
                })
            
            if not orders_to_place:
                return {"success": False, "message": "No orders to place", "order_ids": []}
            
            # Execute all orders in parallel
            results = []
            threads = []
            
            def place_single_order(order_info, result_list, index):
                """Thread function to place a single order"""
                try:
                    print(f"🚀 FAST: Placing {order_info['type']} order for {order_info['symbol']}")
                    result = self._place_order_with_retry(
                        api, broker,
                        order_info['symbol'],
                        order_info['quantity'],
                        None,  # Market order
                        'MKT',
                        'NRML',
                        order_info['transaction'],
                        'NFO'
                    )
                    result_list[index] = {
                        'type': order_info['type'],
                        'symbol': order_info['symbol'],
                        'result': result,
                        'success': result and result.get('stat') == 'Ok'
                    }
                except Exception as e:
                    result_list[index] = {
                        'type': order_info['type'],
                        'symbol': order_info['symbol'],
                        'result': {"stat": "Not_Ok", "emsg": str(e)},
                        'success': False
                    }
            
            # Initialize results array
            results = [None] * len(orders_to_place)
            
            # Create and start threads for parallel execution
            for i, order_info in enumerate(orders_to_place):
                thread = threading.Thread(target=place_single_order, args=(order_info, results, i))
                threads.append(thread)
                thread.start()
            
            # Wait for all threads to complete
            for thread in threads:
                thread.join()
            
            # Process results
            successful_orders = []
            failed_orders = []
            order_ids = []
            
            for result in results:
                if result is None:
                    failed_orders.append("Unknown: Thread failed to complete")
                    continue
                    
                if result['success']:
                    order_id = result['result'].get('norenordno') or result['result'].get('orderid')
                    if order_id:
                        order_ids.append(f"{result['type']}_{order_id}")
                        successful_orders.append(result['type'])
                        print(f"✅ FAST: {result['type']} order placed: {result['symbol']}")
                    else:
                        failed_orders.append(f"{result['type']}: No order ID returned")
                else:
                    error_msg = result['result'].get('emsg', 'Unknown error')
                    failed_orders.append(f"{result['type']}: {error_msg}")
                    print(f"❌ FAST: {result['type']} failed: {error_msg}")
            
            # Return results
            if successful_orders:
                message = f"Parallel orders: {len(successful_orders)} successful"
                if failed_orders:
                    message += f", {len(failed_orders)} failed"
                
                return {
                    "success": True,
                    "message": message,
                    "order_ids": order_ids,
                    "successful_orders": successful_orders,
                    "failed_orders": failed_orders
                }
            else:
                return {
                    "success": False,
                    "message": f"All parallel orders failed: {', '.join(failed_orders)}",
                    "order_ids": []
                }
                
        except Exception as e:
            return {"success": False, "message": f"Parallel order placement failed: {str(e)}", "order_ids": []}
    
    def _format_expiry_for_symbol(self, expiry: str) -> str:
        """Helper function to format expiry date for symbol generation"""
        if not expiry:
            return ''
        
        try:
            from datetime import datetime
            
            # Try multiple date formats
            date_formats = ['%d-%b-%Y', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y']
            
            for date_format in date_formats:
                try:
                    date_obj = datetime.strptime(expiry, date_format)
                    return date_obj.strftime('%d%b%y').upper()
                except ValueError:
                    continue
            
            # Manual parsing fallback
            expiry_str = str(expiry).upper()
            if 'AUG' in expiry_str and '2025' in expiry_str:
                day = expiry_str[:2] if expiry_str[:2].isdigit() else expiry_str[0]
                return f"{day}AUG25"
            
            return expiry_str.replace('-', '').replace('/', '')[:7]
            
        except Exception:
            return str(expiry).replace('-', '').replace('/', '')[:7]
    
    def _place_hedge_orders(self, strategy: Dict, num_lots: int, analysis_data: Dict) -> Dict:
        """Place hedge orders first (buy options for protection)"""
        try:
            # Import broker APIs
            from selling import flattrade_api, angelone_api
            
            # Get the connected broker and API instance
            broker_status = self._check_broker_connection()
            if not broker_status['connected']:
                return {"success": False, "message": broker_status['message'], "order_ids": []}
            
            broker = broker_status['broker']
            
            # Import and get correct API instance
            if broker == 'flattrade':
                from selling import flattrade_api
                api = flattrade_api
            else:  # angelone
                from selling import angelone_api
                api = angelone_api
            
            if not api:
                return {"success": False, "message": f"{broker} API not available", "order_ids": []}
            
            order_ids = []
            errors = []
            
            # Extract strategy data
            ce_hedge_strike = strategy.get('CE Hedge Strike')
            pe_hedge_strike = strategy.get('PE Hedge Strike')
            instrument = analysis_data.get('instrument', 'NIFTY')
            expiry = analysis_data.get('expiry', '')
            
            # Get lot size (NIFTY=75, BANKNIFTY=35)
            lot_size = 75 if instrument == 'NIFTY' else 35
            total_quantity = lot_size * num_lots
            
            # Format expiry for symbol creation
            if expiry:
                try:
                    # Convert date format if needed
                    from datetime import datetime
                    
                    # Try multiple date formats
                    expiry_formatted = None
                    date_formats = ['%d-%b-%Y', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y']
                    
                    for date_format in date_formats:
                        try:
                            date_obj = datetime.strptime(expiry, date_format)
                            # Format for Flattrade option symbol (DDMMMYY)
                            # Example: 21-Aug-2025 -> 21AUG25
                            expiry_formatted = date_obj.strftime('%d%b%y').upper()
                            break
                        except ValueError:
                            continue
                    
                    if not expiry_formatted:
                        raise ValueError(f"Could not parse date: {expiry}")
                        
                except Exception as e:
                    print(f"⚠️ DEBUG: Expiry format error: {e}, using manual parsing")
                    # Manual parsing for common formats
                    expiry_str = str(expiry).upper()
                    if 'AUG' in expiry_str and '2025' in expiry_str:
                        # Extract day from start
                        day = expiry_str[:2] if expiry_str[:2].isdigit() else expiry_str[0]
                        expiry_formatted = f"{day}AUG25"
                    elif 'AUG' in expiry_str and '25' in expiry_str:
                        # Already in correct format
                        expiry_formatted = expiry_str.replace('-', '')
                    else:
                        # Last resort - use as is, cleaned
                        expiry_formatted = expiry_str.replace('-', '').replace('/', '')[:7]
            else:
                expiry_formatted = ''
            
            print(f"🔍 DEBUG: Expiry formatting - Input: {expiry}, Output: {expiry_formatted}")
            
            # Place CE Hedge (Buy Call for protection) if exists
            if ce_hedge_strike and ce_hedge_strike != 'None':
                try:
                    # Flattrade format: NIFTY21AUG25C24500
                    ce_symbol = f"{instrument}{expiry_formatted}C{int(ce_hedge_strike)}"
                    
                    print(f"🔍 DEBUG: Placing CE hedge order for {ce_symbol} with {total_quantity} quantity ({num_lots} lots)")
                    result = self._place_order_with_retry(
                        api, broker,
                        ce_symbol,           # symbol
                        total_quantity,      # quantity 
                        None,                # price (None for market order)
                        'MKT',               # order_type
                        'NRML',              # product
                        'B',                 # transaction_type (B for Buy)
                        'NFO'                # exchange
                    )
                    print(f"🔍 DEBUG: CE hedge order result: {result}")
                    
                    if result and result.get('stat') == 'Ok':
                        order_id = result.get('norenordno') or result.get('orderid')
                        if order_id:
                            order_ids.append(f"CE_HEDGE_{order_id}")
                            print(f"✅ CE Hedge order placed: {ce_symbol} x {total_quantity}")
                        else:
                            errors.append(f"CE Hedge: Order placed but no order ID returned")
                    else:
                        error_msg = result.get('emsg', 'Unknown error') if result else 'No response'
                        errors.append(f"CE Hedge failed: {error_msg}")
                        
                except Exception as e:
                    errors.append(f"CE Hedge error: {str(e)}")
            
            # Place PE Hedge (Buy Put for protection) if exists  
            if pe_hedge_strike and pe_hedge_strike != 'None':
                try:
                    # Flattrade format: NIFTY21AUG25P24500
                    pe_symbol = f"{instrument}{expiry_formatted}P{int(pe_hedge_strike)}"
                    
                    print(f"🔍 DEBUG: Placing PE hedge order for {pe_symbol} with {total_quantity} quantity ({num_lots} lots)")
                    result = self._place_order_with_retry(
                        api, broker,
                        pe_symbol,           # symbol
                        total_quantity,      # quantity 
                        None,                # price (None for market order)
                        'MKT',               # order_type
                        'NRML',              # product
                        'B',                 # transaction_type (B for Buy)
                        'NFO'                # exchange
                    )
                    print(f"🔍 DEBUG: PE hedge order result: {result}")
                    
                    if result and result.get('stat') == 'Ok':
                        order_id = result.get('norenordno') or result.get('orderid')
                        if order_id:
                            order_ids.append(f"PE_HEDGE_{order_id}")
                            print(f"✅ PE Hedge order placed: {pe_symbol} x {total_quantity}")
                        else:
                            errors.append(f"PE Hedge: Order placed but no order ID returned")
                    else:
                        error_msg = result.get('emsg', 'Unknown error') if result else 'No response'
                        errors.append(f"PE Hedge failed: {error_msg}")
                        
                except Exception as e:
                    errors.append(f"PE Hedge error: {str(e)}")
            
            # Return results
            if order_ids:
                message = f"Hedge orders placed: {len(order_ids)} orders"
                if errors:
                    message += f" (with {len(errors)} errors: {', '.join(errors)})"
                return {"success": True, "message": message, "order_ids": order_ids}
            else:
                error_msg = f"All hedge orders failed: {', '.join(errors)}" if errors else "No hedge orders to place"
                return {"success": False, "message": error_msg, "order_ids": []}
                
        except Exception as e:
            return {"success": False, "message": f"Hedge order placement failed: {str(e)}", "order_ids": []}
    
    def _place_main_orders(self, strategy: Dict, num_lots: int, analysis_data: Dict) -> Dict:
        """Place main selling orders (sell options to collect premium)"""
        try:
            # Get the connected broker and API instance
            broker_status = self._check_broker_connection()
            if not broker_status['connected']:
                return {"success": False, "message": broker_status['message'], "order_ids": []}
            
            broker = broker_status['broker']
            
            # Import and get correct API instance
            if broker == 'flattrade':
                from selling import flattrade_api
                api = flattrade_api
            else:  # angelone
                from selling import angelone_api
                api = angelone_api
            
            if not api:
                return {"success": False, "message": f"{broker} API not available", "order_ids": []}
            
            order_ids = []
            errors = []
            
            # Extract strategy data
            ce_strike = strategy.get('CE Strike')
            pe_strike = strategy.get('PE Strike')
            instrument = analysis_data.get('instrument', 'NIFTY')
            expiry = analysis_data.get('expiry', '')
            
            # Get lot size (NIFTY=75, BANKNIFTY=35)
            lot_size = 75 if instrument == 'NIFTY' else 35
            total_quantity = lot_size * num_lots
            
            # Format expiry for symbol creation
            if expiry:
                try:
                    # Convert date format if needed
                    from datetime import datetime
                    
                    # Try multiple date formats
                    expiry_formatted = None
                    date_formats = ['%d-%b-%Y', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y']
                    
                    for date_format in date_formats:
                        try:
                            date_obj = datetime.strptime(expiry, date_format)
                            # Format for Flattrade option symbol (DDMMMYY)
                            # Example: 21-Aug-2025 -> 21AUG25
                            expiry_formatted = date_obj.strftime('%d%b%y').upper()
                            break
                        except ValueError:
                            continue
                    
                    if not expiry_formatted:
                        raise ValueError(f"Could not parse date: {expiry}")
                        
                except Exception as e:
                    print(f"⚠️ DEBUG: Main orders expiry format error: {e}, using manual parsing")
                    # Manual parsing for common formats
                    expiry_str = str(expiry).upper()
                    if 'AUG' in expiry_str and '2025' in expiry_str:
                        # Extract day from start
                        day = expiry_str[:2] if expiry_str[:2].isdigit() else expiry_str[0]
                        expiry_formatted = f"{day}AUG25"
                    elif 'AUG' in expiry_str and '25' in expiry_str:
                        # Already in correct format
                        expiry_formatted = expiry_str.replace('-', '')
                    else:
                        # Last resort - use as is, cleaned
                        expiry_formatted = expiry_str.replace('-', '').replace('/', '')[:7]
            else:
                expiry_formatted = ''
            
            print(f"🔍 DEBUG: Main orders expiry formatting - Input: {expiry}, Output: {expiry_formatted}")
            
            # Place CE Sell Order (Sell Call to collect premium) if exists
            if ce_strike and ce_strike != 'None':
                try:
                    # Flattrade format: NIFTY21AUG25C24500
                    ce_symbol = f"{instrument}{expiry_formatted}C{int(ce_strike)}"
                    
                    result = self._place_order_with_retry(
                        api, broker,
                        ce_symbol,           # symbol
                        total_quantity,      # quantity 
                        None,                # price (None for market order)
                        'MKT',               # order_type
                        'NRML',              # product
                        'S',                 # transaction_type (S for Sell)
                        'NFO'                # exchange
                    )
                    
                    if result and result.get('stat') == 'Ok':
                        order_id = result.get('norenordno') or result.get('orderid')
                        if order_id:
                            order_ids.append(f"CE_SELL_{order_id}")
                            print(f"✅ CE Sell order placed: {ce_symbol} x {total_quantity}")
                        else:
                            errors.append(f"CE Sell: Order placed but no order ID returned")
                    else:
                        error_msg = result.get('emsg', 'Unknown error') if result else 'No response'
                        errors.append(f"CE Sell failed: {error_msg}")
                        
                except Exception as e:
                    errors.append(f"CE Sell error: {str(e)}")
            
            # Place PE Sell Order (Sell Put to collect premium) if exists  
            if pe_strike and pe_strike != 'None':
                try:
                    # Flattrade format: NIFTY21AUG25P24500
                    pe_symbol = f"{instrument}{expiry_formatted}P{int(pe_strike)}"
                    
                    result = self._place_order_with_retry(
                        api, broker,
                        pe_symbol,           # symbol
                        total_quantity,      # quantity 
                        None,                # price (None for market order)
                        'MKT',               # order_type
                        'NRML',              # product
                        'S',                 # transaction_type (S for Sell)
                        'NFO'                # exchange
                    )
                    
                    if result and result.get('stat') == 'Ok':
                        order_id = result.get('norenordno') or result.get('orderid')
                        if order_id:
                            order_ids.append(f"PE_SELL_{order_id}")
                            print(f"✅ PE Sell order placed: {pe_symbol} x {total_quantity}")
                        else:
                            errors.append(f"PE Sell: Order placed but no order ID returned")
                    else:
                        error_msg = result.get('emsg', 'Unknown error') if result else 'No response'
                        errors.append(f"PE Sell failed: {error_msg}")
                        
                except Exception as e:
                    errors.append(f"PE Sell error: {str(e)}")
            
            # Return results
            if order_ids:
                message = f"Main orders placed: {len(order_ids)} orders"
                if errors:
                    message += f" (with {len(errors)} errors: {', '.join(errors)})"
                return {"success": True, "message": message, "order_ids": order_ids}
            else:
                error_msg = f"All main orders failed: {', '.join(errors)}" if errors else "No main orders to place"
                return {"success": False, "message": error_msg, "order_ids": []}
                
        except Exception as e:
            return {"success": False, "message": f"Main order placement failed: {str(e)}", "order_ids": []}
    
    def _emergency_close_hedges(self, hedge_order_ids: List[str]):
        """Emergency close hedge positions if main orders fail"""
        # Implement emergency hedge closure
        pass
    
    def _close_live_position(self, position: Dict) -> Dict:
        """Close a live trading position"""
        # Implement position closure
        return {"success": False, "message": "Position closure not implemented"}
    
    def get_automation_status(self) -> Dict:
        """Get current automation status and statistics"""
        active_positions = [p for p in self.active_positions if p['status'] == 'active']
        closed_positions = [p for p in self.active_positions if p['status'] == 'closed']
        
        total_pnl = sum(p.get('final_pnl', p.get('last_pnl', 0)) for p in self.active_positions)
        
        return {
            'enabled': self.config.enabled,
            'trading_mode': self.config.trading_mode.value,
            'active_strategies': self.config.strategies,
            'active_positions_count': len(active_positions),
            'total_positions': len(self.active_positions),
            'closed_positions_count': len(closed_positions),
            'total_pnl': total_pnl,
            'auto_square_off_enabled': self.config.auto_square_off,
            'use_existing_targets': self.config.use_existing_targets
        }

# Global instance
live_trade_manager = None

def initialize_live_trading(data_dir: str):
    """Initialize the live trading manager"""
    global live_trade_manager
    live_trade_manager = LiveTradeManager(data_dir)
    return live_trade_manager

def get_live_trade_manager() -> Optional[LiveTradeManager]:
    """Get the global live trading manager instance"""
    return live_trade_manager
