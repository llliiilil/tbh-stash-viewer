' start_with_game.vbs - Launch the TBH stash viewer, then relay the game command.
' No console window anywhere: wscript is windowless and exits at once after
' starting both the viewer and the game.
'
' Steam launch options for Task Bar Hero (appid 3678970):
'   C:\Windows\System32\wscript.exe "<this folder>\start_with_game.vbs" %command%
' Steam (Windows) runs the first token directly without a shell and hands the rest
' over as arguments, so the expanded %command% arrives here as script arguments
' and this script relays it to the game after starting the viewer.
Option Explicit
On Error Resume Next   ' never block the game launch with an error dialog
Dim sh, fso, root, pid, ts, gameCmd, a, py
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 0) paths: data root = this script's folder; python = TBH_PYTHON or a known install path
root = fso.GetParentFolderName(WScript.ScriptFullName)
py = sh.ExpandEnvironmentStrings("%TBH_PYTHON%")
If InStr(py, "%") > 0 Or Not fso.FileExists(py) Then
    py = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe")
    If Not fso.FileExists(py) Then py = "pythonw.exe"
End If

' 1) stash viewer (single-instance is enforced inside via named mutex)
pid = sh.Run("""" & py & """ -X utf8 """ & root & "\stash_viewer.py""", 1, False)

' 2) relay the game command (%command% expansion arrives as script arguments;
'    WScript.Arguments dequotes, so re-quote any piece containing spaces)
gameCmd = ""
For Each a In WScript.Arguments
    If InStr(a, " ") > 0 Then
        gameCmd = gameCmd & " """ & a & """"
    Else
        gameCmd = gameCmd & " " & a
    End If
Next
gameCmd = Trim(gameCmd)
If Len(gameCmd) > 0 Then
    sh.Run gameCmd, 1, False
End If

Set ts = fso.OpenTextFile(root & "\launch_log.txt", 8, True)
ts.WriteLine Now & " viewer pid=" & pid & " game=[" & gameCmd & "]"
ts.Close
