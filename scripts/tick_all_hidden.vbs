Set Fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")

scriptDir = Fso.GetParentFolderName(WScript.ScriptFullName)
tickAllPath = Fso.BuildPath(scriptDir, "tick_all.ps1")

WshShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & tickAllPath & """", 0, True
