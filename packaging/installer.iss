; Inno Setup script for S4CCM. Built by packaging/build.py, which passes AppVersion, SourceDir and OutDir.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E2C1B4A-5D0F-4C8E-9B57-3A1F2D8C4E90}
AppName=S4CCM
AppVersion={#AppVersion}
AppVerName=S4CCM
AppPublisher=S4CCM
DefaultDirName={autopf}\S4CCM
DefaultGroupName=S4CCM
DisableProgramGroupPage=yes
; per-user install by default (no administrator prompt); the user may still choose "all users"
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutDir}
OutputBaseFilename=S4CCM_Setup_v{#AppVersion}
SetupIconFile={#SourceDir}\_internal\s4ccm\assets\icon.ico
UninstallDisplayIcon={app}\S4CCM.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\S4CCM"; Filename: "{app}\S4CCM.exe"
Name: "{autodesktop}\S4CCM"; Filename: "{app}\S4CCM.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\S4CCM.exe"; Description: "{cm:LaunchProgram,S4CCM}"; Flags: nowait postinstall skipifsilent

; user data (%APPDATA%\S4CCM: memos, previews, settings) is kept on uninstall and on updates
