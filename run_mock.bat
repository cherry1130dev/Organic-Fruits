@echo off
set "PATH=C:\Users\chara\python311;C:\Users\chara\python311\Scripts;%PATH%"
echo Running Mock ESP32 Simulation...
python tests\mock_esp32_client.py --condition random
pause
