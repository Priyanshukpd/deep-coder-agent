"""
Browser Tester Tool.
Provides capabilities to verifying web applications running locally.
"""
import logging
import time
from typing import Dict, Optional, Any

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
