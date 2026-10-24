@echo off
rem Draws the session key levels (PWH/PWL, PDH/PDL, Asia, London, NY AM highs/lows, midnight open) on the ACTIVE cTrader chart.
rem Click inside the chart first, then double-click this file.  Other index:  draw-levels.cmd US30
cd /d "%~dp0"
set SYM=%1
if "%SYM%"=="" set SYM=NAS100
"%LOCALAPPDATA%\ictscan\venv\Scripts\python.exe" -m ictscan.draw_levels %SYM%
echo.
pause