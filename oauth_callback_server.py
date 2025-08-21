#!/usr/bin/env python3
"""
OAuth Callback Server for FiFTO Selling v4
Handles OAuth callbacks for both Flattrade and Angel One brokers
"""

import os
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading
import time

# Data directory
DATA_DIR = os.path.join(os.path.expanduser('~'), ".fifto_analyzer_data")
os.makedirs(DATA_DIR, exist_ok=True)

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callbacks"""
    
    def do_GET(self):
        """Handle GET requests (OAuth callbacks)"""
        try:
            # Parse the URL and query parameters
            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)
            
            print(f"[RECV] Received callback: {self.path}")
            print(f"[DATA] Query parameters: {query_params}")
            
            # Handle root path with a simple status page
            if parsed_url.path == '/' or parsed_url.path == '':
                self.send_status_page()
                return
            
            # Handle favicon requests
            if parsed_url.path == '/favicon.ico':
                self.send_response(404)
                self.end_headers()
                return
            
            # Only process /callback path
            if parsed_url.path != '/callback':
                self.send_error_response(f"Invalid path: {parsed_url.path}. Expected /callback")
                return
            
            # Check if this is a Flattrade callback
            if 'code' in query_params and 'state' in query_params:
                state = query_params.get('state', [''])[0]
                
                if state == 'fifto_flattrade_auth':
                    # Flattrade OAuth callback
                    auth_code = query_params.get('code', [''])[0]
                    self.handle_flattrade_callback(auth_code, state)
                    
                elif state == 'fifto_angelone_auth':
                    # Angel One Publisher Login callback (if using state parameter)
                    auth_code = query_params.get('code', [''])[0]
                    self.handle_angelone_callback_with_code(auth_code, state)
                    
                else:
                    self.send_error_response(f"Unknown state parameter: {state}")
                    return
            
            # Check if this is an Angel One callback with auth_token
            elif 'auth_token' in query_params:
                # Angel One Publisher Login callback
                auth_token = query_params.get('auth_token', [''])[0]
                feed_token = query_params.get('feed_token', [''])[0]
                state = query_params.get('state', [''])[0]
                self.handle_angelone_callback(auth_token, feed_token, state)
                
            else:
                # Handle test callbacks or missing parameters gracefully
                if 'test' in query_params:
                    self.send_test_response()
                else:
                    self.send_error_response("No valid OAuth parameters found")
                return
                
        except Exception as e:
            print(f"[ERROR] Error handling callback: {e}")
            self.send_error_response(f"Server error: {str(e)}")
    
    def handle_flattrade_callback(self, auth_code, state):
        """Handle Flattrade OAuth callback"""
        try:
            if not auth_code:
                self.send_error_response("No authorization code received from Flattrade")
                return
            
            # Save the authorization code for the main application to process
            auth_code_file = os.path.join(DATA_DIR, 'Flattrade_auth_code.txt')
            with open(auth_code_file, 'w') as f:
                f.write(auth_code)
            
            print(f"[OK] Flattrade authorization code saved: {auth_code[:10]}...")
            
            # Send success response
            self.send_success_response(
                "Flattrade Authentication Successful!",
                f"""
                <h2>[SUCCESS] Flattrade OAuth Authentication Successful!</h2>
                <p><strong>[OK] Authorization code received and saved.</strong></p>
                <p><strong>[NEXT] Next steps:</strong></p>
                <ol>
                    <li>Return to the FiFTO application</li>
                    <li>Click "Check Authorization Code" button</li>
                    <li>Complete the authentication process</li>
                </ol>
                <p><strong>[INFO] Technical details:</strong></p>
                <ul>
                    <li>Authorization Code: {auth_code[:20]}...</li>
                    <li>State: {state}</li>
                    <li>Broker: Flattrade</li>
                </ul>
                <p><strong>[NOTE] You can close this window now.</strong></p>
                """
            )
            
        except Exception as e:
            print(f"[ERROR] Error handling Flattrade callback: {e}")
            self.send_error_response(f"Error processing Flattrade callback: {str(e)}")
    
    def handle_angelone_callback(self, auth_token, feed_token, state):
        """Handle Angel One Publisher Login callback with tokens"""
        try:
            if not auth_token:
                self.send_error_response("No auth_token received from Angel One")
                return
            
            # Save the callback data for the main application to process
            callback_data = {
                'auth_token': auth_token,
                'feed_token': feed_token,
                'state': state,
                'timestamp': time.time()
            }
            
            callback_file = os.path.join(DATA_DIR, 'Angelone_auth_callback.txt')
            with open(callback_file, 'w') as f:
                json.dump(callback_data, f)
            
            print(f"[OK] Angel One callback data saved: {auth_token[:10]}...")
            
            # Send success response
            self.send_success_response(
                "Angel One Authentication Successful!",
                f"""
                <h2>[SUCCESS] Angel One Publisher Login Successful!</h2>
                <p><strong>[OK] Authentication tokens received and saved.</strong></p>
                <p><strong>[NEXT] Next steps:</strong></p>
                <ol>
                    <li>Return to the FiFTO application</li>
                    <li>Click "Check Callback Status" button</li>
                    <li>Complete the authentication process</li>
                </ol>
                <p><strong>[INFO] Technical details:</strong></p>
                <ul>
                    <li>Auth Token: {auth_token[:20]}...</li>
                    <li>Feed Token: {feed_token[:20] if feed_token else 'Not provided'}...</li>
                    <li>State: {state}</li>
                    <li>Broker: Angel One</li>
                </ul>
                <p><strong>[NOTE] You can close this window now.</strong></p>
                """
            )
            
        except Exception as e:
            print(f"[ERROR] Error handling Angel One callback: {e}")
            self.send_error_response(f"Error processing Angel One callback: {str(e)}")
    
    def handle_angelone_callback_with_code(self, auth_code, state):
        """Handle Angel One callback with authorization code (alternative flow)"""
        try:
            if not auth_code:
                self.send_error_response("No authorization code received from Angel One")
                return
            
            # Save the authorization code for the main application to process
            callback_data = {
                'auth_code': auth_code,
                'state': state,
                'timestamp': time.time(),
                'type': 'code_flow'
            }
            
            callback_file = os.path.join(DATA_DIR, 'Angelone_auth_callback.txt')
            with open(callback_file, 'w') as f:
                json.dump(callback_data, f)
            
            print(f"[OK] Angel One authorization code saved: {auth_code[:10]}...")
            
            # Send success response
            self.send_success_response(
                "Angel One Authentication Successful!",
                f"""
                <h2>[SUCCESS] Angel One Authentication Successful!</h2>
                <p><strong>[OK] Authorization code received and saved.</strong></p>
                <p><strong>[NEXT] Next steps:</strong></p>
                <ol>
                    <li>Return to the FiFTO application</li>
                    <li>Click "Check Callback Status" button</li>
                    <li>Complete the authentication process</li>
                </ol>
                <p><strong>[INFO] Technical details:</strong></p>
                <ul>
                    <li>Authorization Code: {auth_code[:20]}...</li>
                    <li>State: {state}</li>
                    <li>Broker: Angel One</li>
                    <li>Flow: Code-based authentication</li>
                </ul>
                <p><strong>[NOTE] You can close this window now.</strong></p>
                """
            )
            
        except Exception as e:
            print(f"[ERROR] Error handling Angel One code callback: {e}")
            self.send_error_response(f"Error processing Angel One callback: {str(e)}")
    
    def send_status_page(self):
        """Send a status page for server verification"""
        self.send_success_response(
            "OAuth Server Status",
            """
            <h2>[STATUS] FiFTO OAuth Callback Server</h2>
            <p><strong>[ONLINE] Server is running and ready to handle OAuth callbacks.</strong></p>
            <p><strong>[READY] Supported authentication flows:</strong></p>
            <ul>
                <li><strong>Flattrade:</strong> OAuth 2.0 authorization code flow</li>
                <li><strong>Angel One:</strong> Publisher Login with token callbacks</li>
            </ul>
            <p><strong>[ENDPOINT] Callback endpoint:</strong> <code>/callback</code></p>
            <p><strong>[INFO] To test OAuth functionality, initiate authentication from the FiFTO application.</strong></p>
            """
        )
    
    def send_test_response(self):
        """Send a test response for server verification"""
        self.send_success_response(
            "OAuth Server Test",
            """
            <h2>[TEST] OAuth Callback Server is Working!</h2>
            <p><strong>[OK] Server is running and accepting connections.</strong></p>
            <p><strong>[INFO] This is a test response to verify the server functionality.</strong></p>
            <p><strong>[READY] The server is ready to handle OAuth callbacks from:</strong></p>
            <ul>
                <li>Flattrade OAuth 2.0 flow</li>
                <li>Angel One Publisher Login</li>
            </ul>
            <p><strong>[NOTE] You can close this window now.</strong></p>
            """
        )
    
    def send_success_response(self, title, content):
        """Send a success HTML response with auto-close functionality"""
        html_response = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>{title}</title>
            <meta charset="utf-8">
            <style>
                body {{ 
                    font-family: Arial, sans-serif; 
                    max-width: 800px; 
                    margin: 50px auto; 
                    padding: 20px;
                    background-color: #f5f5f5;
                }}
                .container {{
                    background-color: white;
                    padding: 30px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                .success {{ color: #28a745; }}
                .info {{ color: #17a2b8; }}
                h2 {{ color: #28a745; }}
                ul, ol {{ text-align: left; }}
                .auto-close {{
                    background-color: #28a745;
                    color: white;
                    padding: 15px;
                    border-radius: 5px;
                    margin: 20px 0;
                    text-align: center;
                }}
                .countdown {{
                    font-weight: bold;
                    font-size: 20px;
                }}
                .actions {{
                    text-align: center;
                    margin: 20px 0;
                }}
                .btn {{
                    padding: 10px 20px;
                    margin: 0 10px;
                    border: none;
                    border-radius: 5px;
                    cursor: pointer;
                    font-size: 14px;
                    text-decoration: none;
                    display: inline-block;
                }}
                .btn-primary {{
                    background-color: #007bff;
                    color: white;
                }}
                .btn-success {{
                    background-color: #28a745;
                    color: white;
                }}
                .footer {{ 
                    margin-top: 30px; 
                    padding-top: 20px; 
                    border-top: 1px solid #eee; 
                    color: #666; 
                    font-size: 12px; 
                }}
            </style>
            <script>
                let countdown = 10;
                function updateCountdown() {{
                    const element = document.getElementById('countdown');
                    if (element) {{
                        element.textContent = countdown;
                        if (countdown <= 0) {{
                            // Try to close the window
                            try {{
                                window.close();
                            }} catch(e) {{
                                // If close fails, try to redirect to FiFTO app
                                try {{
                                    window.location.href = 'http://localhost:7861';
                                }} catch(e2) {{
                                    window.location.href = 'about:blank';
                                }}
                            }}
                        }} else {{
                            countdown--;
                            setTimeout(updateCountdown, 1000);
                        }}
                    }}
                }}
                
                // Start countdown when page loads
                window.onload = function() {{
                    updateCountdown();
                    
                    // Add click handlers for manual actions
                    document.getElementById('closeBtn')?.addEventListener('click', function() {{
                        window.close();
                    }});
                    
                    document.getElementById('returnBtn')?.addEventListener('click', function() {{
                        window.location.href = 'http://localhost:7861';
                    }});
                }};
            </script>
        </head>
        <body>
            <div class="container">
                <div class="auto-close">
                    [AUTO-CLOSE] Window will close automatically in <span id="countdown" class="countdown">10</span> seconds
                </div>
                
                {content}
                
                <div class="actions">
                    <button id="returnBtn" class="btn btn-primary">[RETURN] Return to FiFTO App</button>
                    <button id="closeBtn" class="btn btn-success">[CLOSE] Close Window</button>
                </div>
                
                <div class="footer">
                    <p>FiFTO Selling v4 - OAuth Callback Server</p>
                    <p>Server running on: http://localhost:3001</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html_response.encode('utf-8'))
    
    def send_error_response(self, error_message):
        """Send an error HTML response"""
        html_response = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Authentication Error</title>
            <meta charset="utf-8">
            <style>
                body {{ 
                    font-family: Arial, sans-serif; 
                    max-width: 800px; 
                    margin: 50px auto; 
                    padding: 20px;
                    background-color: #f5f5f5;
                }}
                .container {{
                    background-color: white;
                    padding: 30px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                .error {{ color: #dc3545; }}
                h2 {{ color: #dc3545; }}
                .footer {{ 
                    margin-top: 30px; 
                    padding-top: 20px; 
                    border-top: 1px solid #eee; 
                    color: #666; 
                    font-size: 12px; 
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>[ERROR] Authentication Error</h2>
                <p class="error"><strong>Error:</strong> {error_message}</p>
                <p><strong>[INFO] What to do:</strong></p>
                <ol>
                    <li>Return to the FiFTO application</li>
                    <li>Try the authentication process again</li>
                    <li>Check your broker credentials</li>
                    <li>Ensure your internet connection is stable</li>
                </ol>
                <div class="footer">
                    <p>FiFTO Selling v4 - OAuth Callback Server</p>
                    <p>Server running on: http://localhost:3001</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        self.send_response(400)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html_response.encode('utf-8'))
    
    def log_message(self, format, *args):
        """Override to customize log messages"""
        print(f"[WEB] OAuth Server: {format % args}")

def start_oauth_server(port=3001):
    """Start the OAuth callback server"""
    try:
        server = HTTPServer(('localhost', port), OAuthCallbackHandler)
        print(f"[START] OAuth Callback Server started on http://localhost:{port}")
        print(f"[DATA] Data directory: {DATA_DIR}")
        print(f"[READY] Ready to handle OAuth callbacks for:")
        print(f"   - Flattrade: OAuth 2.0 authorization code flow")
        print(f"   - Angel One: Publisher Login with tokens")
        print(f"")
        print(f"[INFO] To stop the server, press Ctrl+C")
        print(f"[URL] Callback URL: http://localhost:{port}/callback")
        print(f"")
        
        server.serve_forever()
        
    except KeyboardInterrupt:
        print(f"\n[STOP] OAuth server stopped by user")
        server.shutdown()
        
    except Exception as e:
        print(f"[ERROR] Error starting OAuth server: {e}")
        print(f"[INFO] Make sure port {port} is not already in use")

def start_oauth_server_threaded(port=3001):
    """Start OAuth server in a background thread"""
    def run_server():
        start_oauth_server(port)
    
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    return server_thread

if __name__ == "__main__":
    print("[START] FiFTO OAuth Callback Server")
    print("="*50)
    start_oauth_server()
