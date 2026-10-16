@echo off
rem Build and run the standalone scatter-math tests (MSVC or g++).
rem
rem NOTE: this file is ASCII-only on purpose, and test_scatter.cpp is too.
rem The QtScrcpy build adds /utf-8, but an ad-hoc cl invocation does not, and
rem MSVC on a CP936 machine would then choke on non-ASCII source. /utf-8 is
rem passed below so the test is immune either way.
setlocal
pushd "%~dp0"

set SRC=test_scatter.cpp

where cl >nul 2>&1
if not errorlevel 1 goto msvc

where g++ >nul 2>&1
if not errorlevel 1 goto gcc

echo [warn] no compiler on PATH; trying vcvarsall once...
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if not errorlevel 1 goto msvc
call "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if not errorlevel 1 goto msvc

echo [ERROR] no compiler found (need cl.exe or g++).
popd
exit /B 1

:msvc
echo building with cl.exe (UTF-8 source)
cl /nologo /std:c++17 /EHsc /W4 /utf-8 /Fe:test_scatter.exe /Fo:test_scatter.obj "%SRC%"
if errorlevel 1 (
  echo BUILD FAILED
  popd
  exit /B 1
)
test_scatter.exe
set RC=%errorlevel%
popd
exit /B %RC%

:gcc
echo building with g++
g++ -std=c++17 -O2 -Wall -Wextra -o test_scatter.exe "%SRC%"
if errorlevel 1 (
  echo BUILD FAILED
  popd
  exit /B 1
)
test_scatter.exe
set RC=%errorlevel%
popd
exit /B %RC%
