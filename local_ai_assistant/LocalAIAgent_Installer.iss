; LocalAIAgent_Installer.iss
;
; Inno Setup configuration for Local AI Assistant
; 
; This script creates a professional Windows installer that:
; - Installs LocalAIAgent.exe to Program Files
; - Configures user settings via wizard
; - Creates Start Menu / Desktop shortcuts
; - Generates .env configuration file
; - Sets up user data directory in APPDATA
;
; Build with:
;   "C:\Program Files (x86)\Inno Setup 6\iscc.exe" LocalAIAgent_Installer.iss
;
; Output:
;   Output\LocalAIAgent_Installer.exe (~100 MB)

#define MyAppName "Local AI Assistant"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Local AI"
#define MyAppURL "https://github.com/your-repo/local-ai-assistant"
#define MyAppExeName "LocalAIAgent.exe"
; Change this to point to your dist/ folder where PyInstaller output is
#define SourceExePath "dist\LocalAIAgent.exe"

[Setup]
; ─────────────────────────────────────────────────────────────────────────
; Basic Installer Settings
; ─────────────────────────────────────────────────────────────────────────
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; ─────────────────────────────────────────────────────────────────────────
; Installation Directories
; ─────────────────────────────────────────────────────────────────────────
DefaultDirName={autopf}\Local AI Assistant
DefaultGroupName=Local AI Assistant
AllowNoIcons=yes
OutputDir=Output
OutputBaseFilename=LocalAIAgent_Installer

; ─────────────────────────────────────────────────────────────────────────
; GUI & Visual Settings
; ─────────────────────────────────────────────────────────────────────────
SetupIconFile=icon.ico
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern
ShowLanguageDialog=yes

; ─────────────────────────────────────────────────────────────────────────
; Permissions & Elevation
; ─────────────────────────────────────────────────────────────────────────
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=no
ChangesEnvironment=no

; ─────────────────────────────────────────────────────────────────────────
; File Association (optional)
; ─────────────────────────────────────────────────────────────────────────
ChangesAssociations=no

; ─────────────────────────────────────────────────────────────────────────
; 64-bit Support
; ─────────────────────────────────────────────────────────────────────────
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quick_start"; Description: "Show Quick Start Guide after installation"; GroupDescription: "{cm:AdditionalIcons}"
Name: "open_folder"; Description: "Open installation folder after installation"; GroupDescription: "{cm:AdditionalIcons}"

; ─────────────────────────────────────────────────────────────────────────
; FILES TO INSTALL
; ─────────────────────────────────────────────────────────────────────────
[Files]
; Main executable from PyInstaller dist/
Source: "{#SourceExePath}"; DestDir: "{app}"; Flags: ignoreversion

; Documentation files (copy from source if present)
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme; \
  OnlyBelowVersion: 0
Source: "WINDOWS_DEPLOYMENT_PLAN.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "TROUBLESHOOTING.md"; DestDir: "{app}"; Flags: ignoreversion; \
  OnlyBelowVersion: 0

; Uninstaller icon
Source: "icon.ico"; DestDir: "{app}"; Flags: dontcopy

; ─────────────────────────────────────────────────────────────────────────
; SHORTCUTS
; ─────────────────────────────────────────────────────────────────────────
[Icons]
; Start Menu Group
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  Comment: "Run Local AI Assistant"
Name: "{group}\{#MyAppName} Configuration"; Filename: "notepad.exe"; \
  Parameters: "{app}\.env"; Comment: "Edit .env configuration"
Name: "{group}\Open Data Folder"; Filename: "{userappdatalocal}\..\Roaming\LocalAIAgent\data"; \
  Comment: "Open user data directory"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

; Desktop shortcut (if user selects it)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
  Tasks: desktopicon; Comment: "Run Local AI Assistant"

; ─────────────────────────────────────────────────────────────────────────
; INSTALLATION CODE (Pascal/Inno Setup scripting)
; ─────────────────────────────────────────────────────────────────────────
[Code]
var
  UserNameEdit: TEdit;
  WindowsDocsPathEdit: TEdit;
  EmailHostEdit: TEdit;
  EmailPortEdit: TEdit;
  EmailUserEdit: TEdit;
  EmailPasswordEdit: TEdit;
  EmailFromEdit: TEdit;
  TesseractPathEdit: TEdit;

  UserNameInput: String;
  WindowsDocsPathInput: String;
  EmailHostInput: String;
  EmailPortInput: String;
  EmailUserInput: String;
  EmailPasswordInput: String;
  EmailFromInput: String;
  TesseractPathInput: String;

procedure InitializeWizard;
var
  WizPage: TWizardPage;
