# info_search.py
# 整合新闻搜索和学术搜索，供主应用调用
import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin
import time
from collections import defaultdict
import xml.etree.ElementTree as ET

# ========== 公共：精确词匹配 ==========
def contains_word(text, keyword):
    if not keyword or not text:
        return False
    pattern = rf'\b{re.escape(keyword)}\b'
    return re.search(pattern, text, re.IGNORECASE) is not None

# ========== 公共：布尔查询匹配器 ==========
def matches_boolean_query(title: str, query: str) -> bool:
    if not query or not query.strip():
        return True
    query = query.strip()
    title_lower = title.lower()
    
    def check_term(term: str) -> bool:
        term = term.strip()
        if term.startswith('"') and term.endswith('"'):
            phrase = term[1:-1].lower()
            return phrase in title_lower
        else:
            return contains_word(title, term)
    
    def parse_and_evaluate(expr: str) -> bool:
        expr = expr.strip()
        while '(' in expr:
            start = expr.rfind('(')
            end = expr.find(')', start)
            if start == -1 or end == -1:
                break
            inner = expr[start+1:end]
            inner_result = parse_and_evaluate(inner)
            expr = expr[:start] + str(inner_result) + expr[end+1:]
        
        parts = re.split(r'\bNOT\b', expr, flags=re.IGNORECASE)
        if len(parts) > 1:
            right_result = parse_and_evaluate(parts[1])
            return parse_and_evaluate(parts[0]) and not right_result
        
        and_parts = re.split(r'\bAND\b', expr, flags=re.IGNORECASE)
        if len(and_parts) > 1:
            and_results = [parse_and_evaluate(part) for part in and_parts]
            return all(and_results)
        
        or_parts = re.split(r'\bOR\b', expr, flags=re.IGNORECASE)
        if len(or_parts) > 1:
            or_results = [parse_and_evaluate(part) for part in or_parts]
            return any(or_results)
        
        expr = expr.strip()
        if expr in ['True', 'False']:
            return expr == 'True'
        if expr:
            return check_term(expr)
        return False
    
    try:
        return parse_and_evaluate(query)
    except Exception:
        words = re.findall(r'[^\s()]+', query)
        return any(contains_word(title, w) for w in words if w.upper() not in ['AND', 'OR', 'NOT'])

def parse_query_to_matcher(query: str):
    if not query or not query.strip():
        return lambda title: True
    query = query.strip()
    if re.search(r'\b(AND|OR|NOT)\b', query, re.IGNORECASE):
        return lambda title: matches_boolean_query(title, query)
    else:
        keywords = [k.strip() for k in query.split() if k.strip()]
        if len(keywords) == 1:
            return lambda title: contains_word(title, keywords[0])
        else:
            return lambda title: any(contains_word(title, kw) for kw in keywords)

# ========== 新闻搜索部分 ==========
NEWS_SOURCES = {
    "Sumi News": {"url": "https://sumi.news", "parser": "parse_sumi"},
    "68k News": {"url": "http://68k.news", "parser": "parse_68k"},
    "ReadSpike": {"url": "https://readspike.com", "parser": "parse_readspike"},
    "HackURLs": {"url": "https://hackurls.com", "parser": "parse_hackurls"},
    "FinURLs": {"url": "https://finurls.com", "parser": "parse_finurls"},
    "TechURLs": {"url": "https://techurls.com", "parser": "parse_techurls"},
    "Spike News": {"url": "https://spike.news", "parser": "parse_spike"},
    "Hacker Herald": {"url": "https://hackerherald.com", "parser": "parse_hackerherald"},
    "SciURLs": {"url": "https://sciurls.com", "parser": "parse_sciurls"}
}

