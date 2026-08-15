Set WshShell = CreateObject("WScript.Shell")
Set Fso = CreateObject("Scripting.FileSystemObject")
BaseDir = Fso.GetParentFolderName(WScript.ScriptFullName)
Command = "pythonw.exe """ & BaseDir & "\run_app.py"""
WshShell.Run Command, 0, False
