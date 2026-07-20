from src.collectors.nsfocus import NsfocusCollector


def test_repeated_url_keeps_each_section_occurrence():
    html = """
    <div class="ser_c_b_tit">标准系列升级包列表</div>
    <div class="ser_c_b_con">
      <a href="/update/rule">系统规则库升级包</a>
      <a href="/update/standard-only">启发式病毒库升级包v2</a>
    </div>
    <div class="ser_c_b_tit">10000系列升级包列表</div>
    <div class="ser_c_b_con">
      <a href="/update/rule">系统规则库升级包</a>
      <a href="/update/10000-only">引擎升级包</a>
    </div>
    """

    collector = NsfocusCollector()
    links = collector._extract_content_links_with_sections(html)

    assert links == [
        ('系统规则库升级包', '/update/rule', '标准系列升级包列表'),
        ('启发式病毒库升级包v2', '/update/standard-only', '标准系列升级包列表'),
        ('系统规则库升级包', '/update/rule', '10000系列升级包列表'),
        ('引擎升级包', '/update/10000-only', '10000系列升级包列表'),
    ]