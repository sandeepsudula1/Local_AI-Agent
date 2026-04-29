# system_agent.py

import os
import subprocess
import platform
import shutil


def open_application(app_name):
    """
    Opens applications based on OS.
    Supports: Windows
    """
    app_name = app_name.lower()
    system = platform.system()

    if system == "Windows":
        return open_app_windows(app_name)

    return "Unsupported operating system."


def open_app_windows(app_name):
    """
    Opens common applications in a portable way.
    """

    # Dynamically resolve applications where possible
    vscode_path = shutil.which("code") or os.environ.get("VSCODE_PATH")

    apps = {
        "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "edge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "notepad": "notepad",
        "calculator": "calc",
        "vs code": vscode_path,   # ✅ FIXED (no hardcoded user path)
        "explorer": "explorer",
    }

    for key in apps:
        if key in app_name:
            try:
                path = apps[key]

                if not path:
                    return f"{key} is not installed or not found in PATH."

                subprocess.Popen(path)
                return f"Opening {key}..."

            except Exception as e:
                return f"Failed to open {key}: {str(e)}"

    return "I don't recognize that application. Please add it to system_agent.py"