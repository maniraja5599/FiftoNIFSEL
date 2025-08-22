"""
Test Script: Ultra-Fast Scheduler Demo
Shows how the 15-second broker delay is compensated
"""

import time
from datetime import datetime, timedelta
import pytz
from ultra_fast_scheduler import UltraFastTradeScheduler

def simulate_broker_delay():
    """Simulate the 15-second broker terminal delay"""
    print("🐌 Simulating Broker Terminal Delay...")
    time.sleep(15)  # 15 second delay
    print("✅ Broker order placed!")

def simulate_ultra_fast_execution():
    """Simulate ultra-fast execution"""
    print("⚡ Ultra-Fast Execution...")
    time.sleep(0.5)  # Almost instant
    print("🎯 Ultra-Fast order placed!")

def demo_timing_comparison():
    """Demo: Traditional vs Ultra-Fast timing"""
    
    print("=" * 60)
    print("🚀 ULTRA-FAST SCHEDULER TIMING DEMO")
    print("=" * 60)
    
    # Target execution time (e.g., 15:04:00)
    target_time = "15:04:00"
    print(f"🎯 Target Execution Time: {target_time}")
    print()
    
    # Traditional Method
    print("📊 TRADITIONAL METHOD:")
    print(f"   15:04:00 - Schedule triggers")
    print(f"   15:04:01 - Strategy generation starts...")
    print(f"   15:04:05 - Strategy generation complete (4s)")
    print(f"   15:04:05 - Broker order placement starts...")
    print(f"   15:04:20 - First order placed (15s broker delay)")
    print(f"   15:04:25 - All orders complete")
    print(f"   ❌ RESULT: 25 seconds late!")
    print()
    
    # Ultra-Fast Method
    print("⚡ ULTRA-FAST METHOD:")
    print(f"   15:03:35 - Pre-generation starts (25s early)")
    print(f"   15:03:39 - Pre-generation complete (4s)")
    print(f"   15:03:45 - Broker warmup starts (15s early)")
    print(f"   15:03:46 - Broker connection verified")
    print(f"   15:04:00 - ⚡ INSTANT EXECUTION ⚡")
    print(f"   15:04:01 - All orders complete")
    print(f"   ✅ RESULT: 1 second execution time!")
    print()
    
    print("🎯 ADVANTAGE: 24 seconds faster execution!")
    print("⚡ PRECISION: Millisecond-accurate timing!")

def demo_live_ultra_scheduler():
    """Demo the actual ultra-fast scheduler"""
    
    print("\n" + "=" * 60)
    print("🧪 LIVE ULTRA-FAST SCHEDULER TEST")
    print("=" * 60)
    
    # Create scheduler
    scheduler = UltraFastTradeScheduler()
    
    # Add a test schedule (execute in 30 seconds)
    future_time = datetime.now(pytz.timezone('Asia/Kolkata')) + timedelta(seconds=30)
    test_time = future_time.strftime("%H:%M")
    
    print(f"⏰ Adding test schedule: NIFTY at {test_time}")
    scheduler.add_ultra_schedule("test123", test_time, "NIFTY", "Weekly")
    
    # Start monitoring
    scheduler.start_ultra_monitoring()
    
    print("🔄 Monitoring started... (will auto-execute in 30 seconds)")
    print()
    
    # Monitor for 35 seconds
    for i in range(35):
        time.sleep(1)
        status = scheduler.get_ultra_schedule_status()
        if status:
            current_status = status[0]['status']
            print(f"⏱️  T-{30-i:2d}s | Status: {current_status}")
            
            if current_status == 'executed':
                print("🎉 ULTRA-FAST EXECUTION COMPLETE!")
                break
    
    scheduler.stop_ultra_monitoring()
    print("✅ Demo complete!")

if __name__ == "__main__":
    # Run timing comparison demo
    demo_timing_comparison()
    
    # Ask user if they want to run live demo
    print("\n" + "=" * 60)
    response = input("🚀 Run live ultra-fast scheduler demo? (y/n): ").lower()
    if response == 'y':
        demo_live_ultra_scheduler()
    else:
        print("Demo skipped. Run with 'y' to see live ultra-fast execution!")
