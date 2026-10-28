' Windows task "Surven-Meta-Followup" (every 10 minutes, 10/28): runs surven_outreach/meta_followup.py with no
' window, so Joey gets the Meta ads answer the cloud could not get, and the Day 7 emails get their Ad Library checks
' (pushed to the cloud as day7_pc.json, 10/28). Log: data\carehome\meta_followup.log
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\dev\surven-outreach"
sh.Run """C:\dev\surven-outreach\.venv\Scripts\python.exe"" -m surven_outreach.meta_followup", 0, False
