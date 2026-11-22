; Inno Setup 6: same immutable onedir build as the Portable ZIP.
#ifndef AppVersion
  #error AppVersion must be supplied by tools/build_v1.py
#endif
#ifndef NumericVersion
  #error NumericVersion must be supplied by tools/build_v1.py
#endif
#ifndef BuildSource
  #error BuildSource must be a clean, audited PyInstaller output
#endif
#ifndef OutputDirectory
  #error OutputDirectory must be an empty build artifacts directory
#endif

[Setup]
AppId={{7B364EE0-D3E6-45DC-A8C8-C46B888A64F6}
AppName=CapsWriter Offline
AppVersion={#AppVersion}
VersionInfoVersion={#NumericVersion}
AppPublisher=CapsWriter Offline contributors
AppPublisherURL=https://github.com/qianxiaohuaqi-design/CapsWriter-Offline
AppSupportURL=https://github.com/qianxiaohuaqi-design/CapsWriter-Offline/issues
AppUpdatesURL=https://github.com/qianxiaohuaqi-design/CapsWriter-Offline/releases
DefaultDirName={localappdata}\Programs\CapsWriter
DefaultGroupName=CapsWriter
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
DisableProgramGroupPage=yes
DisableDirPage=auto
WizardStyle=modern
UninstallDisplayIcon={app}\CapsWriter.exe
SetupIconFile={#BuildSource}\_internal\assets\source\capswriter.ico
LicenseFile={#BuildSource}\_internal\LICENSE
InfoBeforeFile=RELEASE_NOTES.md
OutputDir={#OutputDirectory}
OutputBaseFilename=CapsWriter-Offline-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
AppMutex=CapsWriter.Offline.ControlCenter

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#elif FileExists(AddBackslash(CompilerPath) + "Languages\Unofficial\ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\Unofficial\ChineseSimplified.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
Source: "{#BuildSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "portable.flag,data\*,models\*,logs\*,web_gui\private_config.json"

[Icons]
Name: "{group}\CapsWriter"; Filename: "{app}\CapsWriter.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\CapsWriter"; Filename: "{app}\CapsWriter.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\卸载 CapsWriter"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\CapsWriter.exe"; Description: "打开 CapsWriter"; Flags: nowait postinstall skipifsilent unchecked

; No Run registry entry and no UninstallDelete: personal data/models under
; LocalAppData\CapsWriter are intentionally outside the installer-owned directory.
[Code]
function HasWebView2(): Boolean;
var
  RuntimeVersion: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', RuntimeVersion) then
    Result := (RuntimeVersion <> '') and (RuntimeVersion <> '0.0.0.0');
  if not Result then
    if RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', RuntimeVersion) then
      Result := (RuntimeVersion <> '') and (RuntimeVersion <> '0.0.0.0');
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if CheckForMutexes('CapsWriter.Offline.ControlCenter') then begin
    MsgBox('CapsWriter 仍在后台运行。请先在托盘选择“完全退出”，确认任务已安全停止后，再重新运行安装程序。', mbInformation, MB_OK);
    Result := False;
  end;
  if Result and (not HasWebView2()) then
    Result := SuppressibleMsgBox('未检测到 Microsoft WebView2 Runtime。原生控制中心需要此组件；可从 https://developer.microsoft.com/microsoft-edge/webview2/ 获取。安装程序不会自动联网安装依赖。是否仍继续安装 CapsWriter？', mbConfirmation, MB_YESNO, IDNO) = IDYES;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if CheckForMutexes('CapsWriter.Offline.ControlCenter') then begin
    MsgBox('CapsWriter 仍在运行。请先在托盘完全退出，再卸载。模型、配置和个人数据会保留。', mbInformation, MB_OK);
    Result := False;
  end;
end;
