Option Explicit

' Refresh the IconLocation of ComfyDir.lnk so Windows picks up the
' regenerated assets\app.ico immediately (without waiting for the
' Explorer icon cache to expire).
'
' Run from project root:
'   cscript //nologo tools\refresh_shortcut_icon.vbs
'
' This script intentionally has NO MsgBox calls so it runs silently
' from cscript and does not require user interaction.

Dim shell, fso, projDir, lnkPath, iconPath, lnk
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

projDir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
lnkPath  = projDir & "\ComfyDir.lnk"
iconPath = projDir & "\assets\app.ico"

If Not fso.FileExists(lnkPath) Then
  WScript.StdErr.WriteLine "shortcut not found: " & lnkPath
  WScript.Quit 1
End If

If Not fso.FileExists(iconPath) Then
  WScript.StdErr.WriteLine "icon not found: " & iconPath
  WScript.Quit 1
End If

Set lnk = shell.CreateShortcut(lnkPath)
WScript.Echo "old IconLocation: " & lnk.IconLocation
lnk.IconLocation = iconPath & ", 0"
lnk.Save
WScript.Echo "new IconLocation: " & lnk.IconLocation
WScript.Echo "OK: " & lnkPath
