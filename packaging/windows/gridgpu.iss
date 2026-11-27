; Inno Setup script for GPUHive.
; Wraps the PyInstaller-built gridgpu.exe into a Windows installer wizard.
; Build in CI:  ISCC /DMyAppVersion=0.1.0 packaging\windows\gridgpu.iss
; Expects dist\gridgpu.exe to exist (produced by: pyinstaller packaging\gridgpu.spec).

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName "GPUHive"
#define MyAppPublisher "GPUHive"
#define MyAppURL "https://github.com/Sidd11145/GPUHive"
#define MyAppExeName "gridgpu.exe"

[Setup]
AppId={{B7F3C2A1-9D4E-4C8B-A1F6-GPUHIVE000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=GPUHive-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ChangesEnvironment=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked
Name: "addtopath"; Description: "Add GPUHive to PATH (run 'gridgpu' from any terminal)"; GroupDescription: "Options:"

[Files]
Source: "..\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Join GPUHive"; Filename: "{app}\{#MyAppExeName}"; Parameters: "join"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Join GPUHive"; Filename: "{app}\{#MyAppExeName}"; Parameters: "join"; Tasks: desktopicon

[Registry]
; Append install dir to the user PATH when the addtopath task is selected.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
    ValueData: "{olddata};{app}"; Tasks: addtopath; \
    Check: NeedsAddPath('{app}')

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "join"; \
    Description: "Join GPUHive now (share this GPU)"; \
    Flags: postinstall nowait skipifsilent

[Code]
function NeedsAddPath(Param: string): Boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + ExpandConstant(Param) + ';', ';' + OrigPath + ';') = 0;
end;
