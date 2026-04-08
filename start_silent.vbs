Set WShell = CreateObject("WScript.Shell")
' Change to project directory first
WShell.CurrentDirectory = "e:\Projects\NIFTY Claude Setup"
' Run Python in background (hidden window)
WShell.Run """e:\Projects\NIFTY Claude Setup\.venv\Scripts\pythonw.exe"" ""e:\Projects\NIFTY Claude Setup\dev_server.py""", 0, False