begin
  { This procedure is called at startup to create custom wizard pages }
  WizPage := CreateCustomPage(wpSelectDir,
    'Configuration', 'Configure Local AI Assistant');

  { User Name }
  TNewStaticText.Create(WizPage).Caption := 'User Name:';
  UserNameEdit := TEdit.Create(WizPage);
  UserNameEdit.Left := 0;
  UserNameEdit.Top := 20;
  UserNameEdit.Width := 400;
  UserNameEdit.Text := GetEnv('USERNAME');
  WizPage.Add(UserNameEdit);

  { Windows Documents Path }
  TNewStaticText.Create(WizPage).Caption := 'Windows Documents Path (for indexing):';
  WindowsDocsPathEdit := TEdit.Create(WizPage);
  WindowsDocsPathEdit.Left := 0;
  WindowsDocsPathEdit.Top := 60;
  WindowsDocsPathEdit.Width := 400;
  WindowsDocsPathEdit.Text := 'C:\AI_Test_Documents';
  WizPage.Add(WindowsDocsPathEdit);

  { Email Configuration Header }
  TNewStaticText.Create(WizPage).Caption := 'Email Configuration (optional - leave blank to skip):';
  
  { EMAIL_HOST }
  TNewStaticText.Create(WizPage).Caption := 'Email SMTP Host:';
  EmailHostEdit := TEdit.Create(WizPage);
  EmailHostEdit.Left := 0;
  EmailHostEdit.Top := 100;
  EmailHostEdit.Width := 400;
  EmailHostEdit.Text := 'smtp.gmail.com';
  WizPage.Add(EmailHostEdit);

  { EMAIL_PORT }
  TNewStaticText.Create(WizPage).Caption := 'Email SMTP Port:';
  EmailPortEdit := TEdit.Create(WizPage);
  EmailPortEdit.Left := 0;
  EmailPortEdit.Top := 140;
  EmailPortEdit.Width := 100;
  EmailPortEdit.Text := '587';
  WizPage.Add(EmailPortEdit);

  { EMAIL_USER }
  TNewStaticText.Create(WizPage).Caption := 'Email User (your email address):';
  EmailUserEdit := TEdit.Create(WizPage);
  EmailUserEdit.Left := 0;
  EmailUserEdit.Top := 180;
  EmailUserEdit.Width := 400;
  EmailUserEdit.Text := '';
  WizPage.Add(EmailUserEdit);

  { EMAIL_PASSWORD }
  TNewStaticText.Create(WizPage).Caption := 'Email Password (or app-specific password):';
  EmailPasswordEdit := TEdit.Create(WizPage);
  EmailPasswordEdit.Left := 0;
  EmailPasswordEdit.Top := 220;
  EmailPasswordEdit.Width := 400;
  EmailPasswordEdit.Text := '';
  EmailPasswordEdit.PasswordChar := '*';
  WizPage.Add(EmailPasswordEdit);

  { EMAIL_FROM }
  TNewStaticText.Create(WizPage).Caption := 'Email Sender Name:';
  EmailFromEdit := TEdit.Create(WizPage);
  EmailFromEdit.Left := 0;
  EmailFromEdit.Top := 260;
  EmailFromEdit.Width := 400;
  EmailFromEdit.Text := '';
  WizPage.Add(EmailFromEdit);

  { TESSERACT_CMD }
  TNewStaticText.Create(WizPage).Caption := 'Tesseract OCR Path (optional):';
  TesseractPathEdit := TEdit.Create(WizPage);
  TesseractPathEdit.Left := 0;
  TesseractPathEdit.Top := 300;
  TesseractPathEdit.Width := 400;
  TesseractPathEdit.Text := 'C:\Program Files\Tesseract-OCR\tesseract.exe';
  WizPage.Add(TesseractPathEdit);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { This is called when wizard page changes }
  if CurPageID = wpSelectDir then
  begin
    { Collect user inputs }
    UserNameInput := UserNameEdit.Text;
    WindowsDocsPathInput := WindowsDocsPathEdit.Text;
    EmailHostInput := EmailHostEdit.Text;
    EmailPortInput := EmailPortEdit.Text;
    EmailUserInput := EmailUserEdit.Text;
    EmailPasswordInput := EmailPasswordEdit.Text;
    EmailFromInput := EmailFromEdit.Text;
    TesseractPathInput := TesseractPathEdit.Text;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  EnvFilePath: String;
  EnvContent: String;
