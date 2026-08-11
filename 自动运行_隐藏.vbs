Set fso = CreateObject("Scripting.FileSystemObject")
Set ws = CreateObject("Wscript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
ws.Run """" & base & "\每日自动运行.cmd""", 0, False