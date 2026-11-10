"""设置页「升级包跟踪数量」接线回归。

下拉值必须同时出现在 saveSettings 与 forceStopAndSave 两个保存字典里，
否则正常保存/强制停止任一入口都会把该设置写丢。
"""
import re
from pathlib import Path


def test_settings_page_has_track_limit_select_wired():
    html = (Path(__file__).parents[1] / 'src/web/templates/index.html').read_text(
        encoding='utf-8')
    assert 'id="cfg-pkg-track"' in html
    assert re.search(r'<select id="cfg-pkg-track" class="dd">', html)
    # saveSettings + forceStopAndSave 两处都要带上该字段
    assert html.count("pkg_track_limit:document.getElementById('cfg-pkg-track').value") == 2
    assert all(v in html for v in ('value="5"', 'value="3"', 'value="10"', 'value="all"'))
