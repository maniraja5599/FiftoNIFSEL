"""
Detailed session debugging script
"""

import sys
import os

# Add current directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

def test_session_manually():
    """Test session functionality manually"""
    print("=== Session Debugging ===")
    
    # Check auth code file
    auth_code_file = os.path.join(os.path.expanduser('~'), '.fifto_analyzer_data', 'Flattrade_auth_code.txt')
    print(f"\n1. Auth code file: {auth_code_file}")
    
    if os.path.exists(auth_code_file):
        with open(auth_code_file, 'r') as f:
            auth_code = f.read().strip()
        print(f"✅ Auth code exists: {auth_code[:10]}...{auth_code[-10:]}")
        
        # Import and test Flattrade API
        from selling import FlattradeAPI, load_settings
        
        # Get credentials
        settings = load_settings()
        flattrade_config = settings.get('brokers', {}).get('flattrade', {})
        
        api_key = flattrade_config.get('api_key', '').strip()
        secret_key = flattrade_config.get('secret_key', '').strip()
        client_id = flattrade_config.get('client_id', '').strip()
        
        print(f"\n2. Credentials check:")
        print(f"   API Key: {'✅' if api_key else '❌'} ({len(api_key)} chars)")
        print(f"   Secret Key: {'✅' if secret_key else '❌'} ({len(secret_key)} chars)")
        print(f"   Client ID: {'✅' if client_id else '❌'} ({client_id})")
        
        if all([api_key, secret_key, client_id]):
            print(f"\n3. Testing token generation...")
            
            # Create API instance
            api = FlattradeAPI(api_key, secret_key, client_id)
            
            # Try to get token
            success, message = api.get_access_token(auth_code)
            print(f"   Result: {'✅' if success else '❌'} {message}")
            
            if success:
                print(f"   New token: {api.access_token[:20]}...{api.access_token[-10:] if api.access_token else 'None'}")
                
                # Test API call
                print(f"\n4. Testing API call...")
                try:
                    test_result = api.make_api_request('UserDetails', {})
                    if test_result and test_result.get('stat') == 'Ok':
                        print(f"   ✅ API call successful")
                        print(f"   User: {test_result.get('userinfo', {}).get('uname', 'Unknown')}")
                    else:
                        print(f"   ❌ API call failed: {test_result}")
                except Exception as e:
                    print(f"   ❌ API call error: {e}")
            else:
                print(f"\n   Possible issues:")
                print(f"   - Auth code might be expired (codes typically expire in 5-10 minutes)")
                print(f"   - Wrong credentials")
                print(f"   - Network issues")
                print(f"\n   Solution: Re-authenticate via OAuth:")
                print(f"   1. Run: python flattrade_oauth_server.py")
                print(f"   2. Complete OAuth flow")
                print(f"   3. Try again")
        else:
            print(f"   ❌ Missing credentials")
    else:
        print(f"❌ Auth code file not found")
        print(f"   Solution: Complete OAuth authentication first")
        print(f"   Run: python flattrade_oauth_server.py")

if __name__ == "__main__":
    test_session_manually()
