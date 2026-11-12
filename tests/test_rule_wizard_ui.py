"""订阅规则向导 UI 回归测试：分步展示与步骤校验。"""
import re

from tests.frontend_source import WEB, js_source

CSS = (WEB / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')


def _rule_modal_source() -> str:
    source = js_source()
    start = source.index('<div class="wizard-steps" id="rWizardSteps">')
    end = source.index('<div class="drawer-foot wizard-foot">')
    return source[start:end]


def _step_bodies() -> list[str]:
    block = _rule_modal_source()
    parts = re.split(r'<div class="rstep(?: hidden)?" data-step="\d">', block)
    return parts[1:]


def test_rule_modal_is_four_step_wizard():
    source = js_source()
    assert "const RULE_STEPS=['基本信息','订阅条件','推送策略','通知对象']" in source
    assert 'id="rWizardSteps"' in source
    assert 'data-on-click="goRuleStep"' in source
    assert 'data-on-click="ruleNextStep"' in source
    assert 'data-on-click="rulePrevStep"' in source
    assert 'id="rSaveBtn"' in source and 'id="rPrevBtn"' in source and 'id="rNextBtn"' in source
    assert 'function _ruleShowStep' in source
    assert 'function goRuleStep' in source
    assert len(_step_bodies()) == 4


def test_wizard_steps_group_related_fields():
    steps = _step_bodies()
    assert 'id="rnm"' in steps[0] and 'id="rcust"' in steps[0]
    assert 'id="rall"' in steps[1] and 'id="rcondsWrap"' in steps[1]
    assert 'id="rdm"' in steps[2] and 'id="rdl"' in steps[2] and 'id="rnr"' in steps[2]
    # 通知对象：机器人渠道与邮箱收件人/发件渠道都用 + 逐个添加
    assert 'id="rchansRobotTags"' in steps[3]
    assert 'id="rchansRobotMenu"' in steps[3]
    assert 'id="rrobotAdd"' in steps[3]
    assert 'data-on-click="toggleRobotPicker"' in steps[3]
    assert 'id="remails"' in steps[3] and 'id="remailSuggest"' in steps[3]
    assert 'data-on-click="addEmailRecipient"' in steps[3]
    assert 'id="remailSenderMenu"' in steps[3] and 'id="remailAttach"' in steps[3]
    assert 'data-on-click="toggleSenderPicker"' in steps[3]
    assert 'id="rchansEmailTags"' not in steps[3]
    # 模板说明默认收起，避免一次性铺开
    assert '<details class="tpl-help">' in steps[3]


def test_notify_targets_prefer_nsfocus_and_support_multi_sender():
    source = js_source()
    assert "const NS_EMAIL_DOMAINS=['nsfocus.com','nsfocus.net']" in source
    assert 'function _isNsEmail' in source
    assert 'function renderNotifyTargets' in source
    # 机器人渠道也用 + 添加，选中后作为通知对象，可移除
    assert 'function toggleRobotPicker' in source
    assert 'function addRobotChan' in source and 'function removeRobotChan' in source
    assert 'function toggleRobotChan' not in source
    # 收件邮箱用 + 逐个添加，不再用逗号分隔录入
    assert 'function addEmailRecipient' in source
    assert 'function onEmailDraftKeydown' in source
    assert '多个用逗号分隔' not in source
    # 建议邮箱按 nsfocus 域名排前
    assert '_isNsEmail(b)?1:0)-(_isNsEmail(a)?1:0)' in source
    # 发件渠道用 + 添加；发件地址来自渠道 smtp_user，nsfocus 域名优先
    assert 'function addEmailSender' in source and 'function removeEmailSender' in source
    assert 'function toggleSenderPicker' in source
    assert 'config&&ch.config.smtp_user' in source
    assert '_isNsEmail(_channelSenderEmail(a))?0:1' in source
    assert 'pick-badge' in source
    # 多个发件渠道按选择顺序主备 + 顺序角标
    assert 'sender-order' in source
    assert '前一个失败自动用下一个' in source
    assert 'function validateNotifyTargets' in source
    # 保存时先提交输入框里未添加的邮箱，并写入每个邮箱渠道
    save = source[source.index('async function saveRule'):source.index('async function delRule')]
    assert '_commitEmailDraft()' in save
    assert 'validateNotifyTargets()' in save
    assert "customer_emails: isEmail?emailRecips:''" in save
    assert 'attachment_max_mb: isEmail?emailAttach:0' in save


def test_wizard_validates_before_advancing():
    source = js_source()
    assert 'function _ruleValidateStep' in source
    assert "return '请输入规则名称'" in source
    assert "return '请选择关联客户'" in source
    assert '请选择订阅路径，或勾选「订阅全部产品」' in source
    assert 'for(let s=cur;s<n;s++)' in source
    # 保存失败时跳回对应步骤
    save = source[source.index('async function saveRule'):source.index('async function delRule')]
    assert '_ruleShowStep(1)' in save
    assert '_ruleShowStep(2)' in save
    assert '_ruleShowStep(3)' in save


def test_wizard_final_step_shows_summary():
    source = js_source()
    assert 'id="rSummary"' in source
    assert 'function _ruleSummaryHtml' in source
    assert 'wz-sum-label' in source and source.count('wz-sum-item') >= 4
    for label in ('>客户<', '>订阅<', '>对象<', '>推送<'):
        assert label in source


def test_wizard_styles_present():
    for cls in ('.wizard-steps', '.wizard-step', '.wizard-dot', '.wizard-line',
                '.wizard-summary', '.wz-sum-item', '.wz-sum-label', '.tpl-help',
                '.notify-sec', '.obj-row', '.email-chip-ns', '.email-chip-ext',
                '.pick-menu', '.pick-item', '.sender-chip', '.sender-order'):
        assert cls in CSS
    # 已完成步骤显示对勾，当前步骤显示序号
    assert "dot.textContent=s<n?'✓':String(s)" in js_source()
