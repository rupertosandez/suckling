Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

projectRoot = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = projectRoot

pythonw = fso.BuildPath(projectRoot, "venv\Scripts\pythonw.exe")
python = fso.BuildPath(projectRoot, "venv\Scripts\python.exe")
launcher = fso.BuildPath(projectRoot, "launcher.py")

If fso.FileExists(pythonw) Then
    command = """" & pythonw & """ """ & launcher & """"
Else
    command = "cmd /c """ & python & """ """ & launcher & """"
End If

shell.Run command, 0, False
