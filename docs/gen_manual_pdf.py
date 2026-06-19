#!/usr/bin/env python3
"""
生成白眼（Byakugan）升级监控系统用户手册 PDF
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus.flowables import Flowable

# ── 配色 ──────────────────────────────────────────────
NAVY     = HexColor('#0d1b2a')
BLUE     = HexColor('#1a3a5c')
LIGHTBLUE= HexColor('#4fc3f7')
ORANGE   = HexColor('#ff7043')
GRAY     = HexColor('#607d8b')
LIGHTGRAY= HexColor('#eceff1')
GREEN    = HexColor('#26a69a')
RED      = HexColor('#e53935')

PAGE_W, PAGE_H = A4
MARGIN = 2*cm

# ── 中文字体 ──────────────────────────────────────────
# Font paths are uncertain; fall back to built-in
try:
    pdfmetrics.registerFont(TTFont('SimHei', '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'))
    pdfmetrics.registerFont(TTFont('SimSun', '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'))
    BASE_FONT = 'SimHei'
except Exception:
    BASE_FONT = 'Helvetica'

def font(name, size, color=black):
    return ('FONT', name, size, color)

class StyledDoc(SimpleDocTemplate):
    def __init__(self, filename, title, author='白眼系统', **kw):
        self.title_str = title
        super().__init__(filename, pagesize=A4,
                         leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=2.5*cm, bottomMargin=2.5*cm,
                         title=title, author=author, **kw)

def draw_cover(c, doc):
    c.saveState()
    # 深色背景条
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 6*cm, PAGE_W, 6*cm, fill=1, stroke=0)
    # 蓝色装饰条
    c.setFillColor(BLUE)
    c.rect(0, PAGE_H - 6.4*cm, PAGE_W, 0.4*cm, fill=1, stroke=0)
    # 标题
    c.setFillColor(white)
    c.setFont(BASE_FONT, 28)
    c.drawCentredString(PAGE_W/2, PAGE_H - 2.8*cm, '白眼升级监控系统')
    c.setFont(BASE_FONT, 14)
    c.setFillColor(LIGHTBLUE)
    c.drawCentredString(PAGE_W/2, PAGE_H - 3.8*cm, 'Byakugan Update Monitor — 用户操作手册')
    # 底部版本信息
    c.setFillColor(GRAY)
    c.setFont(BASE_FONT, 9)
    c.drawCentredString(PAGE_W/2, 1.5*cm, f'版本 1.7  |  绿盟升级监控系统  |  {doc.title_str}')
    c.restoreState()

def draw_page_bg(c, doc):
    c.saveState()
    c.setFillColor(LIGHTGRAY)
    c.rect(0, 0, PAGE_W, 0.8*cm, fill=1, stroke=0)
    c.setFillColor(GRAY)
    c.setFont(BASE_FONT, 8)
    c.drawString(MARGIN, 0.3*cm, f'白眼升级监控系统 · 用户手册')
    c.drawRightString(PAGE_W - MARGIN, 0.3*cm, f'第 {doc.page} 页')
    c.restoreState()

class Diagram(Flowable):
    """ASCII 风格流程图方框"""
    def __init__(self, lines, width=None, bg=HexColor('#1e2a38'), fg=white,
                 border=LIGHTBLUE, col1=HexColor('#263238'), col2=HexColor('#1a3a5c'),
                 box_radius=3):
        super().__init__()
        self.lines = lines
        self._width = width
        self.bg = bg; self.fg = fg
        self.border = border
        self.col1 = col1; self.col2 = col2
        self.box_radius = box_radius
        self.height = (len(lines) + 1) * 14 + 10

    def wrap(self, availWidth, availHeight):
        self.avail_w = availWidth
        return (self._width or availWidth, self.height)

    def draw(self):
        c = self.canv
        w = self.avail_w
        lh = 14
        # 外框
        c.setFillColor(self.bg)
        c.setStrokeColor(self.border)
        c.setLineWidth(1)
        c.roundRect(0, 0, w, self.height, self.box_radius, fill=1, stroke=1)
        # 列标题
        c.setFillColor(self.col2)
        c.roundRect(0, self.height - lh - 5, w, lh, self.box_radius, fill=1, stroke=0)
        # clip top corners
        c.setFillColor(self.col2)
        c.rect(0, self.height - lh - 5, w, lh, fill=0, stroke=0)
        # header text
        c.setFillColor(LIGHTBLUE)
        c.setFont(BASE_FONT, 8)
        c.drawString(8, self.height - lh + 2, '●  操作界面示意  ●')
        # 分隔线
        c.setStrokeColor(self.border)
        c.line(0, self.height - lh - 5, w, self.height - lh - 5)
        # 内容行
        for i, line in enumerate(self.lines):
            y = self.height - lh - 8 - (i+1)*lh
            if '::' in line:
                # 特殊格式：标题行
                c.setFillColor(NAVY)
                c.rect(0, y-1, w, lh, fill=1, stroke=0)
                c.setFillColor(LIGHTBLUE)
                c.setFont(BASE_FONT, 8)
                c.drawString(8, y+3, line.split('::')[1])
            elif line.startswith('  → '):
                c.setFillColor(ORANGE)
                c.setFont(BASE_FONT, 8)
                c.drawString(10, y+3, line)
            elif line.startswith('  ✓'):
                c.setFillColor(GREEN)
                c.setFont(BASE_FONT, 8)
                c.drawString(10, y+3, line)
            elif line.startswith('  !'):
                c.setFillColor(RED)
                c.setFont(BASE_FONT, 8)
                c.drawString(10, y+3, line)
            else:
                c.setFillColor(self.fg)
                c.setFont(BASE_FONT, 8)
                c.drawString(10, y+3, line)

class Box(Flowable):
    """纯色提示框"""
    def __init__(self, text, bg=HexColor('#1a3a5c'), fg=white,
                 icon='ℹ', border=LIGHTBLUE, width=None):
        super().__init__()
        self.text = text; self.bg = bg; self.fg = fg
        self.icon = icon; self.border = border
        self._width = width
        self.height = 0.9*cm

    def wrap(self, availWidth, availHeight):
        self.avail_w = self._width or availWidth
        return (self.avail_w, self.height)

    def draw(self):
        c = self.canv
        c.setFillColor(self.bg)
        c.setStrokeColor(self.border)
        c.setLineWidth(0.5)
        c.roundRect(0, 0, self.avail_w, self.height, 3, fill=1, stroke=1)
        c.setFillColor(self.border)
        c.setFont(BASE_FONT, 9)
        c.drawString(8, 3, f'{self.icon}  {self.text}')

# ── 样式 ──────────────────────────────────────────────
def make_styles():
    base = getSampleStyleSheet()
    s = {}
    s['title'] = ParagraphStyle('title', fontName=BASE_FONT, fontSize=20,
                                 textColor=NAVY, spaceAfter=8, leading=24)
    s['h1'] = ParagraphStyle('h1', fontName=BASE_FONT, fontSize=14,
                               textColor=NAVY, spaceBefore=16, spaceAfter=6,
                               leading=18, borderPad=0)
    s['h2'] = ParagraphStyle('h2', fontName=BASE_FONT, fontSize=12,
                               textColor=BLUE, spaceBefore=10, spaceAfter=4,
                               leading=16)
    s['body'] = ParagraphStyle('body', fontName=BASE_FONT, fontSize=9,
                                textColor=HexColor('#2c3e50'), leading=14,
                                spaceAfter=4)
    s['body_sm'] = ParagraphStyle('body_sm', fontName=BASE_FONT, fontSize=8,
                                   textColor=GRAY, leading=12, spaceAfter=3)
    s['code'] = ParagraphStyle('code', fontName='Courier', fontSize=7.5,
                                textColor=HexColor('#c0392b'), leading=12,
                                backColor=LIGHTGRAY, borderPad=4, spaceAfter=4)
    s['center'] = ParagraphStyle('center', fontName=BASE_FONT, fontSize=9,
                                  textColor=GRAY, alignment=TA_CENTER)
    return s

# ── 构建文档 ──────────────────────────────────────────
OUTPUT = '/root/nsfocus-monitor/docs/白眼用户手册.pdf'
doc = StyledDoc(OUTPUT, '白眼升级监控系统用户手册')
s = make_styles()

story = []

# ── 第一章：概述 ──────────────────────────────────────
story.append(Paragraph('第一章  系统概述', s['h1']))
story.append(HRFlowable(width='100%', thickness=1.5, color=NAVY))
story.append(Spacer(1, 6))
story.append(Paragraph(
    '白眼（Byakugan）升级监控系统是用于自动化监控绿盟产品升级包的 SaaS 平台。'
    '系统定期抓取绿盟升级页面，发现新版本升级包后，根据用户配置的订阅规则，'
    '通过企业微信、邮件等渠道推送升级通知。', s['body']))
story.append(Spacer(1, 4))
story.append(Paragraph('主要功能：', s['h2']))
for feat in [
    ('🔍', '自动采集', '定期/手动抓取绿盟升级页面，发现新增升级包'),
    ('📊', '汇总推送', '支持周/月/季度汇总模式，一次性推送多个升级包'),
    ('📡', '多渠道通知', '企业微信、钉钉、飞书、邮件等渠道'),
    ('🔐', '安全认证', '登录限流、Session 管理、审计日志'),
    ('📋', '订阅规则', '按产品/版本/包类型灵活筛选推送范围'),
]:
    story.append(Paragraph(f'{feat[0]} <b>{feat[1]}</b>：{feat[2]}', s['body']))
story.append(Spacer(1, 8))

# ── 第二章：首次登录 ──────────────────────────────────
story.append(Paragraph('第二章  首次登录', s['h1']))
story.append(HRFlowable(width='100%', thickness=1.5, color=NAVY))
story.append(Spacer(1, 6))

story.append(Paragraph('2.1  获取初始密码', s['h2']))
story.append(Paragraph(
    '系统首次启动时，会自动创建管理员账户 <b>admin</b>，密码打印在启动终端窗口。'
    '如果是在后台服务或 Docker 环境中运行，密码文件路径如下：', s['body']))
story.append(Spacer(1, 4))

story.append(Diagram([
    '  ::首次启动密码获取方式',
    '  ────────────────────────────────',
    '  ① 直接运行（终端前台）',
    '     → 密码直接打印在终端窗口',
    '  ② 后台服务 / nohup',
    '     → 密码写入：~/nsfocus-monitor/data/initial_password.txt',
    '  ③ Docker 容器',
    '     → 执行：docker logs <container>',
    '     → 查看终端输出中的密码',
    '  ④ Windows exe',
    '     → 弹窗显示密码，文件保存在 exe 同目录下',
    '  ────────────────────────────────',
    '  !  admin 初始密码仅首次生成，后续不改',
    '  ✓ 密码格式：URL-safe Base64，12位，如 YWJjZGVmZ2hpamtsbW5vcA',
], width=PAGE_W - 2*MARGIN - 2))
story.append(Spacer(1, 8))

story.append(Paragraph('2.2  登录系统', s['h2']))
story.append(Paragraph(
    '打开浏览器，访问部署机器 IP + 端口 <b>9999</b>，例如：', s['body']))
story.append(Paragraph('http://192.168.1.100:9999', s['code']))
story.append(Diagram([
    '  ::登录页面',
    '  ─────────────────────────────────────────────────',
    '  ┌─────────────────────────────────────────────┐',
    '  │           绿盟升级监控  (顶部 Logo)            │',
    '  │  ┌─────────────────────────────────────────┐  │',
    '  │  │  用户名                                 │  │',
    '  │  └─────────────────────────────────────────┘  │',
    '  │  ┌─────────────────────────────────────────┐  │',
    '  │  │  密码  ●●●●●●●●                        │  │',
    '  │  └─────────────────────────────────────────┘  │',
    '  │         [ 登录 ]                              │',
    '  │                                               │',
    '  │  首次使用？请联系管理员获取初始密码             │',
    '  └─────────────────────────────────────────────┘',
    '  ─────────────────────────────────────────────────',
    '  → 输入 admin 及初始密码，点击【登录】',
    '  ✓ 登录成功后自动跳转到监控概览页面',
], width=PAGE_W - 2*MARGIN - 2))
story.append(Spacer(1, 8))

story.append(Paragraph('2.3  修改密码', s['h2']))
story.append(Paragraph(
    '登录后点击右上角用户名 → 【个人设置】→ 修改密码。'
    '建议首次登录后立即修改默认密码。', s['body']))
story.append(Spacer(1, 8))

# ── 第三章：采集任务 ──────────────────────────────────
story.append(PageBreak())
story.append(Paragraph('第三章  采集任务', s['h1']))
story.append(HRFlowable(width='100%', thickness=1.5, color=NAVY))
story.append(Spacer(1, 6))

story.append(Paragraph('3.1  查看采集状态', s['h2']))
story.append(Paragraph(
    '登录后首页即为监控概览，可看到所有内容源及其健康状态。'
    '每个内容源显示：', s['body']))
for item in [
    '上次采集时间',
    '健康状态（正常/异常/采集中）',
    '产品数量和包类型',
]:
    story.append(Paragraph(f'• {item}', s['body']))
story.append(Spacer(1, 6))
story.append(Diagram([
    '  ::监控概览页面（首页）',
    '  ──────────────────────────────────────────────────────────────',
    '  【白眼】绿盟升级监控    [admin ▼]  [手动采集] [规则管理]',
    '  ──────────────────────────────────────────────────────────────',
    '  ┌──────────────────────────────────────────────────────────┐',
    '  │  🔍 监控概览                          [手动采集] [▼]     │',
    '  ├──────────────────────────────────────────────────────────┤',
    '  │  产品总数: 78  │  内容源: 160+  │  最后采集: 5分钟前    │',
    '  ├──────────────────────────────────────────────────────────┤',
    '  │  📡 内容源健康状态                                       │',
    '  │  ┌────────────────────────────────────────────────────┐ │',
    '  │  │ ✅ WEB应用防护系统(WAF)    上次: 2026-05-31 14:00  │ │',
    '  │  │ ✅ 网络入侵防护系统(IPS)    上次: 2026-05-31 14:01  │ │',
    '  │  │ ⚠️ 运维安全管理系统(OSMS)   上次: 2026-05-30 08:00  │ │',
    '  │  │ ✅ 安全审计系统(SAS)        上次: 2026-05-31 14:02  │ │',
    '  │  └────────────────────────────────────────────────────┘ │',
    '  └──────────────────────────────────────────────────────────┘',
    '  ──────────────────────────────────────────────────────────────',
    '  ✓ 绿色=正常  黄色=告警  红色=异常',
], width=PAGE_W - 2*MARGIN - 2))
story.append(Spacer(1, 10))

story.append(Paragraph('3.2  手动采集', s['h2']))
story.append(Paragraph(
    '点击顶部导航栏【手动采集】按钮，可选择：', s['body']))
for m in ['全部内容源', '指定产品线（如 WAF/IPS）']:
    story.append(Paragraph(f'• {m}', s['body']))
story.append(Spacer(1, 4))
story.append(Diagram([
    '  ::手动采集确认弹窗',
    '  ─────────────────────────────────────────────────────',
    '  ┌──────────────────────────────────────────────────┐',
    '  │  ⚠️  确认开始采集？                              │',
    '  │                                                  │',
    '  │  采集范围：全部内容源（160+）                    │',
    '  │  预计时间：5-10 分钟                            │',
    '  │  采集期间顶部显示红色警告横条                    │',
    '  │                                                  │',
    '  │  [取消]                     [确认开始采集]        │',
    '  └──────────────────────────────────────────────────┘',
    '  ─────────────────────────────────────────────────────',
    '  ✓ 采集中顶部显示红色横条提示，采集完成后自动消失',
], width=PAGE_W - 2*MARGIN - 2))
story.append(Spacer(1, 8))

story.append(Paragraph('3.3  采集状态标识', s['h2']))
story.append(Paragraph(
    '采集过程中，页面顶部会显示红色警告横条，'
    '固定在视口最上方，不影响正常操作，采集结束后自动消失。', s['body']))
story.append(Diagram([
    '  ::采集进行中页面顶部状态栏',
    '  ──────────────────────────────────────────────────────────────',
    '  ████████████████████████████████████████████████████████████',
    '  █  ⚠️  采集中，请勿关闭页面  [WAF: 3/12] [IPS: 2/8]         █',
    '  ████████████████████████████████████████████████████████████',
    '  ──────────────────────────────────────────────────────────────',
    '  【白眼】绿盟升级监控    [admin ▼]  [手动采集] [规则管理]',
    '  ──────────────────────────────────────────────────────────────',
    '  ...（页面正常内容）',
    '  ──────────────────────────────────────────────────────────────',
    '  ✓ 红色警告栏固定在视口最上方，采集完成后自动消失',
], width=PAGE_W - 2*MARGIN - 2))
story.append(Spacer(1, 8))

# ── 第四章：订阅规则 ──────────────────────────────────
story.append(PageBreak())
story.append(Paragraph('第四章  订阅规则配置', s['h1']))
story.append(HRFlowable(width='100%', thickness=1.5, color=NAVY))
story.append(Spacer(1, 6))

story.append(Paragraph('4.1  进入规则管理', s['h2']))
story.append(Paragraph(
    '点击顶部导航【规则管理】，进入订阅规则列表页面。'
    '规则将决定哪些升级包通过哪些渠道发送给哪些用户。', s['body']))
story.append(Spacer(1, 6))

story.append(Paragraph('4.2  新建订阅规则', s['h2']))
story.append(Paragraph('点击【新建规则】按钮，填写以下信息：', s['body']))
story.append(Spacer(1, 4))
story.append(Diagram([
    '  ::订阅规则编辑表单',
    '  ──────────────────────────────────────────────────────────────',
    '  ┌───────────────────────────────────────────────────────────┐',
    '  │  规则名称：  [WAF规则升级包订阅        ]                   │',
    '  │  ────────────────────────────────────────────────────────  │',
    '  │  产品线：    [WEB应用防护系统(WAF)  ▼]                     │',
    '  │  ────────────────────────────────────────────────────────  │',
    '  │  包类型：   ☑ 系统升级包  ☑ 规则升级包                    │',
    '  │            ☑ 威胁情报包  ☐ 完整升级包                    │',
    '  │  ────────────────────────────────────────────────────────  │',
    '  │  推送模式： ○ 立即推送  ○ 延迟N小时  ● 汇总推送 [weekly▼]│',
    '  │  ────────────────────────────────────────────────────────  │',
    '  │  通知渠道： ☑ 绿盟企业微信  ☐ 163邮箱                     │',
    '  │  ────────────────────────────────────────────────────────  │',
    '  │                           [取消]  [保存规则]              │',
    '  └───────────────────────────────────────────────────────────┘',
    '  ──────────────────────────────────────────────────────────────',
    '  ✓ 包类型留空=订阅该产品线所有类型',
    '  ✓ 推送模式详细说明见 4.3 节',
], width=PAGE_W - 2*MARGIN - 2))
story.append(Spacer(1, 10))

story.append(Paragraph('4.3  推送模式详解', s['h2']))
story.append(Paragraph(
    '系统支持三种推送模式：', s['body']))

modes = [
    ('立即推送', RED, '☒',
     '发现新包后立即发送通知，适合紧急升级包。\n'
     '收到通知后可直接点击下载。'),
    ('延迟推送', ORANGE, '☐',
     '等待 N 小时后才发送，可在延迟窗口内取消。\n'
     '适合希望集中处理而非实时通知的场景。'),
    ('汇总推送', GREEN, '●',
     '按周期（周/月/季度）归并后统一发送。\n'
     '一个通知包含本周/本月所有升级包，按来源（source_url）分组，\n'
     '每个来源单独一个分组，标题区有⚠提示查看升级描述。\n'
     '推荐日常订阅使用此模式，减少通知数量。'),
]
for name, color, icon, desc in modes:
    story.append(Diagram([
        f'  {icon}  {name}',
        f'     {desc}',
    ], width=PAGE_W - 2*MARGIN - 2))
    story.append(Spacer(1, 4))

story.append(Spacer(1, 6))
story.append(Paragraph('4.4  汇总推送示例', s['h2']))
story.append(Diagram([
    '  ::企业微信收到的汇总通知示例',
    '  ──────────────────────────────────────────────────────────────',
    '  📊 WAF规则升级包订阅 — 周度升级汇总',
    '  ',
    '  周期: 2026-W22',
    '  产品: WEB应用防护系统(WAF)',
    '  本期新增: 3 个升级包',
    '  🔺 请查看每个升级包的详情/升级描述，了解具体变更内容',
    '  ',
    '  ### rule',
    '  1. 🔵 WAF V6.0.9规则升级包',
    '     文件名: update_rule.V6.0R09F00.29647177.wcl',
    '     下载: [文件名](https://.../downloads/id/188213)',
    '     MD5: `2232964bdcb4bdfa35baa63f209df6a3`',
    '     详情: [URL](https://.../listWafV69Detail/v/rule)',
    '  ',
    '  ### sys',
    '  2. ℹ️ WAF V6.0.9系统升级包',
    '     文件名: （无文件名）',
    '     详情: [URL](https://.../listWafV69Detail/v/sys)',
    '  ──────────────────────────────────────────────────────────────',
    '  ✓ 每个source_url单独分组',
    '  ✓ ⚠️提示用户查看升级描述',
    '  ✓ 下载链接可点击，详情链接可复制',
], width=PAGE_W - 2*MARGIN - 2))
story.append(Spacer(1, 8))

# ── 第五章：通知渠道 ──────────────────────────────────
story.append(PageBreak())
story.append(Paragraph('第五章  通知渠道配置', s['h1']))
story.append(HRFlowable(width='100%', thickness=1.5, color=NAVY))
story.append(Spacer(1, 6))

story.append(Paragraph('5.1  添加企业微信渠道', s['h2']))
story.append(Diagram([
    '  ::企业微信 webhook 配置步骤',
    '  ──────────────────────────────────────────────────────────────',
    '  ① 打开企业微信 PC 端',
    '  ② 进入「群聊」→「群机器人」设置',
    '  ③ 添加自定义机器人，复制 Webhook URL',
    '     → 格式：https://qyapi.weixin.qq.com/cgi-bin/webhook/send?',
    '     → 密钥在 URL 末尾，如 key=XXXXXX',
    '  ④ 在白眼系统「规则管理」→「渠道配置」中：',
    '     - 渠道名称：如「绿盟企业微信」',
    '     - 渠道类型：选择「企业微信」',
    '     - 粘贴 Webhook URL',
    '  ──────────────────────────────────────────────────────────────',
    '  ✓ Webhook URL 示例：',
    '  https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc-123',
], width=PAGE_W - 2*MARGIN - 2))
story.append(Spacer(1, 10))

story.append(Paragraph('5.2  添加邮件渠道', s['h2']))
story.append(Diagram([
    '  ::邮件渠道配置说明',
    '  ──────────────────────────────────────────────────────────────',
    '  ⚠️  邮件渠道需要配置发件人 SMTP 信息',
    '  ──────────────────────────────────────────────────────────────',
    '  配置项：',
    '  • SMTP 服务器：如 smtp.163.com',
    '  • SMTP 端口：465（SSL）或 587（TLS）',
    '  • 用户名：your_email@163.com',
    '  • 密码/授权码：163邮箱的 POP3/SMTP 授权码',
    '  • 发件人名称：显示在邮件发件人位置',
    '  ──────────────────────────────────────────────────────────────',
    '  ✓ 授权码在 163邮箱 → 设置 → POP3/SMTP/IMAP 中开启后获取',
], width=PAGE_W - 2*MARGIN - 2))
story.append(Spacer(1, 8))

# ── 第六章：常见问题 ──────────────────────────────────
story.append(PageBreak())
story.append(Paragraph('第六章  常见问题', s['h1']))
story.append(HRFlowable(width='100%', thickness=1.5, color=NAVY))
story.append(Spacer(1, 6))

qa_pairs = [
    ('Q: 忘记 admin 密码怎么办？',
     'A: 密码文件保存在 data/initial_password.txt，'
     '或查看服务启动终端输出。可用数据库工具直接重置。'),
    ('Q: 登录提示"登录已禁用"？',
     'A: 系统启用了登录限流，同一 IP 15分钟内连续失败5次会被封禁。'
     '等待15分钟后自动解封，或重启服务清除 login_bans 表。'),
    ('Q: 为什么没收到推送通知？',
     'A: 检查以下几项：①规则是否启用；②渠道是否激活；'
     '③采集任务是否正常运行；④检查 delivery_log 表确认发送状态。'),
    ('Q: 采集任务卡住怎么办？',
     'A: 重启服务：pkill -f run.py && python3.10 -B run.py'
     '然后手动触发一次采集确认恢复。'),
    ('Q: 如何查看历史推送记录？',
     'A: 在「规则管理」页面点击具体规则，可查看推送日志。'
     '数据库 delivery_log 表记录每次发送详情。'),
    ('Q: 汇总推送周期怎么算？',
     'A: weekly=每周日发送，monthly=每月最后一天，quarterly=每季度末。'
     '发送时间固定凌晨0点。'),
]

for q, a in qa_pairs:
    story.append(Paragraph(f'<b>{q}</b>', s['h2']))
    story.append(Paragraph(a, s['body']))

story.append(Spacer(1, 8))

# ── 附录 ─────────────────────────────────────────────
story.append(PageBreak())
story.append(Paragraph('附录  服务管理命令', s['h1']))
story.append(HRFlowable(width='100%', thickness=1.5, color=NAVY))
story.append(Spacer(1, 6))

story.append(Paragraph('服务控制命令（Linux）', s['h2']))
cmds = [
    ('启动服务', 'python3.10 -B /root/nsfocus-monitor/run.py'),
    ('停止服务', 'pkill -f run.py'),
    ('重启服务', 'pkill -f run.py && sleep 2 && python3.10 -B /root/nsfocus-monitor/run.py'),
    ('查看进程', 'ps aux | grep run.py | grep -v grep'),
    ('检查端口', 'curl -s http://localhost:9999/ -o /dev/null -w "%{http_code}"'),
    ('查看日志', 'tail -f /root/nsfocus-monitor/logs/app.log'),
    ('初始密码', 'cat /root/nsfocus-monitor/data/initial_password.txt'),
]
cmd_data = [['命令', '操作'], ] + [[c, a] for c, a in cmds]
cmd_table = Table(cmd_data, colWidths=[5*cm, PAGE_W - 2*MARGIN - 5*cm])
cmd_table.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,0), BASE_FONT),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('FONTNAME', (0,0), (-1,0), BASE_FONT),
    ('BACKGROUND', (0,0), (-1,0), BLUE),
    ('TEXTCOLOR', (0,0), (-1,0), white),
    ('FONTNAME', (0,1), (-1,-1), 'Courier'),
    ('FONTSIZE', (0,1), (-1,-1), 7.5),
    ('BACKGROUND', (0,1), (-1,-1), LIGHTGRAY),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, LIGHTGRAY]),
    ('GRID', (0,0), (-1,-1), 0.3, GRAY),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 4),
    ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ('LEFTPADDING', (0,0), (-1,-1), 6),
]))
story.append(cmd_table)
story.append(Spacer(1, 12))

story.append(Paragraph('默认端口与凭证', s['h2']))
cred_data = [['项目', '值'],]
creds = [
    ('Web 服务端口', '9999'),
    ('默认管理员账号', 'admin'),
    ('默认管理员密码', '（首次启动时生成，参见 2.1 节）'),
    ('数据库路径', '/root/nsfocus-monitor/data/nsfocus_monitor.db'),
    ('日志文件', '/root/nsfocus-monitor/logs/app.log'),
]
cred_data += [[c, v] for c, v in creds]
cred_table = Table(cred_data, colWidths=[5*cm, PAGE_W - 2*MARGIN - 5*cm])
cred_table.setStyle(TableStyle([
    ('FONTNAME', (0,0), (-1,0), BASE_FONT),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('BACKGROUND', (0,0), (-1,0), NAVY),
    ('TEXTCOLOR', (0,0), (-1,0), white),
    ('FONTNAME', (0,1), (-1,-1), BASE_FONT),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, LIGHTGRAY]),
    ('GRID', (0,0), (-1,-1), 0.3, GRAY),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('TOPPADDING', (0,0), (-1,-1), 4),
    ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ('LEFTPADDING', (0,0), (-1,-1), 6),
]))
story.append(cred_table)
story.append(Spacer(1, 20))

story.append(Paragraph('© 白眼（Byakugan）升级监控系统  |  如有疑问请联系系统管理员', s['center']))

# ── 构建 PDF ──────────────────────────────────────────
doc.build(story,
          onFirstPage=draw_cover,
          onLaterPages=draw_page_bg)
print(f'PDF 生成完成: {OUTPUT}')
