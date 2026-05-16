Option Explicit

' ComfyDir one-click launcher (no console window).
'
' Double-click this file to start launcher.py with pythonw.exe in hidden mode.
' launcher.py starts uvicorn in a background thread and shows a tray icon.
' Clicking the tray icon opens the PWA window in Edge/Chrome (--app=...).
' Logs go to data\server.log .
'
' NOTE: This file must be saved as ASCII (no Japanese characters), because
' VBScript reads source as ANSI / cp932 and gets confused by UTF-8 bytes.

Dim shell, fso, scriptDir, pyw, launcher, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir

pyw = scriptDir & "\.venv\Scripts\pythonw.exe"
launcher = scriptDir & "\launcher.py"

If Not fso.FileExists(pyw) Then
  MsgBox ".venv\Scripts\pythonw.exe not found." & vbCrLf & vbCrLf & _
         "Run setup first in cmd:" & vbCrLf & _
         "  python -m venv .venv" & vbCrLf & _
         "  .venv\Scripts\python.exe -m pip install -r requirements.txt", _
         vbCritical, "ComfyDir"
  WScript.Quit 1
End If

If Not fso.FileExists(launcher) Then
  MsgBox "launcher.py not found: " & launcher, vbCritical, "ComfyDir"
  WScript.Quit 1
End If

' Launch pythonw.exe hidden (window_style=0, wait=False).
cmd = """" & pyw & """ """ & launcher & """"
shell.Run cmd, 0, False
