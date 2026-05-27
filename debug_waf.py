#!/usr/bin/env python3
"""Debug: trace WAF discover and find where '>' comes from"""
import sys, re, json
sys.path.insert(0, '/root/nsfocus-monitor')

# Simulate the exact regex from nsfocus.py line 160
BASE_URL = 'https://update.nsfocus.com'

import urllib.request
url = BASE_URL + '/update/wafIndex'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=15) as r:
    html = r.read().decode('utf-8', errors='replace')

print(f'HTML length: {len(html)}')
print(f'ser_c_b_tit in HTML: {html.count("ser_c_b_tit")}')
print()

# The exact regex from nsfocus.py:160
pattern = r"ser_c_b_tit['\">]\s*([^<]+?)\s*</div>"
matches = list(re.finditer(pattern, html))
print(f'Regex matches: {len(matches)}')

# Show what the current live HTML actually has that could produce '>'
# Look for the WAF section text in the HTML
for keyword in ['WEB应用防护系统(WAF)', 'WEB应用防护系统(WAF)列表', 'WAF', 'ser_c', '左侧', '产品分类']:
    count = html.count(keyword)
    if count > 0:
        idx = html.find(keyword)
        print(f'\n"{keyword}" found {count}x, first at {idx}:')
        print(f'  ...{html[max(0,idx-50):idx+100]}...')

print('\n--- Now simulate the discover_package_types flow ---')

# Simulate section_titles extraction
section_titles = {}
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

print(f'section_titles extracted: {len(section_titles)}')
for k, v in list(section_titles.items())[:5]:
    print(f'  {k!r} -> {v!r}')

# Now look at what the entry HTML actually contains
# that might give us section-title-like text
print('\n--- Searching entry HTML for section-like markers ---')

# Check if there's any ">" character near Chinese text in a div
for m in re.finditer(r'>([^<]{3,30})</div>', html):
    text = m.group(1).strip()
    if text and any('\u4e00' <= c <= '\u9fff' for c in text):
        if '>' in text or any('\u4e00' <= c <= '\u9fff' for c in text[:3]):
            print(f'  div text: {text!r}')