def request_with_retry(url, timeout=15, retries=3, delay=2):
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=timeout, headers=headers)
            if resp.status_code == 429:
                wait = delay * (attempt + 1) * 2
                st.warning(f"Rate limited, waiting {wait} seconds...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
    raise Exception("Max retries exceeded")

# 新闻解析函数
def parse_sumi(html, base_url):
    news_list = []
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a', href=True):
        title = link.get_text(strip=True)
        href = link.get('href')
        if title and 15 < len(title) < 200:
            if not any(skip in title.lower() for skip in ['sign in', 'search', 'following', 'reorder']):
                full_url = urljoin(base_url, href) if href.startswith('/') else href
                if not any(item['title'] == title for item in news_list):
                    news_list.append({"title": title, "link": full_url, "source": "Sumi News"})
    return news_list

def parse_68k(html, base_url):
    news_list = []
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a', href=True):
        title = link.get_text(strip=True)
        href = link.get('href')
        if title and len(title) > 10 and not title.startswith('Posted'):
            if not any(skip in title.lower() for skip in ['us edition', 'change']):
                full_url = urljoin(base_url, href)
                if not any(item['title'] == title for item in news_list):
                    news_list.append({"title": title, "link": full_url, "source": "68k News"})
    return news_list

def parse_readspike(html, base_url):
    news_list = []
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a', href=True):
        title = link.get_text(strip=True)
        href = link.get('href')
        if title and 15 < len(title) < 150:
            if not any(skip in title.lower() for skip in ['home', 'login', 'sign', 'subscribe', 'search', 'today\'s', 'deals']):
                full_url = urljoin(base_url, href) if href.startswith('/') else href
                if not any(item['title'] == title for item in news_list):
                    news_list.append({"title": title, "link": full_url, "source": "ReadSpike"})
    return news_list

def parse_hackurls(html, base_url):
    news_list = []
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a', href=True):
        title = link.get_text(strip=True)
        href = link.get('href')
        if title and len(title) > 10 and href and not href.startswith('#'):
            full_url = urljoin(base_url, href) if href.startswith('/') else href
            if not any(item['title'] == title for item in news_list):
                news_list.append({"title": title, "link": full_url, "source": "HackURLs"})
    return news_list

def parse_finurls(html, base_url):
    news_list = []
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a', href=True):
        title = link.get_text(strip=True)
        href = link.get('href')
        if title and len(title) > 15 and href:
            full_url = urljoin(base_url, href) if href.startswith('/') else href
            if not any(item['title'] == title for item in news_list):
                news_list.append({"title": title, "link": full_url, "source": "FinURLs"})
    return news_list

def parse_techurls(html, base_url):
    news_list = []
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a', href=True):
        title = link.get_text(strip=True)
        href = link.get('href')
        if title and len(title) > 15 and href:
            full_url = urljoin(base_url, href) if href.startswith('/') else href
            if not any(item['title'] == title for item in news_list):
                news_list.append({"title": title, "link": full_url, "source": "TechURLs"})
    return news_list

def parse_spike(html, base_url):
    news_list = []
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a', href=True):
        title = link.get_text(strip=True)
        href = link.get('href')
        if title and 15 < len(title) < 200:
            if not any(skip in title.lower() for skip in ['show more']):
                full_url = urljoin(base_url, href) if href.startswith('/') else href
                if not any(item['title'] == title for item in news_list):
                    news_list.append({"title": title, "link": full_url, "source": "Spike News"})
    return news_list

def parse_hackerherald(html, base_url):
    news_list = []
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a', href=True):
        title = link.get_text(strip=True)
        href = link.get('href')
        if title and len(title) > 15 and href:
            full_url = urljoin(base_url, href) if href.startswith('/') else href
            if not any(item['title'] == title for item in news_list):
                news_list.append({"title": title, "link": full_url, "source": "Hacker Herald"})
    return news_list

def parse_sciurls(html, base_url):
    news_list = []
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a', href=True):
        title = link.get_text(strip=True)
        href = link.get('href')
        if title and len(title) > 15 and href:
            full_url = urljoin(base_url, href) if href.startswith('/') else href
            if not any(item['title'] == title for item in news_list):
                news_list.append({"title": title, "link": full_url, "source": "SciURLs"})
    return news_list

def fetch_news_from_source(source_name, source_config, matcher_func, timeout=10):
    try:
        response = requests.get(source_config["url"], timeout=timeout, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        response.raise_for_status()
        parser_name = source_config["parser"]
        if parser_name == "parse_sumi":
            news_items = parse_sumi(response.text, source_config["url"])
        elif parser_name == "parse_68k":
            news_items = parse_68k(response.text, source_config["url"])
        elif parser_name == "parse_readspike":
            news_items = parse_readspike(response.text, source_config["url"])
        elif parser_name == "parse_hackurls":
            news_items = parse_hackurls(response.text, source_config["url"])
        elif parser_name == "parse_finurls":
            news_items = parse_finurls(response.text, source_config["url"])
        elif parser_name == "parse_techurls":
            news_items = parse_techurls(response.text, source_config["url"])
        elif parser_name == "parse_spike":
            news_items = parse_spike(response.text, source_config["url"])
        elif parser_name == "parse_hackerherald":
            news_items = parse_hackerherald(response.text, source_config["url"])
        elif parser_name == "parse_sciurls":
            news_items = parse_sciurls(response.text, source_config["url"])
        else:
            news_items = []
        filtered = [item for item in news_items if matcher_func(item["title"])]
        return filtered
    except Exception as e:
        st.error(f"爬取 {source_name} 失败: {str(e)[:100]}")
        return []

def fetch_all_news(matcher_func):
    all_news = []
    progress_bar = st.progress(0)
    total = len(NEWS_SOURCES)
    for i, (name, config) in enumerate(NEWS_SOURCES.items()):
        st.info(f"正在爬取: {name}...")
        news = fetch_news_from_source(name, config, matcher_func)
        all_news.extend(news)
        progress_bar.progress((i + 1) / total)
        time.sleep(0.5)
    progress_bar.empty()
    return all_news

# ========== 学术搜索部分 ==========
ACADEMIC_SOURCES = {
    "arXiv (Latest Papers)": {
        "url": "https://arxiv.org",
        "parser": "parse_arxiv",
        "type": "api"
    },
    "Hugging Face Papers (Trending)": {
        "url": "https://huggingface.co/papers/trending",
        "parser": "parse_huggingface",
        "type": "scrape"
    },
    "Emergent Mind (AI Papers)": {
        "url": "https://www.emergentmind.com",
        "parser": "parse_emergentmind",
        "type": "scrape"
    },
    "Connected Papers (Graph)": {
        "url": "https://www.connectedpapers.com",
        "parser": "parse_connectedpapers",
        "type": "link_only"
    },
    "BAAI Paper Hub": {
        "url": "https://hub.baai.ac.cn/papers",
        "parser": "parse_baai",
        "type": "link_only"
    }
}

def parse_arxiv(base_url, limit=20):
    papers = []
    try:
        query = "cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL"
        url = f"http://export.arxiv.org/api/query?search_query={query}&sortBy=submittedDate&sortOrder=descending&max_results={limit}"
        resp = request_with_retry(url, timeout=15, retries=3, delay=3)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            ns = {'arxiv': 'http://www.w3.org/2005/Atom'}
            for entry in root.findall('arxiv:entry', ns):
                title = entry.find('arxiv:title', ns).text.strip()
                id_elem = entry.find('arxiv:id', ns)
                paper_id = id_elem.text.split('/')[-1] if id_elem else ""
                link = f"https://arxiv.org/abs/{paper_id}" if paper_id else base_url
                papers.append({"title": title, "link": link, "source": "arXiv (Latest Papers)"})
        else:
            st.error(f"arXiv API returned status {resp.status_code}")
    except Exception as e:
        st.error(f"Failed to fetch arXiv: {str(e)[:150]}")
    return papers

def parse_huggingface(base_url, limit=20):
    papers = []
    try:
        api_url = "https://huggingface.co/api/daily_papers"
        resp = request_with_retry(api_url, timeout=10, retries=2, delay=1)
        if resp.status_code == 200:
            data = resp.json()
            for item in data[:limit]:
                title = item.get("paper", {}).get("title", "No title")
                pid = item.get("paper", {}).get("id", "")
                link = f"https://huggingface.co/papers/{pid}" if pid else base_url
                papers.append({"title": title, "link": link, "source": "Hugging Face Papers (Trending)"})
        else:
            papers.append({"title": "Visit Hugging Face Daily Papers", "link": base_url, "source": "Hugging Face Papers (Trending)"})
    except Exception as e:
        st.error(f"Failed to fetch Hugging Face: {str(e)[:100]}")
    return papers

def parse_emergentmind(base_url, limit=20):
    papers = []
    try:
        resp = request_with_retry(base_url, timeout=10, retries=2, delay=1)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            for link in soup.select('article h2 a'):
                title = link.get_text(strip=True)
                href = link.get('href')
                if title and len(title) > 10:
                    full_url = urljoin(base_url, href) if href else base_url
                    papers.append({"title": title, "link": full_url, "source": "Emergent Mind (AI Papers)"})
                    if len(papers) >= limit:
                        break
        if not papers:
            papers.append({"title": "Visit Emergent Mind for latest AI papers", "link": base_url, "source": "Emergent Mind (AI Papers)"})
    except Exception as e:
        st.error(f"Failed to fetch Emergent Mind: {str(e)[:100]}")
    return papers

def parse_connectedpapers(base_url, limit=1):
    return [{"title": "Go to Connected Papers, enter paper title or DOI to generate graph", "link": base_url, "source": "Connected Papers (Graph)"}]

def parse_baai(base_url, limit=1):
    return [{"title": "Visit BAAI Paper Hub for latest Chinese AI papers", "link": base_url, "source": "BAAI Paper Hub"}]

def fetch_academic_source(source_name, source_config, matcher_func):
    try:
        parser = source_config["parser"]
        base_url = source_config["url"]
        if parser == "parse_arxiv":
            items = parse_arxiv(base_url)
        elif parser == "parse_huggingface":
            items = parse_huggingface(base_url)
        elif parser == "parse_emergentmind":
            items = parse_emergentmind(base_url)
        elif parser == "parse_connectedpapers":
            items = parse_connectedpapers(base_url)
        elif parser == "parse_baai":
            items = parse_baai(base_url)
        else:
            items = []
        if source_config["type"] == "link_only":
            return items
        filtered = [item for item in items if matcher_func(item["title"])]
        return filtered
    except Exception as e:
        st.error(f"Failed to fetch {source_name}: {str(e)[:100]}")
        return []

def fetch_all_academic(matcher_func):
    all_items = []
    progress_bar = st.progress(0)
    total = len(ACADEMIC_SOURCES)
    for i, (name, config) in enumerate(ACADEMIC_SOURCES.items()):
        st.info(f"Fetching: {name}...")
        items = fetch_academic_source(name, config, matcher_func)
        all_items.extend(items)
        progress_bar.progress((i + 1) / total)
        time.sleep(1)
    progress_bar.empty()
    return all_items

# ========== 统一展示函数 ==========
def show_info_search():
    """主应用中调用此函数即可显示信息搜索界面（新闻+学术切换）"""
    st.title("Information Search")
    st.markdown("Search news or academic papers using keywords and boolean queries.")

    # 选择搜索类型
    search_type = st.radio(
        "Select search source:",
        options=["News", "Academic Papers"],
        horizontal=True,
        format_func=lambda x: x
    )

    # 根据类型展示不同的帮助说明
    if search_type == "News":
        with st.expander("Boolean Query Syntax (News)", expanded=False):
            st.markdown("""
            **Basic Usage**
            - Single word: `AI`
            - Multiple words (OR): `AI Trump`
            
            **Boolean Operators (AND, OR, NOT)**
            - `AI AND Trump` : both terms
            - `AI OR Artificial` : either term
            - `AI NOT Dubai` : exclude term
            - `(AI OR Artificial) AND Trump`
            - `"climate change" AND AI`
            
            Note: Word-boundary matching (e.g., `AI` does not match `Dubai`). Case-insensitive.
            """)
        default_query = "AI"
    else:
        with st.expander("Boolean Query Syntax (Academic)", expanded=False):
            st.markdown("""
            **Basic Usage**
            - Single word: `transformer`
            - Multiple words (OR): `LLM agent`
            
            **Boolean Operators (AND, OR, NOT)**
            - `LLM AND agent`
            - `attention OR transformer`
            - `GPT NOT vision`
            - `(large language) AND alignment`
            
            Note: Connected Papers and BAAI Paper Hub are link-only sources, not filtered by keyword.
            """)
        default_query = "LLM"

    col1, col2 = st.columns([3, 1])
    with col1:
        keyword = st.text_input(
            "Enter keyword(s) or boolean expression",
            value=default_query,
            placeholder='e.g., AI  |  AI AND Trump  |  (AI OR Artificial) NOT Dubai'
        )
    with col2:
        st.markdown("##")
        search_button = st.button("Search", type="primary", use_container_width=True)

    if keyword and keyword.strip():
        st.caption(f"Current query: `{keyword.strip()}`")
        if re.search(r'\b(AND|OR|NOT)\b', keyword, re.IGNORECASE):
            st.caption("Mode: Boolean matching")
        elif len(keyword.strip().split()) > 1:
            st.caption("Mode: Multi-word OR matching")
        else:
            st.caption("Mode: Exact word matching")

    if search_button and keyword:
        matcher = parse_query_to_matcher(keyword)
        if search_type == "News":
            with st.spinner(f"Searching news for '{keyword}'..."):
                results = fetch_all_news(matcher)
            st.markdown("---")
            st.markdown(f"## News Results ({len(results)} items)")
            if results:
                grouped = defaultdict(list)
                for item in results:
                    grouped[item["source"]].append(item)
                for source_name, items in grouped.items():
                    with st.expander(f"{source_name} ({len(items)} items)", expanded=True):
                        for idx, item in enumerate(items, 1):
                            title = item["title"]
                            # 高亮关键词
                            keywords_to_highlight = re.findall(r'\b\w+\b', keyword)
                            for kw in keywords_to_highlight:
                                if kw.upper() not in ['AND', 'OR', 'NOT'] and len(kw) > 1:
                                    title = re.sub(rf'\b{re.escape(kw)}\b', f"**{kw}**", title, flags=re.IGNORECASE)
                            st.markdown(f"{idx}. {title}")
                            st.markdown(f"   [Read original]({item['link']})")
                            st.markdown("---")
            else:
                st.warning(f"No news found for '{keyword}'")
        else:  # 学术
            with st.spinner(f"Searching academic papers for '{keyword}'..."):
                results = fetch_all_academic(matcher)
            st.markdown("---")
            st.markdown(f"##Academic Results ({len(results)} items)")
            if results:
                grouped = defaultdict(list)
                for item in results:
                    grouped[item["source"]].append(item)
                for source_name, items in grouped.items():
                    with st.expander(f"{source_name} ({len(items)} items)", expanded=True):
                        for idx, item in enumerate(items, 1):
                            title = item["title"]
                            if source_name not in ["Connected Papers (Graph)", "BAAI Paper Hub"]:
                                keywords_to_highlight = re.findall(r'\b\w+\b', keyword)
                                for kw in keywords_to_highlight:
                                    if kw.upper() not in ['AND', 'OR', 'NOT'] and len(kw) > 1:
                                        title = re.sub(rf'\b{re.escape(kw)}\b', f"**{kw}**", title, flags=re.IGNORECASE)
                            st.markdown(f"{idx}. {title}")
                            if item['link'] and not item['link'].startswith('#'):
                                st.markdown(f"   [Read original]({item['link']})")
                            st.markdown("---")
            else:
                st.warning(f"No academic papers found for '{keyword}'")
    elif search_button and not keyword:
        st.warning("Please enter a keyword or query")

# 如果直接运行此文件，可以单独测试
if __name__ == "__main__":
    show_info_search()
