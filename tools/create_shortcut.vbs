Option Explicit

' Create a Windows shortcut (.lnk) for ComfyDir.
'
' The shortcut launches start.vbs via wscript.exe (no console window),
' uses assets\app.ico as its icon, and is placed in the project root.
'
' Run this once:
'   - double-click this file, OR
'   - cscript //nologo tools\create_shortcut.vbs
'
' Then right-click the generated "ComfyDir.lnk" -> "Pin to taskbar".
'
' NOTE: Save this file as ASCII (no Japanese characters), because VBScript
' source is read as ANSI and gets confused by UTF-8 bytes.

Dim shell, fso, projDir, lnkPath, iconPath, startVbs, lnk
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' tools/ is one level below the project root.
projDir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))

startVbs = projDir & "\start.vbs"
iconPath = projDir & "\assets\app.ico"
lnkPath  = projDir & "\ComfyDir.lnk"

If Not fso.FileExists(startVbs) Then
  MsgBox "start.vbs not found at: " & startVbs, vbCritical, "Create Shortcut"
  WScript.Quit 1
End If

If Not fso.FileExists(iconPath) Then
  MsgBox "Icon not found: " & iconPath & vbCrLf & vbCrLf & _
         "Generate it first with:" & vbCrLf & _
         "  .venv\Scripts\python.exe tools\make_icon.py", _
         vbCritical, "Create Shortcut"
  WScript.Quit 1
End If

Set lnk = shell.CreateShortcut(lnkPath)
lnk.TargetPath = "wscript.exe"
lnk.Arguments = """" & startVbs & """"
lnk.WorkingDirectory = projDir
lnk.IconLocation = iconPath & ", 0"
lnk.Description = "ComfyDir - ComfyUI image & prompt organizer"
lnk.WindowStyle = 7  ' minimized (the actual server has no window anyway)
lnk.Save

MsgBox "Shortcut created:" & vbCrLf & lnkPath & vbCrLf & vbCrLf & _
       "Next steps to pin to the taskbar:" & vbCrLf & _
       "  1. Right-click the shortcut" & vbCrLf & _
       "  2. (Windows 11) Click 'Show more options'" & vbCrLf & _
       "  3. Click 'Pin to taskbar'", _
       vbInformation, "ComfyDir"
