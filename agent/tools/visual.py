import os
import time
import subprocess
from typing import Dict, Any, Optional

class VisualTool:
    """
    A tool that provides 'Visual Feedback' similar to Replit.
    It takes a screenshot of a URL and captures the last N lines of a log file.
    This gives the agent immediate context on 'what the user sees' and 'what the server says'.
    """

    def quick_visual_check(self, url: str, log_file_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Captures a screenshot of the URL and reads the tail of the log file.
        Returns a dict with paths to the screenshot and the log content.
        """
        result = {
            "url": url,
            "screenshot_path": None,
            "logs": None,
            "error": None
        }

        # 1. Capture Screenshot using Playwright (via CLI for simplicity or python api)
        # We will use the existing BrowserTester logic if available, or a simple subprocess wrapper
        # For simplicity in this tool, let's assume we can run a python one-liner or simple script.
        
        screenshot_filename = f"screenshot_{int(time.time())}.png"
        screenshot_path = os.path.abspath(screenshot_filename)
        
        try:
            # We prefer using the python API directly if possible, but let's write a temporary script
            # to avoid complex import issues if playwright isn't fully set up in this process.
            # Actually, let's try to import playwright here.
            from playwright.sync_api import sync_playwright
            
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                try:
                    page.goto(url, timeout=10000) # 10s timeout
                    # Wait a bit for rendering
                    page.wait_for_timeout(2000) 
                    page.screenshot(path=screenshot_path)
                    result["screenshot_path"] = screenshot_path
                except Exception as e:
                    result["error"] = f"Screenshot failed: {str(e)}"
                finally:
                    browser.close()

        except ImportError:
            result["error"] = "Playwright not installed. Cannot take screenshot."
        except Exception as e:
            result["error"] = f"Unexpected error: {str(e)}"

        # 2. Read Log File
        if log_file_path:
            if os.path.exists(log_file_path):
                try:
                    # Read last 50 lines
                    # Equivalent to tail -n 50
                    with open(log_file_path, "r") as f:
                        lines = f.readlines()
                        result["logs"] = "".join(lines[-50:])
                except Exception as e:
                    result["logs"] = f"Error reading log file: {str(e)}"
            else:
                result["logs"] = f"Log file not found: {log_file_path}"
        
        return result

if __name__ == "__main__":
    # Test run
    tool = VisualTool()
    print("Testing Visual Tool...")
    # Assuming something is running on localhost:8000 or httpbin
    # We can test with a public url like google.com for now
    res = tool.quick_visual_check("https://google.com")
    print(res)
    if res["screenshot_path"]:
        print(f"Screenshot saved to {res['screenshot_path']}")
        # Clean up
        os.remove(res["screenshot_path"])
