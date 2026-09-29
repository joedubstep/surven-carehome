' Surven care-home campaign: nightly stats to Notion (Campaign Stats (daily) + Campaign Events + every prospect row).
' Runs HIDDEN, launched once a night by the Surven-CareHome-Stats scheduled task. Logs to data\carehome\stats_task.log.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\dev\surven-outreach"
sh.Run "cmd /c (echo. & echo ==== %DATE% %TIME% ==== & .venv\Scripts\python.exe -m surven_outreach.carehome_stats) >> data\carehome\stats_task.log 2>&1", 0, True
