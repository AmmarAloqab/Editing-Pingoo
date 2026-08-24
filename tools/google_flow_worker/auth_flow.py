from __future__ import annotations

import os
import shutil

from playwright.sync_api import sync_playwright

from .config import get_config, ensure_runtime_dirs
from .flow_browser import GoogleFlowBrowser


def main() -> None:
    config = get_config()
    ensure_runtime_dirs(config)

    if not os.environ.get("DISPLAY"):
        print("FLOW_AUTH_REQUIRES_INTERACTIVE_LOGIN=YES")
        print(
            "AUTH_COMMAND=cd /opt/MoneyPrinterTurbo && "
            "/opt/pingoo-google-flow/venv/bin/python -m tools.google_flow_worker.auth_flow"
        )
        return

    executable = (
        config.browser_executable
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
    )

    with sync_playwright() as playwright:
        kwargs = {
            "user_data_dir": str(config.profile_dir),
            "headless": False,
            "accept_downloads": True,
            "downloads_path": str(config.downloads_dir),
            "viewport": {"width": 1280, "height": 900},
        }
        if executable:
            kwargs["executable_path"] = executable
        context = playwright.chromium.launch_persistent_context(**kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(config.flow_url, wait_until="domcontentloaded", timeout=60000)
        print("Login in opened browser. Press Enter here after Google Flow loads.")
        input()
        authenticated = GoogleFlowBrowser(config)._is_authenticated(page)
        context.close()

    if authenticated:
        print("FLOW_AUTH_SESSION=READY")
    else:
        print("FLOW_AUTH_SESSION=AUTH_REQUIRED")


if __name__ == "__main__":
    main()
