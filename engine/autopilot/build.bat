@echo off
rem Build the test starter (32-bit, no manifest) with Visual Studio 2022.
setlocal
set HERE=%~dp0
set "VSDIR="
for %%d in ("%ProgramFiles%\Microsoft Visual Studio\2022\Community" "%ProgramFiles%\Microsoft Visual Studio\2022\Professional" "%ProgramFiles%\Microsoft Visual Studio\2022\Enterprise" "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools") do (
  if not defined VSDIR if exist "%%~d\VC\Auxiliary\Build\vcvars32.bat" set "VSDIR=%%~d"
)
if not defined VSDIR (echo Visual Studio 2022 C++ tools not found & exit /b 1)
call "%VSDIR%\VC\Auxiliary\Build\vcvars32.bat" >nul || exit /b 1
if not exist "%HERE%out" mkdir "%HERE%out"
cl /nologo /O2 /MT /W3 /D_CRT_SECURE_NO_WARNINGS "%HERE%starter.c" /Fo"%HERE%out\\" /Fe"%HERE%out\facade_test_starter.exe" ^
   /link /SUBSYSTEM:WINDOWS /MANIFEST:NO user32.lib || exit /b 1
echo built %HERE%out\facade_test_starter.exe
