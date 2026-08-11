@echo off
rem ?????????? Python???????????¦Ë??????????§Õ?? PYEXE ????
set "PYEXE="
where python >nul 2>nul
if not errorlevel 1 set "PYEXE=python"
if not defined PYEXE if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PYEXE if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python310\python.exe"
if not defined PYEXE if exist "D:\Program Files\Python314\python.exe" set "PYEXE=D:\Program Files\Python314\python.exe"
if not defined PYEXE if exist "D:\Program Files\PYTHON\python.exe" set "PYEXE=D:\Program Files\PYTHON\python.exe"
if not defined PYEXE (
    echo [????] ????? Python???????? Python 3.10 ?????·Ú??????? Add to PATH??
    pause
    exit /b 1
)
