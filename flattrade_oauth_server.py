"""
OAuth Handler for Flattrade Authentication - Enhanced Version with Auto-Redirect
This script provides a flexible web server to handle OAuth callbacks from Flattrade.
"""

import json
import os
import webbrowser
import threading
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
import threading
import time
import threading
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
import threading
import time

class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Handle ANY request path - maximum flexibility for Flattrade callbacks
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        print(f">> Received request: {self.path}")
        print(f">> Query parameters: {query_params}")
        
        # Check for authorization code in ANY of these parameter names
        auth_code = None
        possible_code_params = ['code', 'request_code', 'authorization_code', 'auth_code', 'authcode']
        
        for param_name in possible_code_params:
            if param_name in query_params:
                auth_code = query_params[param_name][0]
                print(f"[SUCCESS] Found authorization code in parameter: {param_name}")
                break
        
        # Also check for any parameter that looks like an auth code (long string)
        if not auth_code:
            for param_name, param_values in query_params.items():
                if param_values and len(param_values[0]) > 20:  # Auth codes are typically long
                    auth_code = param_values[0]
                    print(f"[SUCCESS] Found potential auth code in parameter: {param_name}")
                    break
        
        if auth_code:
            print(f"[SUCCESS] Authorization code received: {auth_code}")
            
            # Save the authorization code to a temporary file
            temp_file = os.path.join(os.path.expanduser('~'), '.fifto_analyzer_data', 'Flattrade_auth_code.txt')
            os.makedirs(os.path.dirname(temp_file), exist_ok=True)
            with open(temp_file, 'w') as f:
                f.write(auth_code)
            
            # Also create a completion flag file to signal the main app
            completion_file = os.path.join(os.path.expanduser('~'), '.fifto_analyzer_data', 'oauth_completed.flag')
            with open(completion_file, 'w') as f:
                f.write(str(time.time()))  # Write timestamp
            
            print(f"[SAVED] Authorization code saved to: {temp_file}")
            print(f"[FLAG] OAuth completion flag created: {completion_file}")
            
            # Send success response with auto-redirect to main app
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            success_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>[SUCCESS] FiFTO - Flattrade Authentication Complete!</title>
                <style>
                    body {{ 
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                        text-align: center; 
                        padding: 50px; 
                        background: linear-gradient(135deg, #10b981 0%, #059669 100%);
                        color: white;
                        margin: 0;
                        min-height: 100vh;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                    }}
                    .container {{ 
                        max-width: 700px; 
                        margin: 0 auto; 
                        background: rgba(255, 255, 255, 0.98); 
                        padding: 50px; 
                        border-radius: 25px; 
                        box-shadow: 0 25px 50px rgba(0,0,0,0.2);
                        color: #1f2937;
                        animation: slideIn 0.6s ease-out;
                    }}
                    @keyframes slideIn {{
                        from {{ opacity: 0; transform: translateY(-30px) scale(0.95); }}
                        to {{ opacity: 1; transform: translateY(0) scale(1); }}
                    }}
                    .success {{ 
                        color: #10b981; 
                        font-size: 32px; 
                        margin-bottom: 20px; 
                        animation: pulse 2s infinite;
                        font-weight: bold;
                        background: #f0fdf4;
                        padding: 15px;
                        border-radius: 10px;
                        border: 2px solid #10b981;
                    }}
                    @keyframes pulse {{
                        0%, 100% {{ transform: scale(1); }}
                        50% {{ transform: scale(1.1); }}
                    }}
                    .title {{
                        color: #1f2937;
                        font-size: 32px;
                        margin-bottom: 25px;
                        font-weight: 700;
                    }}
                    .subtitle {{
                        color: #10b981;
                        font-size: 20px;
                        margin-bottom: 30px;
                        font-weight: 600;
                    }}
                    .code {{ 
                        background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%); 
                        padding: 25px; 
                        border-radius: 15px; 
                        font-family: 'Courier New', monospace; 
                        margin: 25px 0; 
                        border-left: 5px solid #10b981;
                        font-size: 14px;
                        word-break: break-all;
                        font-weight: 600;
                        color: #374151;
                    }}
                    .highlight {{
                        background: linear-gradient(135deg, #10b981 0%, #059669 100%);
                        color: white;
                        padding: 20px;
                        border-radius: 15px;
                        margin: 25px 0;
                        font-weight: 700;
                        font-size: 18px;
                    }}
                    .countdown {{
                        background: #fef3c7;
                        border: 2px solid #f59e0b;
                        color: #92400e;
                        padding: 15px;
                        border-radius: 10px;
                        margin: 20px 0;
                        font-weight: 600;
                        font-size: 16px;
                    }}
                    .button {{ 
                        background: linear-gradient(135deg, #10b981 0%, #059669 100%); 
                        color: white; 
                        padding: 18px 35px; 
                        border: none; 
                        border-radius: 50px; 
                        text-decoration: none; 
                        display: inline-block; 
                        margin: 20px 10px; 
                        font-size: 16px;
                        font-weight: 700;
                        cursor: pointer;
                        transition: all 0.3s ease;
                        box-shadow: 0 8px 25px rgba(16, 185, 129, 0.3);
                    }}
                    .button:hover {{
                        transform: translateY(-3px);
                        box-shadow: 0 12px 35px rgba(16, 185, 129, 0.4);
                    }}
                    .redirect-info {{
                        background: #dbeafe;
                        border: 2px solid #3b82f6;
                        color: #1e40af;
                        padding: 15px;
                        border-radius: 10px;
                        margin: 20px 0;
                        font-weight: 600;
                        font-size: 16px;
                    }}
                </style>
                <script>
                    let countdown = 5;
                    
                    function updateCountdown() {{
                        const countdownElement = document.getElementById('countdown');
                        const redirectElement = document.getElementById('redirect-info');
                        
                        if (countdownElement) {{
                            countdownElement.innerHTML = `[REDIRECT] Redirecting to FiFTO app in <strong>${{countdown}}</strong> seconds...`;
                        }}
                        
                        if (redirectElement) {{
                            redirectElement.innerHTML = `[REDIRECT] Auto-redirecting to FiFTO app in ${{countdown}} seconds. Click "Return to App" to go immediately.`;
                        }}
                        
                        countdown--;
                        
                        if (countdown < 0) {{
                            redirectToApp();
                        }}
                    }}
                    
                    window.onload = function() {{
                        setInterval(updateCountdown, 1000);
                        updateCountdown();
                    }};
                    
                    function closeWindow() {{
                        window.close();
                    }}
                    
                    function redirectToApp() {{
                        try {{
                            // Try to redirect to the main Gradio app
                            window.location.href = 'http://localhost:7860';
                        }} catch(e) {{
                            // If redirect fails, just close the window
                            window.close();
                        }}
                    }}
                </script>
            </head>
            <body>
                <div class="container">
                    <div class="success">[SUCCESS]</div>
                    <div class="title">Flattrade OAuth Complete!</div>
                    <div class="subtitle">[SUCCESS] Authentication Successful</div>
                    
                    <div class="highlight">
                        [ROCKET] Your FiFTO application is now connected to Flattrade!
                    </div>
                    
                    <div class="code">
                        <strong>[KEY] Authorization Code:</strong><br>
                        {auth_code}
                    </div>
                    
                    <div class="redirect-info" id="redirect-info">
                        [REDIRECT] Auto-redirecting to FiFTO app in 5 seconds. Click "Return to App" to go immediately.
                    </div>
                    
                    <div class="countdown" id="countdown">
                        [REDIRECT] Redirecting to FiFTO app in <strong>5</strong> seconds...
                    </div>
                    
                    <button onclick="redirectToApp()" class="button">[HOME] Return to FiFTO App</button>
                    <button onclick="closeWindow()" class="button">[CLOSE] Close Window</button>
                </div>
            </body>
            </html>
            """
            
            self.wfile.write(success_html.encode())
            
            # Enhanced server shutdown - giving enough time for UI display
            print("[SUCCESS] OAuth authentication completed successfully!")
            print("[SAVED] Authorization code saved securely")
            print("[REDIRECT] Redirecting user back to main FiFTO application")
            print("[SHUTDOWN] Scheduling server shutdown in 10 seconds...")
            threading.Timer(10.0, lambda: os._exit(0)).start()
            
        else:
            # Enhanced error handling with debug information
            print("[ERROR] No authorization code found in callback")
            print(f"[DEBUG] Available parameters: {list(query_params.keys())}")
            print(f"[DEBUG] Full callback path: {self.path}")
            print(f"[DEBUG] All query data: {query_params}")
            
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            # Debug page showing what was received
            error_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>[DEBUG] FiFTO - OAuth Debug Information</title>
                <style>
                    body {{ 
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                        text-align: center; 
                        padding: 50px; 
                        background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
                        color: white;
                        margin: 0;
                    }}
                    .container {{ 
                        max-width: 800px; 
                        margin: 0 auto; 
                        background: rgba(255, 255, 255, 0.95); 
                        padding: 40px; 
                        border-radius: 20px; 
                        box-shadow: 0 20px 40px rgba(0,0,0,0.1);
                        color: #333;
                        text-align: left;
                    }}
                    .error {{ 
                        color: #ef4444; 
                        font-size: 32px; 
                        margin-bottom: 20px; 
                        text-align: center;
                        font-weight: bold;
                        background: #fef2f2;
                        padding: 15px;
                        border-radius: 10px;
                        border: 2px solid #ef4444;
                    }}
                    .title {{
                        color: #1f2937;
                        font-size: 24px;
                        margin-bottom: 20px;
                        font-weight: 600;
                        text-align: center;
                    }}
                    .debug {{
                        background: #f8fafc;
                        padding: 20px;
                        border-radius: 10px;
                        font-family: 'Courier New', monospace;
                        margin: 20px 0;
                        border-left: 4px solid #ef4444;
                        font-size: 14px;
                        word-break: break-all;
                    }}
                    .info {{
                        color: #6b7280;
                        font-size: 16px;
                        line-height: 1.6;
                        margin: 20px 0;
                    }}
                    .button {{ 
                        background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); 
                        color: white; 
                        padding: 15px 30px; 
                        border: none; 
                        border-radius: 50px; 
                        text-decoration: none; 
                        display: inline-block; 
                        margin: 20px 10px; 
                        font-size: 16px;
                        font-weight: 700;
                        cursor: pointer;
                        transition: all 0.3s ease;
                        text-align: center;
                    }}
                </style>
                <script>
                    function redirectToApp() {{
                        window.location.href = 'http://localhost:7860';
                    }}
                </script>
            </head>
            <body>
                <div class="container">
                    <div class="error">[DEBUG]</div>
                    <div class="title">OAuth Debug Information</div>
                    
                    <div class="info">
                        <strong>Issue:</strong> No authorization code found in the callback from Flattrade.
                    </div>
                    
                    <div class="debug">
                        <strong>Callback URL:</strong> {self.path}<br>
                        <strong>Available Parameters:</strong> {list(query_params.keys())}<br>
                        <strong>All Data:</strong> {query_params}
                    </div>
                    
                    <div class="info">
                        <strong>This helps diagnose the OAuth redirect issue.</strong><br>
                        The server received the callback but couldn't find an authorization code.<br>
                        Check your Flattrade app registration and redirect URI configuration.
                    </div>
                    
                    <div style="text-align: center;">
                        <button onclick="redirectToApp()" class="button">[HOME] Return to FiFTO App</button>
                    </div>
                </div>
            </body>
            </html>
            """
            
            self.wfile.write(error_html.encode())
    
    def log_message(self, format, *args):
        # Custom logging with timestamps
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {format % args}")

def start_oauth_server(port=3001):
    """Start the OAuth callback server"""
    server = HTTPServer(('localhost', port), OAuthHandler)
    print(f"[Enhanced OAuth] callback server started on http://localhost:{port}")
    print(">> Waiting for Flattrade authentication callback...")
    print(">> Server handles ANY callback path with auth parameters")
    print(">> Maximum compatibility with Flattrade OAuth")
    print(">> Auto-redirects to FiFTO app after successful authentication")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[STOP] Server stopped by user.")
    finally:
        server.server_close()

if __name__ == "__main__":
    print("=" * 80)
    print("FiFTO Enhanced Flattrade OAuth Authentication Server with Auto-Redirect")
    print("=" * 80)
    print()
    print("This server handles OAuth callbacks from Flattrade with maximum flexibility.")
    print("It works with ANY redirect URI path and parameter format.")
    print("After successful authentication, it automatically redirects back to the FiFTO app.")
    print()
    
    start_oauth_server()
