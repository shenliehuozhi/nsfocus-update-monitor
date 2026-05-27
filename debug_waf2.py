#!/usr/bin/env python3
"""Debug: trace exact data flow for WAF discover"""
import sys, re, json
sys.path.insert(0, '/root/nsfocus-monitor')

BASE_URL = 'https://update.nsfocus.com'
import urllib.request

# --- Step 1: fetch WAF entry page ---
url = BASE_URL + '/update/wafIndex'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=15) as r:
    html = r.read().decode('utf-8', errors='replace')

print(f'[1] Fetched wafIndex HTML: {len(html)} bytes')
print(f'[1] ser_c_b_tit count: {html.count("ser_c_b_tit")}')

# --- Step 2: extract section titles (exact nsfocus.py code) ---
section_titles = {}
pattern = r"ser_c_b_tit['\">]\s*([^<]+?)\s*</div>"
for sec_match in re.finditer(pattern, html):
    raw = sec_match.group(1)
    sec_title = raw.strip().lstrip('>')
    if sec_title:
        sec_start = sec_match.end()
        next_sec = html.find("ser_c_b_tit", sec_start)
        sec_html = html[sec_start:next_sec if next_sec > 0 else len(html)]
        for link_match in re.finditer(r'<a href=["\']([^"\']+)["\']\s*>', sec_html):
            link_url = link_match.group(1).strip()
            if link_url and not link_url.startswith('#'):
                section_titles[link_url] = sec_title

print(f'[2] section_titles extracted: {len(section_titles)}')
for k, v in list(section_titles.items())[:3]:
    print(f'    {k!r} -> {v!r}')

# --- Step 3: extract top links ---
def extract_content_links(html_text):
    links = []
    for m in re.finditer(r'<a\s+href=["\']([^"\']+)["\']\s*>', html_text):
        href = m.group(1).strip()
        start = m.end()
        end = html_text.find('</a>', start)
        if end > 0:
            text = html_text[start:end].strip()
            text = re.sub(r'<[^>]+>', '', text).strip()
            if text:
                links.append((text, href))
    return links

def is_sidebar_link(u):
    return any(x in u for x in ['#', 'javascript', '/update/listLic', '/update/upLic',
                                '/update/listDas', '/update/listSash', '/update/listHwaf',
                                '/update/listNfSseIndex', '/update/ListNf', '/update/ListNfVpnIndex'])

top_links = [(t.strip(), u) for t, u in extract_content_links(html)
             if not is_sidebar_link(u)]

print(f'[3] top_links extracted: {len(top_links)}')
for t, u in top_links[:5]:
    print(f'    {t!r} -> {u!r}')

# --- Step 4: simulate chain building for first link ---
if top_links:
    top_text, top_url = top_links[0]
    sec_title = section_titles.get(top_url, '')
    initial_chain = [sec_title, top_text] if sec_title else [top_text]
    print(f'\n[4] First link chain: initial_chain={initial_chain}')
    print(f'    sec_title: {sec_title!r}')
    print(f'    top_text: {top_text!r}')

    # --- Step 5: check sub-page ---
    sub_url = BASE_URL + top_url
    req2 = urllib.request.Request(sub_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req2, timeout=15) as r2:
        sub_html = r2.read().decode('utf-8', errors='replace')

    title_match = re.search(r'<title>([^<]+)</title>', sub_html)
    if title_match:
        print(f'\n[5] Sub-page title: {title_match.group(1)!r}')
    print(f'[5] Sub-page ser_c_b_tit count: {sub_html.count("ser_c_b_tit")}')

    # Search for ">WEB" pattern in sub-page
    gt_matches = list(re.finditer(r'>WEB[^<]{0,50}', sub_html))
    print(f'[5] ">WEB" pattern in sub-page: {len(gt_matches)}')
    for m in gt_matches[:3]:
        print(f'      context: ...{sub_html[max(0,m.start()-20):m.end()+20]}...')

    # What about ">..." patterns anywhere?
    gt_all = list(re.finditer(r'>([^<>]{3,30})<', sub_html))
    chinese_gt = [m for m in gt_all if any('\u4e00' <= c <= '\u9fff' for c in m.group(1))]
    print(f'[5] Chinese text in ">"..."<" pattern: {len(chinese_gt)}')
    for m in chinese_gt[:5]:
        print(f'      {m.group(1)!r}')