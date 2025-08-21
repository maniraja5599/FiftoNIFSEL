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
    trading_mode: TradingMode = TradingMode.PAPER
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
            
            # 2. Calculate position size
            base_lot_size = analysis_data.get('lot_size', 75)  # NIFTY default
            position_size = int(base_lot_size * self.config.position_size_multiplier)
            
            # 3. Execute hedge orders first (as per manual order reference)
            hedge_orders = self._place_hedge_orders(strategy, position_size, analysis_data)
            if not hedge_orders['success']:
                return {
                    "strategy": strategy['Entry'],
                    "status": "error",
                    "reason": f"Hedge orders failed: {hedge_orders['message']}"
                }
            
            # 4. Execute main selling orders
            main_orders = self._place_main_orders(strategy, position_size, analysis_data)
            if not main_orders['success']:
                # If main orders fail, try to close hedge positions
                self._emergency_close_hedges(hedge_orders['order_ids'])
                return {
                    "strategy": strategy['Entry'],
                    "status": "error",
                    "reason": f"Main orders failed: {main_orders['message']}"
                }
            
            # 5. Create position entry
            position = self._create_position_entry(strategy, analysis_data, schedule_info, 
                                                 hedge_orders['order_ids'], main_orders['order_ids'])
            self.active_positions.append(position)
            self.save_positions()
            
            # 6. Log successful execution
            self.log_trade_action("live_strategy_executed", {
                "strategy": strategy['Entry'],
                "position_id": position['id'],
                "hedge_orders": hedge_orders['order_ids'],
                "main_orders": main_orders['order_ids'],
                "position_size": position_size
            })
            
            return {
                "strategy": strategy['Entry'],
                "status": "success",
                "position_id": position['id'],
                "message": f"Live orders placed successfully"
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
        # Implement broker connection check
        return {"connected": False, "message": "Broker integration not implemented"}
    
    def _place_hedge_orders(self, strategy: Dict, position_size: int, analysis_data: Dict) -> Dict:
        """Place hedge orders first (buy options for protection)"""
        # Implement hedge order placement
        return {"success": False, "message": "Hedge order placement not implemented", "order_ids": []}
    
    def _place_main_orders(self, strategy: Dict, position_size: int, analysis_data: Dict) -> Dict:
        """Place main selling orders"""
        # Implement main order placement  
        return {"success": False, "message": "Main order placement not implemented", "order_ids": []}
    
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
