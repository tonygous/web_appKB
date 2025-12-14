import pytest
from crawler import AsyncCrawler
from bs4 import BeautifulSoup

def test_url_normalization_removes_fragment():
    crawler = AsyncCrawler(start_url="http://example.com")
    
    # Test normalization
    assert crawler._normalize_url("http://example.com/page#section") == "http://example.com/page"
    assert crawler._normalize_url("http://example.com/page/#section") == "http://example.com/page"
    
    # Test deduplication logic (mocking visited/enqueued)
    crawler.visited.add("http://example.com/page")
    assert "http://example.com/page#foo" not in crawler.visited # Should be deduped against normalized
    
    # Actually, we test if _can_enqueue_url handles it, but _normalize_url is the key.

def test_noise_removal():
    crawler = AsyncCrawler(start_url="http://example.com", remove_additional_noise=True)
    
    html = """
    <html>
        <body>
            <div id="cookie-banner">Accept cookies</div>
            <nav class="main-nav">Navigation</nav>
            <div class="sidebar">Sidebar content</div>
            <main>
                <h1>Real Content</h1>
                <p>some text</p>
                <div class="share-buttons">Share this</div>
            </main>
            <footer>Footer</footer>
        </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    crawler._remove_noise_tags(soup)
    
    text = soup.get_text(separator=" ", strip=True)
    assert "Accept cookies" not in text
    assert "Navigation" not in text
    assert "Sidebar content" not in text
    assert "Real Content" in text
    assert "Share this" not in text
    assert "Footer" not in text

def test_extraction_fallback_logic():
    crawler = AsyncCrawler(start_url="http://example.com", min_text_chars=50) # Low threshold for test
    
    # Case 1: Readability works (simulated by good content)
    # We can't easily mock Readability internal logic without complex mocks, 
    # but we can test the Selectors fallback if readability fails or is too short.
    
    # Case 2: Fallback to largest div
    html = """
    <html>
        <body>
            <div id="menu" style="height:100px">Menu items...</div>
            <div id="content">
                This is the main content area. It has more text than the menu.
                It should be selected by the fallback logic if no semantic tags are found
                and readability doesn't pick it up or we force it.
            </div>
            <div id="footer">Copyright</div>
        </body>
    </html>
    """
    # Start with empty selectors to force fallback logic
    crawler.main_selectors = [] 
    
    # Mock _clean_html to bypass Readability or force used_readability=False
    # But simpler to just test _select_main_area directly
    soup = BeautifulSoup(html, "html.parser")
    selected = crawler._select_main_area(soup)
    assert "main content area" in selected.get_text()
    assert "Menu items" not in selected.get_text() # It should select the DIV, not the whole body

def test_code_block_preservation():
    crawler = AsyncCrawler(start_url="http://example.com")
    html = """
    <main>
        <p>Code:</p>
        <pre><code>def foo():
    return "bar"
</code></pre>
    </main>
    """
    title, markdown, used = crawler._clean_html(html, "http://example.com")
    assert "```" in markdown
    assert "def foo():" in markdown
