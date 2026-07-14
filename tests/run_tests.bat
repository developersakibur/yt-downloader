@echo off
setlocal EnableExtensions

:: ============================================================
:: run_tests.bat
:: Place this file inside the "tests" folder (same place as
:: pytest.ini). It uses the embedded Python from the project
:: root (one level up) but runs pytest from inside tests/,
:: so pytest.ini here is picked up correctly.
:: ============================================================

:: Remember the tests/ folder (where this .bat and pytest.ini live)
set TESTS_DIR=%~dp0

:: Find the embedded Python, which lives one level up, in project root
set PYTHON_EXE=%TESTS_DIR%..\python\python.exe

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Could not find %PYTHON_EXE%
    echo Expected "python" folder at the project root, one level above "tests".
    pause
    exit /b 1
)

:: Run pytest FROM the tests/ folder, so it uses tests/pytest.ini
cd /d "%TESTS_DIR%"

echo Running tests from: %cd%
echo Using embedded Python: %PYTHON_EXE%
echo.

"%PYTHON_EXE%" -m pytest %*

echo.
pause
