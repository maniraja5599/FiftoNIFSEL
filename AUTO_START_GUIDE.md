# FIFTO Auto-Start Setup Guide

## Quick Setup (One-Time)

The server is configured to run automatically in the background when you start your computer.

### Step 1: Create Scheduled Task

**Option A: Using the batch file**
1. Right-click `setup_auto_start.bat`
2. Select **"Run as administrator"**
3. Click **Yes** on the UAC prompt
4. Wait for success message

**Option B: Using PowerShell (Admin)**
1. Press `Win + X` → Select **Terminal (Admin)** or **PowerShell (admin)**
2. Run:
```powershell
schtasks /create /tn "FIFTO AI Trading Server" /tr "wscript.exe `"e:\Projects\NIFTY Claude Setup\start_silent.vbs`"" /sc onlogon /rl limited /f
```

**Option C: Using Task Scheduler GUI**
1. Press `Win + R`, type `taskschd.msc`, press Enter
2. Click **Action** → **Create Basic Task**
3. Name: `FIFTO AI Trading Server`
4. Trigger: **When I log on**
5. Action: **Start a program**
6. Program: `wscript.exe`
7. Arguments: `"e:\Projects\NIFTY Claude Setup\start_silent.vbs"`
8. Click **Finish**

### Step 2: Verify Setup

1. Open Task Scheduler (`taskschd.msc`)
2. Find **FIFTO AI Trading Server** in the task list
3. Check it shows **Ready** status
4. Right-click → **Properties** to review settings

### Step 3: Test It

**Option A: Manual test run**
```cmd
wscript.exe "e:\Projects\NIFTY Claude Setup\start_silent.vbs"
```
Then open http://localhost:8080 in your browser

**Option B: Restart your computer**
- Log in normally
- Wait ~30 seconds
- Open http://localhost:8080

---

## Managing Auto-Start

### Disable Auto-Start
- Right-click `disable_auto_start.bat` → **Run as administrator**
- OR: Task Scheduler → Find task → Right-click → **Disable**

### Re-Enable Auto-Start
- Right-click `enable_auto_start.bat` → **Run as administrator**
- OR: Task Scheduler → Find task → Right-click → **Enable**

### Delete Auto-Start Completely
- Task Scheduler → Find task → Right-click → **Delete**

---

## How It Works

1. **Windows logs in** → Triggers scheduled task
2. **Task runs** → `wscript.exe start_silent.vbs`
3. **VBS script** → Launches `pythonw.exe dev_server.py` (hidden window)
4. **Server starts** → Dashboard available at http://localhost:8080
5. **Agent auto-connects** → Connects to AngelOne and starts monitoring

The server runs **completely hidden** in the background - no visible window.

---

## Troubleshooting

### Dashboard won't load
- Check if process is running: Task Manager → Details → `pythonw.exe`
- If not running, manually start: Double-click `start_fifto.bat`
- Check logs in `logs/` folder for errors

### Task won't create (Access denied)
- Must run as Administrator
- Disable your antivirus temporarily if it blocks task creation

### Server starts but can't connect to AngelOne
- Verify `.env` file exists with correct credentials
- Check internet connection
- Review logs in `logs/` folder

### Want to see the server output
- Stop the background process (Task Manager → End `pythonw.exe`)
- Run manually: Double-click `start_fifto.bat`

---

## Files Created

- `start_silent.vbs` - Silent startup script (fixed path)
- `setup_auto_start.bat` - One-time setup wizard
- `enable_auto_start.bat` - Re-enable auto-start
- `disable_auto_start.bat` - Disable auto-start

---

## Notes

- Server runs on port **8080** by default
- Agent is configured to **auto-start** in AUTO mode
- Paper trading mode can be enabled in `.env`
- Telegram notifications work if credentials are set