begin
  if CurStep = ssPostInstall then
  begin
    { Create .env file after installation }
    EnvFilePath := ExpandConstant('{app}\.env');
    
    EnvContent := 
      '# Local AI Assistant Configuration' + #13#10 +
      '# Auto-generated by installer' + #13#10 +
      '#' + #13#10 +
      '# User Identity' + #13#10 +
      'USER_NAME=' + UserNameInput + #13#10 +
      #13#10 +
      '# Windows Documents Indexing' + #13#10 +
      'WINDOWS_DOCS_PATH=' + WindowsDocsPathInput + #13#10 +
      '# WINDOWS_DOCS_SUBFOLDERS=Work,Projects    # (optional)' + #13#10 +
      #13#10;
    
    { Add email config if provided }
    if EmailHostInput <> '' then
    begin
      EnvContent := EnvContent +
        '# Email Configuration' + #13#10 +
        'EMAIL_HOST=' + EmailHostInput + #13#10 +
        'EMAIL_PORT=' + EmailPortInput + #13#10 +
        'EMAIL_USER=' + EmailUserInput + #13#10 +
        'EMAIL_PASS=' + EmailPasswordInput + #13#10 +
        'EMAIL_FROM=' + EmailFromInput + #13#10 +
        '#EMAIL_TLS=true' + #13#10 +
        #13#10;
    end;
    
    { Add tesseract config if provided }
    if TesseractPathInput <> '' then
    begin
      EnvContent := EnvContent +
        '# OCR (PDF Text Extraction)' + #13#10 +
        'TESSERACT_CMD=' + TesseractPathInput + #13#10 +
        #13#10;
    end;
    
    { Advanced settings (commented out) }
    EnvContent := EnvContent +
      '# Advanced Configuration (uncomment to customize)' + #13#10 +
      '# MODEL_NAME=llama3' + #13#10 +
      '# EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2' + #13#10 +
      '# EMBEDDING_DEVICE=cpu' + #13#10 +
      '# LOG_LEVEL=INFO' + #13#10 +
      '# LOG_FILE=debug.log' + #13#10;
    
    { Write .env file }
    SaveStringToFile(EnvFilePath, EnvContent, False);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: String;
  Response: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    { Ask user if they want to keep data }
    DataPath := ExpandConstant('{userappdatalocal}\..\Roaming\LocalAIAgent');
    
    if DirExists(DataPath) then
    begin
      Response := MsgBox(
        'Do you want to keep your user data, settings, and vector stores?' + #10#10 +
        'User data location: ' + DataPath + #10#10 +
        'If you select No, all data will be deleted.',
        mbConfirmation, MB_YESNO);
      
      if Response = IDNO then
      begin
        { User chose to delete data }
        DelTree(DataPath, True, True, True);
      end;
    end;
  end;
end;

function GetUninstallString(): String;
var
  sUnInstPath: String;
  sUnInstallString: String;
begin
  sUnInstPath := ExpandConstant('Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1');
  sUnInstallString := '';
  if not RegQueryStringValue(HKLM, sUnInstPath, 'UninstallString', sUnInstallString) then
    RegQueryStringValue(HKCU, sUnInstPath, 'UninstallString', sUnInstallString);
  Result := sUnInstallString;
end;

function IsUpgrade(): Boolean;
begin
  Result := (GetUninstallString() <> '');
end;

function InitializeSetup(): Boolean;
var
  V: Integer;
  iResultCode: Integer;
  S: String;
begin
  Result := True;
  
  { Check if already installed, offer to uninstall first }
  if IsUpgrade() then begin
    S := GetUninstallString();
    if MsgBox(
      'Local AI Assistant is already installed. Continue to Upgrade?',
      mbConfirmation, MB_YESNO) = IDYES then
    begin
      ShellExec('runas', RemoveQuotes(S), '/S', '', SW_HIDE, ewWaitUntilTerminated, iResultCode);
      Sleep(1000);
      Result := True;
    end else
      Result := False;
  end;
end;

; ─────────────────────────────────────────────────────────────────────────
; MESSAGES
; ─────────────────────────────────────────────────────────────────────────
[Messages]
WelcomeLabel1=Welcome to [name] Setup
WelcomeLabel2=This will install [name/ver] on your computer.%n%nLocal AI Assistant is an intelligent agent system for document analysis, email management, and semantic search.%n%nIMPORTANT: You must have Ollama (https://ollama.ai) installed and running before using the application.

FinishedHeadingText=Completing the [name] Setup Wizard
FinishedLabelText=[name] has been install on your computer.%n%nIMPORTANT NEXT STEPS:%n%n1. Download and run Ollama from https://ollama.ai%n   Ollama is required for the application to function.%n%n2. (Optional) Install Tesseract-OCR if you want PDF/image text extraction:%n   https://github.com/UB-Mannheim/tesseract/wiki%n%n3. Edit the configuration file:%n  {app}\.env%n  (Use Notepad to open and customize settings)%n%n4. If you plan to use email features, you must set up Google OAuth credentials:%n   https://developers.google.com/workspace/guides/create-credentials

FinishedLabel=Click Finish to exit Setup.

; ─────────────────────────────────────────────────────────────────────────
; UNINSTALL SETUP
; ─────────────────────────────────────────────────────────────────────────
[UninstallRun]
; (no automated uninstall steps needed; user data preserved unless deleted)

[Run]
; Optionally run the app after installation (commented out for now)
; Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: shellexec postinstall skipifsilent

Filename: "notepad.exe"; Parameters: "{app}\.env"; Description: "Edit Configuration File (.env)"; Flags: postinstall skipifsilent; Tasks: quick_start
Filename: "{app}"; Description: "Open Installation Folder"; Flags: shellexec postinstall skipifsilent; Tasks: open_folder

