pyinstaller --onefile --windowed --icon=favicon.ico --add-data "favicon.ico;." --name "CodexExtraLight" pc_client.py
rmdir /s /q build
del /q CodexExtraLight.spec
pause
