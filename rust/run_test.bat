@echo off
rem Test launch: settings and DB go to rust\test_home instead of %APPDATA%\S4CCM
set "S4CCM_HOME=%~dp0test_home"
start "" "%~dp0dist\S4CCM\S4CCM.exe"
