@echo off
REM Checks survengeo for genuine replies and fires phone+desktop alerts. Run by the scheduled task.
cd /d C:\dev\surven-outreach
.venv\Scripts\python.exe -m surven_outreach.cli scan
