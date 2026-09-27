; PLOS AI 安装包脚本（Inno Setup 6）
; 构建：ISCC.exe installer\plos_ai.iss
; 产物：installer\output\PLOS_AI_Setup_<version>.exe

#define MyAppName "PLOS AI 个人学习操作系统"
#define MyAppNameEn "PLOS AI"
#define MyAppVersion "2.3.1"
#define MyAppExeName "PLOS AI.exe"

[Setup]
AppId={{8E5B4C2A-7F3D-4B9E-9C1A-PLOSAI000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Michael Piao
; 程序的数据目录（data/config/logs）位于安装目录下，必须装到当前用户可写的位置，
; 否则装在 Program Files 下会因无写权限而无法初始化数据库
DefaultDirName={localappdata}\Programs\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=output
OutputBaseFilename=PLOS_AI_Setup_{#MyAppVersion}
SetupIconFile=app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
; 按当前用户安装，无需管理员权限（安装目录与数据目录均用户可写）
PrivilegesRequired=lowest
; 允许在向导里改选「为所有用户安装」（会请求管理员权限）：
; 装到 D:\APP 这类当前用户没有写权限的目录时必须走这条路径，否则报 Error 5 拒绝访问
PrivilegesRequiredOverridesAllowed=commandline dialog
CloseApplications=yes
LicenseFile=


[Files]
; Permissions: users-modify 让安装后的目录与文件对所有用户可写，
; 保证 data/config/logs 在任何安装位置都能正常创建（程序运行时是普通权限）
Source: "..\dist\PLOS AI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Permissions: users-modify
Source: "app.ico"; DestDir: "{app}"; Flags: ignoreversion; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app.ico"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"


[Code]
{ 在「选择目标位置」页做一次真实写入测试：
  与其让安装过程走到一半报 "Error 5: 拒绝访问"，不如当场提示用户换目录或改用管理员安装。 }
function DirIsWritable(const Dir: String): Boolean;
var
  ProbeFile: String;
begin
  Result := False;
  if not DirExists(Dir) then
  begin
    if not ForceDirectories(Dir) then
      Exit;
  end;
  ProbeFile := AddBackslash(Dir) + '.plos_write_probe';
  if SaveStringToFile(ProbeFile, 'probe', False) then
  begin
    DeleteFile(ProbeFile);
    Result := True;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Target: String;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and (not IsAdminInstallMode) then
  begin
    Target := WizardDirValue;
    if not DirIsWritable(Target) then
    begin
      MsgBox('无法写入这个目录：' + #13#10 + Target + #13#10#13#10 +
             '常见原因：该目录（或其上级目录）的权限不允许当前用户写入，' +
             '例如直接装在 D:\APP、D 盘根目录或 Program Files 下。' + #13#10#13#10 +
             '可以这样做：' + #13#10 +
             '1. 换一个位置，例如自己的文件夹或桌面；或' + #13#10 +
             '2. 点「上一步」后改选「为所有用户安装」（需要管理员权限）；或' + #13#10 +
             '3. 直接使用默认目录（在 C 盘用户目录下，一定可写）。',
             mbError, MB_OK);
      Result := False;
    end;
  end;
end;

