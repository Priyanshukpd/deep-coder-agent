"""
Documentation Crawler Tool.
Allows the agent to search for and read external documentation.
"""
import urllib.request
import urllib.parse
import re
import ssl
import json
from typing import List, Dict, Optional

class DocCrawler:
    def __init__(self):
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE
        
    def search(self, query: str, limit: int = 3) -> List[Dict[str, str]]:
        """
        Search for documentation using DuckDuckGo HTML (no API key needed).
        Returns list of {"title": str, "url": str, "snippet": str}
        """
        try:
            # Use DuckDuckGo HTML version
            encoded_query = urllib.parse.quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
            
            req = urllib.request.Request(
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (compatible; GodModeAgent/1.0)'}
            )
            
            with urllib.request.urlopen(req, context=self._ctx, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')
                
            results = []
            # Regex to parse DDG HTML results
            # Look for result__a class
            link_pattern = re.compile(r'<a class="result__a" href="([^"]+)">([^<]+)</a>')
            snippet_pattern = re.compile(r'<a class="result__snippet" href="[^"]+">([^<]+)</a>')
            
            links = link_pattern.findall(html)
            
            # DDG uses a redirect URL, need to unquote
            # e.g. //duckduckgo.com/l/?kh=-1&uddg=https%3A%2F%2Fexample.com
            
            for i, (href, title) in enumerate(links[:limit]):
                real_url = href
                if "uddg=" in href:
                    try:
                        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                        if 'uddg' in qs:
                            real_url = qs['uddg'][0]
                    except:
                        pass
                        
                results.append({
                    "title": title,
                    "url": real_url,
                    "snippet": "" # Snippet extraction is harder with regex, skipping for now
                })
                
            return results
        except Exception:
            return []

    def read_page(self, url: str) -> str:
        """Fetch and strip HTML from a documentation page."""
        try:
            req = urllib.request.Request(
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (compatible; GodModeAgent/1.0)'}
            )
            with urllib.request.urlopen(req, context=self._ctx, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')
                
            # Naive text extraction
            text = re.sub(r'<script.*?>.*?</script>', '', html, flags=re.DOTALL)
            text = re.sub(r'<style.*?>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            
            return text[:10000] # Limit size
        except Exception as e:
            return f"Failed to read page: {e}"

    def diagnose_error(self, error: str) -> str:
        """
        Search for an error message and return top result summary.
        Useful for ImportError, AttributeError, etc.
        """
        # Clean error for search query
        # Remove file paths, keep the message
        query = error.split('\n')[-1] # Last line usually has the exception
        if len(query) > 200:
             query = query[:200]
             
        results = self.search(query, limit=1)
        if not results:
            return "No documentation found."
            
        top_result = results[0]
        content = self.read_page(top_result['url'])
        
        return (
            f"🔎 Searched docs for: '{query}'\n"
            f"Found: {top_result['title']} ({top_result['url']})\n"
            f"Content Snippet:\n{content[:1000]}..."
        )
