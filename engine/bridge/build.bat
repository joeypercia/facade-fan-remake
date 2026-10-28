@echo off
rem Build the 32-bit proxy bridge with Visual Studio 2022's x86 toolchain.
setlocal
set HERE=%~dp0
set JDK=%JAVA_HOME%
if "%JDK%"=="" set JDK=C:\Program Files\Java\jdk-21
set "VSDIR="
for %%d in ("%ProgramFiles%\Microsoft Visual Studio\2022\Community" "%ProgramFiles%\Microsoft Visual Studio\2022\Professional" "%ProgramFiles%\Microsoft Visual Studio\2022\Enterprise" "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools") do (
  if not defined VSDIR if exist "%%~d\VC\Auxiliary\Build\vcvars32.bat" set "VSDIR=%%~d"
)
if not defined VSDIR (echo Visual Studio 2022 C++ tools not found & exit /b 1)
call "%VSDIR%\VC\Auxiliary\Build\vcvars32.bat" >nul || exit /b 1
if not exist "%HERE%out" mkdir "%HERE%out"
cl /nologo /LD /O2 /EHsc /MT /W3 /D_CRT_SECURE_NO_WARNINGS ^
   /I"%JDK%\include" /I"%JDK%\include\win32" ^
   "%HERE%bridge.cpp" /Fo"%HERE%out\\" /Fe"%HERE%out\nativeaniminterface.dll" ^
   /link /DEF:"%HERE%bridge.def" user32.lib ole32.lib || exit /b 1
echo built %HERE%out\nativeaniminterface.dll
