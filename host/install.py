"""Install/uninstall the Slackfish native messaging host on Windows.

Usage:
    python install.py          # Install
    python install.py --remove # Uninstall
"""

import json
import os
import sys
import winreg

HOST_NAME = "slackfish"
REG_PATH = rf"Software\Mozilla\NativeMessagingHosts\{HOST_NAME}"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(SCRIPT_DIR, "slackfish.json")
BAT_PATH = os.path.join(SCRIPT_DIR, "slackfish.bat")


def install():
    manifest = {
        "name": HOST_NAME,
        "description": "Slackfish native messaging host",
        "path": BAT_PATH,
        "type": "stdio",
        "allowed_extensions": ["slackfish@drubino-mozilla"],
    }
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote manifest: {MANIFEST_PATH}")

    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH)
    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, MANIFEST_PATH)
    winreg.CloseKey(key)
    print(f"Registry key set: HKCU\\{REG_PATH} -> {MANIFEST_PATH}")
    print("Install complete. Restart Firefox to pick up native messaging host.")


def uninstall():
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_PATH)
        print(f"Registry key removed: HKCU\\{REG_PATH}")
    except FileNotFoundError:
        print("Registry key not found (already removed)")
    print("Uninstall complete.")


if __name__ == "__main__":
    if "--remove" in sys.argv:
        uninstall()
    else:
        install()
