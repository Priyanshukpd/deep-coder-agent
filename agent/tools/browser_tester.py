"""
Browser Tester Tool.
Provides capabilities to verifying web applications running locally.
Now matches 'Devin-like' capabilities with interaction (click, type, wait).
"""
import logging
import time
import base64
from typing import Dict, Optional, Any, List

logger = logging.getLogger(__name__)

class BrowserTester:
    def __init__(self):
        self.has_playwright = False
        try:
            from playwright.sync_api import sync_playwright
            self.has_playwright = True
        except ImportError:
            pass

    def check_url(self, url: str) -> Dict[str, Any]:
        """Check if a URL is accessible and return basic info."""
        if self.has_playwright:
             return self._check_playwright(url)
        else:
             return self._check_requests(url)

    def _check_requests(self, url: str) -> Dict[str, Any]:
        import urllib.request
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return {
                    "status": response.getcode(),
                    "url": response.geturl(),
                    "content_length": response.headers.get("Content-Length"),
                    "title": "(Install playwright for title extraction)",
                    "ok": 200 <= response.getcode() < 300
                }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _check_playwright(self, url: str) -> Dict[str, Any]:
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                response = page.goto(url, timeout=10000)
                title = page.title()
                content = page.content()
                status = response.status if response else 0
                browser.close()
                return {
                    "status": status,
                    "url": url,
                    "title": title,
                    "ok": 200 <= status < 300,
                    "content_snippet": content[:500]
                }
        except Exception as e:
             logger.warning(f"Playwright check failed: {e}")
             return self._check_requests(url) # Fallback

    def take_screenshot(self, url: str, code_path: str) -> Optional[str]:
        """Take a screenshot if playwright is available."""
        if not self.has_playwright:
            return None
            
        from playwright.sync_api import sync_playwright
        try:
            screenshot_path = f"{code_path}_screenshot.png"
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(url)
                page.screenshot(path=screenshot_path)
                browser.close()
            return screenshot_path
        except Exception:
            return None

    def take_screenshot_base64(self, url: str) -> Optional[str]:
        """Take a screenshot and return as base64 string (for LLM analysis)."""
        if not self.has_playwright:
            return None

        from playwright.sync_api import sync_playwright
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                try:
                    page.goto(url, timeout=10000)
                except Exception:
                    pass # Try to capture anyway
                
                screenshot_bytes = page.screenshot()
                browser.close()
                return base64.b64encode(screenshot_bytes).decode('utf-8')
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return None

    def perform_interaction(self, url: str, actions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Execute a sequence of actions on a page.
        Actions schema:
        [
            {"type": "fill", "selector": "#user", "value": "admin"},
            {"type": "click", "selector": "#login"},
            {"type": "wait", "duration": 2000},
            {"type": "screenshot", "path": "after_login.png"}
        ]
        """
        if not self.has_playwright:
            return {"error": "Playwright not installed."}

        from playwright.sync_api import sync_playwright
        logs = []
        result = {"success": True, "logs": logs, "final_url": None, "title": None}

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                # Create context to hold cookies/session if needed (default is incognito-like)
                context = browser.new_context()
                page = context.new_page()
                
                logger.info(f"Navigating to {url}...")
                page.goto(url, timeout=15000)
                logs.append(f"Navigated to {url}")

                for i, action in enumerate(actions):
                    act_type = action.get("type")
                    selector = action.get("selector")
                    
                    try:
                        if act_type == "fill":
                            val = action.get("value", "")
                            page.fill(selector, val)
                            logs.append(f"Filled '{selector}' with '***'")
                        
                        elif act_type == "click":
                            page.click(selector)
                            logs.append(f"Clicked '{selector}'")
                        
                        elif act_type == "wait":
                            ms = action.get("duration", 1000)
                            page.wait_for_timeout(ms)
                            logs.append(f"Waited {ms}ms")
                        
                        elif act_type == "evaluate":
                            script = action.get("script")
                            res = page.evaluate(script)
                            logs.append(f"Evaluated script. Result: {str(res)[:50]}...")
                            
                        elif act_type == "screenshot":
                            path = action.get("path", "screenshot.png")
                            page.screenshot(path=path)
                            logs.append(f"Saved screenshot to {path}")
                            if "screenshots" not in result:
                                result["screenshots"] = []
                            result["screenshots"].append(path)

                        elif act_type == "wait_for_selector":
                            page.wait_for_selector(selector, timeout=action.get("timeout", 5000))
                            logs.append(f"Found selector '{selector}'")

                    except Exception as e:
                        logs.append(f"❌ Error at step {i} ({act_type}): {str(e)}")
                        result["success"] = False
                        break # Stop on error
                
                # Capture final state
                result["final_url"] = page.url
                result["title"] = page.title()
                result["content_snippet"] = page.content()[:500]
                
                browser.close()

        except Exception as e:
            return {"error": str(e), "logs": logs}
        
        return result
