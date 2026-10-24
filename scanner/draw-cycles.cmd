@echo off
rem Draws the 90-minute cycles on the ACTIVE cTrader chart. Click inside the chart first, then double-click this file.
rem Optional: drag-and-drop is not needed; to draw US30 or US500 run:  draw-cycles.cmd US30
cd /d "%~dp0"
set SYM=%1
if "%SYM%"=="" set SYM=NAS100
"%LOCALAPPDATA%\ictscan\venv\Scripts\python.exe" -m ictscan.draw_cycles %SYM% --boxes
"%LOCALAPPDATA%\ictscan\venv\Scripts\python.exe" -m ictscan.draw_cycles %SYM% --kinds 90m
echo.
pause