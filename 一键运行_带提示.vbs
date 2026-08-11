Set fso = CreateObject("Scripting.FileSystemObject")
Set ws = CreateObject("Wscript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
ws.Run """" & base & "\每日自动运行.cmd""", 0, True
ws.Popup "选股系统运行完成！" & vbCrLf & "报告已更新，详见 自动运行日志.txt", 8, "选股系统", 64