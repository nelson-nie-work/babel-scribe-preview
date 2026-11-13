Unicode true
!include "MUI2.nsh"
!include "x64.nsh"

Name "BABEL Scribe"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\BABEL Scribe Preview"
InstallDirRegKey HKCU "Software\BABEL Scribe Preview" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
SetOverwrite on
VIProductVersion "0.1.0.0"
VIAddVersionKey "ProductName" "BABEL Scribe"
VIAddVersionKey "FileDescription" "BABEL Scribe CPU Preview Setup"
VIAddVersionKey "FileVersion" "0.1.0-alpha"
VIAddVersionKey "ProductVersion" "0.1.0-alpha"
!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_FINISHPAGE_RUN "$INSTDIR\BABEL Scribe.exe"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Function .onInit
    ${IfNot} ${RunningX64}
        MessageBox MB_ICONSTOP "BABEL Scribe requires 64-bit Windows." /SD IDOK
        SetErrorLevel 1
        Quit
    ${EndIf}
    SetRegView 64
    SetShellVarContext current
FunctionEnd

Section "BABEL Scribe" Main
    SectionIn RO
    IfFileExists "$INSTDIR\scribe-installed.marker" check_running
    FindFirst $0 $1 "$INSTDIR\*"
    scan:
        StrCmp $1 "" empty_directory
        StrCmp $1 "." skip_item
        StrCmp $1 ".." skip_item
        FindClose $0
        MessageBox MB_ICONSTOP "Choose an empty folder for BABEL Scribe." /SD IDOK
        SetErrorLevel 1
        Abort
    skip_item:
        FindNext $0 $1
        Goto scan
    empty_directory:
        FindClose $0
        Goto extract
    check_running:
        ClearErrors
        FileOpen $0 "$INSTDIR\runtime\pythonw.exe" a
        IfErrors app_running
        FileClose $0
        Goto extract
    app_running:
        MessageBox MB_ICONSTOP "Exit BABEL Scribe from the tray before installing the update." /SD IDOK
        SetErrorLevel 1
        Abort
    extract:
        SetOutPath "$INSTDIR"
        File /r "${STAGE}\*.*"
        WriteUninstaller "$INSTDIR\Uninstall.exe"
        CreateDirectory "$SMPROGRAMS\BABEL Scribe Preview"
        CreateShortCut "$SMPROGRAMS\BABEL Scribe Preview\BABEL Scribe.lnk" "$INSTDIR\BABEL Scribe.exe"
        WriteRegStr HKCU "Software\BABEL Scribe Preview" "InstallDir" "$INSTDIR"
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BABEL Scribe Preview" "DisplayName" "BABEL Scribe Preview"
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BABEL Scribe Preview" "DisplayVersion" "0.1.0-alpha"
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BABEL Scribe Preview" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BABEL Scribe Preview" "InstallLocation" "$INSTDIR"
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BABEL Scribe Preview" "DisplayIcon" "$INSTDIR\BABEL Scribe.exe"
        WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BABEL Scribe Preview" "NoModify" 1
        WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BABEL Scribe Preview" "NoRepair" 1
SectionEnd

Section /o "Desktop shortcut" Desktop
    CreateShortCut "$DESKTOP\BABEL Scribe Preview.lnk" "$INSTDIR\BABEL Scribe.exe"
SectionEnd

Section "Uninstall"
    SetRegView 64
    SetShellVarContext current
    IfFileExists "$INSTDIR\scribe-installed.marker" 0 unsafe
    ClearErrors
    FileOpen $0 "$INSTDIR\runtime\pythonw.exe" a
    IfErrors running
    FileClose $0
    Delete "$SMPROGRAMS\BABEL Scribe Preview\BABEL Scribe.lnk"
    RMDir "$SMPROGRAMS\BABEL Scribe Preview"
    Delete "$DESKTOP\BABEL Scribe Preview.lnk"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "BABEL Scribe Preview"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BABEL Scribe Preview"
    DeleteRegKey HKCU "Software\BABEL Scribe Preview"
    RMDir /r "$INSTDIR\runtime"
    RMDir /r "$INSTDIR\scribe"
    Delete "$INSTDIR\BABEL Scribe.exe"
    Delete "$INSTDIR\LICENSE"
    Delete "$INSTDIR\THIRD_PARTY_NOTICES.md"
    Delete "$INSTDIR\README.md"
    Delete "$INSTDIR\runtime-inventory.txt"
    Delete "$INSTDIR\nsis-COPYING.txt"
    Delete "$INSTDIR\scribe-installed.marker"
    Delete "$INSTDIR\media\dictation.gif"
    Delete "$INSTDIR\media\quick-add.gif"
    Delete "$INSTDIR\media\learn-after-edit.gif"
    Delete "$INSTDIR\media\dictionary.gif"
    Delete "$INSTDIR\media\history.gif"
    RMDir "$INSTDIR\media"
    Delete "$INSTDIR\packaging\windows\README.md"
    Delete "$INSTDIR\packaging\windows\runtime-lock.txt"
    RMDir "$INSTDIR\packaging\windows"
    RMDir "$INSTDIR\packaging"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir "$INSTDIR"
    Goto done
    unsafe:
        MessageBox MB_ICONSTOP "BABEL Scribe installation marker not found." /SD IDOK
        SetErrorLevel 1
        Abort
    running:
        MessageBox MB_ICONSTOP "Exit BABEL Scribe from the tray before uninstalling." /SD IDOK
        SetErrorLevel 1
        Abort
    done:
SectionEnd
