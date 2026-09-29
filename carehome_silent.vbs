' Surven care-home cold email campaign runner (Sept 2026). Runs HIDDEN and logs to data\carehome\task.log.
' Launched every 30 min by the Surven-CareHome-Campaign scheduled task. Each run: reply/bounce scan,
' then sends whatever is due inside each prospect's 7-10 AM local window. Safe to run any number of times.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\dev\surven-outreach"
sh.Run "cmd /c (echo. & echo ==== %DATE% %TIME% ==== & .venv\Scripts\python.exe -m surven_outreach.carehome run) >> data\carehome\task.log 2>&1", 0, True
