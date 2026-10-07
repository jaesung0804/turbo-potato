Attribute VB_Name = "EstateFill"
Option Explicit

' Import into a local macro-enabled workbook. Select the helper folder once.
' The original workbook is saved but never overwritten by the collector.
Public Sub FillEstateWorkbook()
    Dim picker As FileDialog, folder As String, book As String
    book = ActiveWorkbook.FullName
    If ActiveWorkbook.Path = "" Then
        MsgBox "Save this workbook first.", vbInformation
        Exit Sub
    End If
    Set picker = Application.FileDialog(msoFileDialogFolderPicker)
    picker.Title = "Select the folder containing fill.cmd"
    If picker.Show <> -1 Then Exit Sub
    folder = picker.SelectedItems(1)
    If Dir(folder & "\fill.cmd") = "" Then
        MsgBox "fill.cmd was not found.", vbExclamation
        Exit Sub
    End If
    ActiveWorkbook.Save
    ' Run a fixed launcher; never treat worksheet text as shell commands.
    If InStr(book & folder, """") Or InStr(book & folder, "%") Then
        MsgBox "Use a workbook path without quotes, percent or exclamation marks.", vbExclamation
        Exit Sub
    End If
    If Dir(folder & "\.venv\Scripts\python.exe") = "" Then
        MsgBox "Run fill.cmd once to install the helper.", vbInformation
        Exit Sub
    End If
    CreateObject("WScript.Shell").Run """" & folder & "\.venv\Scripts\python.exe"" -X utf8 """ & folder & "\fill_workbook.py"" """ & book & """", 1, False
End Sub
