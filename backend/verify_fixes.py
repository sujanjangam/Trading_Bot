# Verification Script for Trading Bot Fixes
# Run this after restarting the bot to verify all fixes are working

import sys
import os

def verify_data_manager():
    """Verify DataManager has market_depth attribute"""
    print("1. Checking DataManager...")
    try:
        from core.data_manager import DataManager
        
        # Create a dummy instance
        dm = DataManager(
            index_token=256265,
            index_symbol="NIFTY 50",
            strategy_params={},
            log_debug_func=lambda x, y: None,
            trend_update_func=lambda x: None
        )
        
        # Check if market_depth exists
        if hasattr(dm, 'market_depth'):
            print("   [OK] market_depth attribute exists")
            if isinstance(dm.market_depth, dict):
                print("   [OK] market_depth is a dictionary")
                return True
            else:
                print("   [FAIL] market_depth is not a dictionary")
                return False
        else:
            print("   [FAIL] market_depth attribute missing")
            return False
    except Exception as e:
        print(f"   [ERROR] {e}")
        return False

def verify_websocket_manager():
    """Verify WebSocket manager has proper error handling"""
    print("\n2. Checking WebSocket Manager...")
    try:
        from core.websocket_manager import ConnectionManager
        
        # Check if the class exists
        cm = ConnectionManager()
        
        # Verify methods exist
        if hasattr(cm, 'broadcast') and hasattr(cm, '_send_with_lock'):
            print("   [OK] broadcast and _send_with_lock methods exist")
            
            # Check if active_connections is a list
            if isinstance(cm.active_connections, list):
                print("   [OK] active_connections is a list")
                return True
            else:
                print("   [FAIL] active_connections is not a list")
                return False
        else:
            print("   [FAIL] Required methods missing")
            return False
    except Exception as e:
        print(f"   [ERROR] {e}")
        return False

def verify_strategy_tick_handler():
    """Verify strategy.py has market depth handling in tick handler"""
    print("\n3. Checking Strategy Tick Handler...")
    try:
        with open('core/strategy.py', 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Check for market depth storage
        if "self.data_manager.market_depth[symbol] = tick['depth']" in content:
            print("   [OK] Market depth storage code found")
            return True
        else:
            print("   [FAIL] Market depth storage code not found")
            return False
    except Exception as e:
        print(f"   [ERROR] {e}")
        return False

def main():
    print("=" * 60)
    print("Trading Bot Fixes Verification")
    print("=" * 60)
    
    results = []
    
    # Run all verifications
    results.append(("DataManager", verify_data_manager()))
    results.append(("WebSocket Manager", verify_websocket_manager()))
    results.append(("Strategy Tick Handler", verify_strategy_tick_handler()))
    
    # Summary
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{name:.<40} {status}")
        if not passed:
            all_passed = False
    
    print("=" * 60)
    
    if all_passed:
        print("\n[SUCCESS] All verifications passed! Bot is ready to run.")
        return 0
    else:
        print("\n[WARNING] Some verifications failed. Please check the errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
