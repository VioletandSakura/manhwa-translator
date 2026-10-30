Option Explicit
Dim files, shell, root, python, launcher, command, result
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(root, ".venv\Scripts\python.exe")
launcher = files.BuildPath(root, "scripts\launch_gui.py")
If Not files.FileExists(python) Or Not files.FileExists(launcher) Then
    MsgBox "The Python environment or launcher is missing. Run scripts\setup.ps1 first.", 16, "Manga Translator"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
command = Chr(34) & python & Chr(34) & " -B " & Chr(34) & launcher & Chr(34)
If WScript.Arguments.Count > 0 Then
    If WScript.Arguments(0) = "--check" Then command = command & " --check"
End If
result = shell.Run(command, 0, True)
If result <> 0 Then
    MsgBox "The application could not start. See logs\startup.log for details.", 16, "Manga Translator"
End If
WScript.Quit result
