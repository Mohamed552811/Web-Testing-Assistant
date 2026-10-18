; Inno Setup script for Web Testing Assistant. Built by tools/build_installer.py, which passes:
;   /DAppVersion=<x.y.z> /DSourceDir=<PyInstaller folder> /DOutputDir=<folder> /DIconFile=<app.ico>
;   /DWizardImage=<bmp list> /DWizardSmallImage=<bmp list>

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Web Testing Assistant"
#define AppExe "WebTestingAssistant.exe"
; The app sets this Windows taskbar identity itself; shortcuts use the same one, so pinned icons group.
#define AppUserModelID "WebTestingAssistant.App"

[Setup]
AppId={{A49B2E96-5B98-5D92-8DCC-4C6214AAF1B1}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Mohamed Hesham
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installs for the current user without administrator rights; the dialog offers all users too.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=WebTestingAssistant-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
WizardImageFile={#WizardImage}
WizardSmallImageFile={#WizardSmallImage}
Compression=lzma2
SolidCompression=yes
; Close a running copy (Restart Manager) before files are replaced.
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// Sessions, runs, settings and backups live in %APPDATA%\Web Testing Assistant and are not removed.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('Your sessions, runs, settings and backups are kept in' + #13#10 +
           ExpandConstant('{userappdata}\{#AppName}') + #13#10 +
           'Delete that folder yourself if you no longer need them.', mbInformation, MB_OK);
end;
