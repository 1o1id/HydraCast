@echo off
setlocal enabledelayedexpansion

:: 1. Prompt for the target folder
set /p "target_dir=Enter the full path of the folder: "
set "target_dir=%target_dir:"=%"

if not exist "%target_dir%" (
    echo Error: The specified folder does not exist.
    pause
    exit /b
)

:: 2. Display the options menu
echo.
echo Select Renaming Option:
echo 1. Direct Swap: Keep length, change () to _ (e.g., (MON) -> _MON_ / (MONDAY) -> _MONDAY_)
echo 2. Convert all to Short Underscore        (e.g., (MON) or (MONDAY) -> _MON_)
echo 3. Convert all to Long Underscore         (e.g., (MON) or (MONDAY) -> _MONDAY_)
echo.

choice /c 123 /m "Enter your choice (1-3): "
set "choice=%errorlevel%"

echo --------------------------------------------------
echo Processing files and folders...
echo --------------------------------------------------

:: Option 1: Direct Swap () to _
if "%choice%"=="1" (
    call :Work "(MONDAY)" "_MONDAY_"
    call :Work "(TUESDAY)" "_TUESDAY_"
    call :Work "(WEDNESDAY)" "_WEDNESDAY_"
    call :Work "(THURSDAY)" "_THURSDAY_"
    call :Work "(FRIDAY)" "_FRIDAY_"
    call :Work "(SATURDAY)" "_SATURDAY_"
    call :Work "(SUNDAY)" "_SUNDAY_"
    
    call :Work "(MON)" "_MON_"
    call :Work "(TUE)" "_TUE_"
    call :Work "(WED)" "_WED_"
    call :Work "(THU)" "_THU_"
    call :Work "(FRI)" "_FRI_"
    call :Work "(SAT)" "_SAT_"
    call :Work "(SUN)" "_SUN_"
)

:: Option 2: Force Short Underscore
if "%choice%"=="2" (
    call :Work "(MONDAY)" "_MON_"
    call :Work "(TUESDAY)" "_TUE_"
    call :Work "(WEDNESDAY)" "_WED_"
    call :Work "(THURSDAY)" "_THU_"
    call :Work "(FRIDAY)" "_FRI_"
    call :Work "(SATURDAY)" "_SAT_"
    call :Work "(SUNDAY)" "_SUN_"
    
    call :Work "(MON)" "_MON_"
    call :Work "(TUE)" "_TUE_"
    call :Work "(WED)" "_WED_"
    call :Work "(THU)" "_THU_"
    call :Work "(FRI)" "_FRI_"
    call :Work "(SAT)" "_SAT_"
    call :Work "(SUN)" "_SUN_"
)

:: Option 3: Force Long Underscore
if "%choice%"=="3" (
    call :Work "(MONDAY)" "_MONDAY_"
    call :Work "(TUESDAY)" "_TUESDAY_"
    call :Work "(WEDNESDAY)" "_WEDNESDAY_"
    call :Work "(THURSDAY)" "_THURSDAY_"
    call :Work "(FRIDAY)" "_FRIDAY_"
    call :Work "(SATURDAY)" "_SATURDAY_"
    call :Work "(SUNDAY)" "_SUNDAY_"
    
    call :Work "(MON)" "_MONDAY_"
    call :Work "(TUE)" "_TUESDAY_"
    call :Work "(WED)" "_WEDNESDAY_"
    call :Work "(THU)" "_THURSDAY_"
    call :Work "(FRI)" "_FRIDAY_"
    call :Work "(SAT)" "_SATURDAY_"
    call :Work "(SUN)" "_SUNDAY_"
)

echo --------------------------------------------------
echo Done! All matching items have been processed.
pause
exit /b

:: The core processing engine
:Work
set "search=%~1"
set "replace=%~2"

:: Step A: Rename Files
for /f "delims=" %%F in ('dir "%target_dir%\*%search%*" /b /s /a:-d 2^>nul') do (
    set "filepath=%%F"
    set "filename=%%~nxF"
    
    :: Correctly evaluate string replacement using pre-expanded search and replace values
    call set "newfilename=%%filename:%search%=%replace%%%"
    
    if not "!filename!"=="!newfilename!" (
        echo Renaming File: !filename! -^> !newfilename!
        ren "!filepath!" "!newfilename!"
    )
)

:: Step B: Rename Folders (Deepest paths first via 'sort /r' so paths don't break)
for /f "delims=" %%D in ('dir "%target_dir%\*%search%*" /b /s /a:d 2^>nul ^| sort /r') do (
    set "dirpath=%%D"
    set "dirname=%%~nxD"
    
    call set "newdirname=%%dirname:%search%=%replace%%%"
    
    if not "!dirname!"=="!newdirname!" (
        echo Renaming Folder: !dirname! -^> !newdirname!
        ren "!dirpath!" "!newdirname!"
    )
)
goto :eof
