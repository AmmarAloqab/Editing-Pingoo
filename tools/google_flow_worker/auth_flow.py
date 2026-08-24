from __future__ import annotations

import os
import platform
import subprocess

from .config import get_config, ensure_runtime_dirs
from .windows_chrome import find_chrome_executable


def main() -> None:
    config = get_config()
    ensure_runtime_dirs(config)

    if platform.system().lower() != "windows":
        print("VPS_LOGIN_DISABLED=PASS")
        print(
            "WINDOWS_AUTH_COMMAND=powershell -ExecutionPolicy Bypass -File "
            ".\\tools\\google_flow_worker\\windows_auth.ps1"
        )
        return

    chrome = config.browser_executable or find_chrome_executable()
    if not chrome:
        print("LOCAL_CHROME_SUPPORTED=FAIL")
        raise SystemExit(1)

    subprocess.Popen(
        [
            chrome,
            f"--user-data-dir={config.profile_dir}",
            "--no-first-run",
            "--new-window",
            config.flow_url,
        ],
        close_fds=True,
    )
    print("LOCAL_CHROME_SUPPORTED=PASS")
    print(f"AUTH_PROFILE={config.profile_dir}")
    print("MANUAL_ONE_TIME_LOGIN=READY")
    print("FLOW_AUTH_SESSION=OPENED_IN_LOCAL_CHROME")


if __name__ == "__main__":
    main()
