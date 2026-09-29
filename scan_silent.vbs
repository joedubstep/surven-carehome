' Surven reply-scan launcher — runs the scan HIDDEN (no console window) and logs to data\scan.log.
' Launched every 30 min by the SurvenOutreach-ReplyScan task via: wscript.exe scan_silent.vbs
' Why: the old task ran scan.bat in a visible cmd window that popped up and got closed
' mid-run (exit 0xC000013A) before the IMAP scan finished -> no reply alerts ever fired.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\dev\surven-outreach"
' window style 0 = hidden; True = wait so the task's Last Result = the scan's real exit code
sh.Run "cmd /c (echo. & echo ==== %DATE% %TIME% ==== & .venv\Scripts\python.exe -m surven_outreach.cli scan) >> data\scan.log 2>&1", 0, True
