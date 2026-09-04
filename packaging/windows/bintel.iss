; Bin-Tel — Windows installer (Inno Setup 6)
;
; Build the application bundle first, then compile this script:
;
;     python scripts\build_windows.py
;     iscc packaging\windows\bintel.iss
;
; or do both in one step:
;
;     python scripts\build_installer.py
;
; The result is dist\installer\Bin-Tel-Setup-<version>.exe
;
; Two decisions worth stating, because both are deliberate:
;
; 1. PrivilegesRequired=lowest. Bin-Tel is a personal tool that writes only to
;    the user's own folders, so it installs per-user into Local AppData and
;    never raises a UAC prompt. Someone who wants a machine-wide install can
;    still run the installer as an administrator; Inno then offers the choice.
;
; 2. The uninstaller leaves your data alone unless you ask. Your BIN list is
;    the source of truth for the database and it is not ours to delete on the
;    way out — an uninstall that silently removes the list someone spent
;    months curating is indefensible. The last page offers to remove it.

#define AppName        "Bin-Tel"

; The version comes from the build script, which reads it from
; app/core/constants.py — one source of truth. Compiling this file by hand
; still works; it just falls back to the value below, which is why a bump in
; constants.py must not be mirrored here. When the two drifted, Inno wrote
; Bin-Tel-Setup-1.0.0.exe while the build script announced a path with the new
; version in it and no file at the end of it.
#ifndef AppVersion
  #define AppVersion   "1.0.0"
#endif
#define AppPublisher   "Bin-Tel Project"
#define AppURL         "https://bintel.org"
#define AppExeName     "Bin-Tel.exe"
#define SourceDir      "..\..\dist\Bin-Tel"

[Setup]
AppId={{7E2F1A64-4C3B-4A19-9E5D-2B8F6C1D30A7}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
VersionInfoVersion={#AppVersion}

; Per-user by default: no UAC prompt, no administrator needed.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

OutputDir=..\..\dist\installer
OutputBaseFilename=Bin-Tel-Setup-{#AppVersion}
SetupIconFile=..\..\assets\icons\app\bintel.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}

; LZMA2 on a ~190 MB Qt bundle is worth the extra compression time.
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; The whole PyInstaller --onedir output, including _internal\data\bin-list.csv,
; which the first run copies into the user's data folder as a writable list.
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only what the installer itself created. The user's data directory is not
; listed here on purpose — see the uninstall step below.
Type: filesandordirs; Name: "{app}\_internal"

[Code]
{ ------------------------------------------------------------------------
  On uninstall, ask before removing the BIN list and the database built from
  it. Defaulting to "keep" is the point: reinstalling should find your list
  where you left it.
  ------------------------------------------------------------------------ }

function DataDirectory(): String;
begin
  Result := ExpandConstant('{localappdata}\Bin-Tel');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Folder: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;

  Folder := DataDirectory();
  if not DirExists(Folder) then
    Exit;

  if MsgBox(
       'Remove your Bin-Tel data as well?' + #13#10#13#10 +
       'This deletes your BIN list, the database built from it, your saved ' +
       'searches and your watchlists, in:' + #13#10#13#10 +
       Folder + #13#10#13#10 +
       'Choose No to keep them — a future install will pick up where you ' +
       'left off.',
       mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    DelTree(Folder, True, True, True);
  end;
end;
