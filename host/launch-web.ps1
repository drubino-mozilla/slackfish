# Slackfish Web UI launcher.
# Opens a loading page instantly, starts the server in the background.

$hostDir = $PSScriptRoot
Start-Process "C:\Python314\pythonw.exe" -ArgumentList (Join-Path $hostDir "slackfish_web.py") -WindowStyle Hidden
Start-Process (Join-Path $hostDir "static\loading.html")
