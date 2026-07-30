# -*- coding: utf-8 -*-
from pathlib import Path
import re, sys
ROOT = Path(__file__).resolve().parents[1]

def test_workers_dom_ids_unique():
    mon = (ROOT / 'webui/monitor.py').read_text(encoding='utf-8')
    assert 'id="workers-input"' in mon
    assert 'id="workers-stats"' in mon
    assert not re.search(r'id="workers"', mon)

def test_no_cors_wildcard():
    mon = (ROOT / 'webui/monitor.py').read_text(encoding='utf-8')
    assert 'Access-Control-Allow-Origin", "*"' not in mon

def test_no_bind_all_fallback():
    mon = (ROOT / 'webui/monitor.py').read_text(encoding='utf-8')
    assert 'host = "0.0.0.0"' not in mon

def test_license_upstream():
    assert 'AaronL725' in (ROOT / 'LICENSE').read_text(encoding='utf-8')
    assert 'AaronL725' in (ROOT / 'NOTICE').read_text(encoding='utf-8')
    notice = (ROOT / 'NOTICE').read_text(encoding='utf-8')
    assert 'Geist' in notice and 'Project Authors' in notice
    assert (ROOT / 'LICENSES/OFL-1.1-Geist.txt').is_file()

def test_redact_proxy_shipped():
    sys.path.insert(0, str(ROOT))
    from webui.security_utils import redact_proxy
    secret = 'super-secret-pass-ZZ'
    out = redact_proxy(f'http://u:{secret}@10.0.0.1:8080')
    assert secret not in out

def test_theme_switch_structure():
    mon = (ROOT / 'webui/monitor.py').read_text(encoding='utf-8')
    assert 'GROK_REGISTER_THEME' in mon
    assert 'data-theme-choice="light"' in mon
    assert 'data-theme-choice="dark"' in mon
    assert 'aria-pressed="false"' in mon
    assert 'function setTheme(theme)' in mon
    assert 'html[data-theme="dark"]' in mon

def test_reference_design_tokens_and_fonts():
    mon = (ROOT / 'webui/monitor.py').read_text(encoding='utf-8')
    assert '--bg: #f3f4f1;' in mon
    assert '--surface: #e9eae6;' in mon
    assert '--text: #151613;' in mon
    assert '--accent: #b93b28;' in mon
    assert '--bg: #171815;' in mon
    assert '--surface: #20211e;' in mon
    assert '--text: #f0f1ed;' in mon
    assert '--accent: #f06449;' in mon
    assert 'url("/assets/geist.woff2")' in mon
    assert 'url("/assets/geist-mono.woff2")' in mon
    assert 'if u.path in FONT_ASSETS:' in mon
    assert (ROOT / 'webui/assets/geist-latin-wght-normal.woff2').is_file()
    assert (ROOT / 'webui/assets/geist-mono-latin-wght-normal.woff2').is_file()

def test_reference_motion_and_reduced_motion():
    mon = (ROOT / 'webui/monitor.py').read_text(encoding='utf-8')
    assert 'background-size: 40px 40px;' in mon
    assert '@keyframes panel-enter' in mon
    assert 'main > :not(.help-view)' in mon
    assert 'animation: panel-enter 520ms' in mon
    assert '@media (prefers-reduced-motion: reduce)' in mon
    assert 'animation-iteration-count: 1 !important;' in mon

def test_compact_overview_density():
    mon = (ROOT / 'webui/monitor.py').read_text(encoding='utf-8')
    assert '.page-heading > div { min-width: 0; }' in mon
    assert '.control-panel { padding: 12px 16px; }' in mon
    assert '.control-panel .msg:empty { display: none; }' in mon
    assert '#kpis { margin-top: 10px; }' in mon
    assert '.rate-panel { margin-top: 10px; padding: 12px 16px 14px; }' in mon
    assert '<section class="card panel rate-panel">' in mon
    assert '@media (min-width: 1121px)' in mon
    assert '.control-panel .control-actions button {' in mon

def test_help_and_faq_module():
    mon = (ROOT / 'webui/monitor.py').read_text(encoding='utf-8')
    html = mon.split('HTML = r"""', 1)[1].split('"""', 1)[0]
    assert 'id="dashboard-view"' in html
    assert 'id="help-view"' in html
    assert 'id="help-view-toggle"' in html
    assert 'aria-controls="help-view"' in html
    assert 'id="help-view-label" aria-hidden="true">问题和使用</span>' in html
    assert 'aria-label="打开问题和使用"' in html
    assert 'label.textContent = isHelp ? "返回控制台" : "问题和使用";' in mon
    assert 'id="help-view-icon"' not in html
    assert html.index('id="help-view-toggle"') < html.index('class="theme-switch"')
    assert 'class="help-view"' in html
    assert 'position: fixed;' in html
    assert 'body.help-view-open { overflow: hidden; }' in html
    assert 'body.help-view-open #dashboard-view > :not(#help-view) { display: none; }' in html
    assert 'role="tablist"' in html
    assert 'id="faq-search"' in html
    assert len(re.findall(r'<details class="faq-item" data-faq-item', html)) == 12
    assert 'policy=deny' in html
    assert '账号补录' in html
    assert '成功项会从待补录队列移除' in html
    assert 'permission-denied' in html
    assert 'function setAppView(view, options = {})' in mon
    assert 'function toggleAppView()' in mon
    assert 'element.inert = isHelp;' in mon
    assert 'help.inert = !isHelp;' in mon
    assert 'function setHelpTab(name)' in mon
    assert 'function handleHelpTabKey(event)' in mon
    assert 'function filterFaq(value)' in mon
    assert 'showHelpFor("令牌")' in mon
    assert '访问令牌不匹配，请重新输入当前面板令牌' in mon
    assert '—' not in html
    assert '–' not in html

def test_panel_security_and_recovery_structure():
    mon = (ROOT / 'webui/monitor.py').read_text(encoding='utf-8')
    html = mon.split('HTML = r"""', 1)[1].split('"""', 1)[0]
    assert 'def _require_read(self)' in mon
    assert 'frame-ancestors \'none\'' in mon
    assert 'X-Frame-Options' in mon
    assert 'MAX_REQUEST_BODY = 64 * 1024' in mon
    assert mon.index('if not self._require_write():') < mon.index('body = self._read_body()')
    assert '/api/recovery/start' in mon
    assert '/api/recovery/stop' in mon
    assert 'id="recovery-pending"' in html
    assert 'id="recovery-accounts"' in html
    assert 'id="recovery-stop"' in html
    assert 'id="run-status" aria-label="任务状态：加载中"' in html
    assert 'setAttribute("aria-label", "任务状态：" + runLabel)' in mon
    assert 'id="kpis" aria-label="核心指标" aria-live=' not in html
    assert 'if u.path == "/favicon.ico":' in mon
    assert 'def version_string(self):' in mon

if __name__ == '__main__':
    test_workers_dom_ids_unique()
    test_no_cors_wildcard()
    test_no_bind_all_fallback()
    test_license_upstream()
    test_redact_proxy_shipped()
    test_theme_switch_structure()
    test_reference_design_tokens_and_fonts()
    test_reference_motion_and_reduced_motion()
    test_compact_overview_density()
    test_help_and_faq_module()
    test_panel_security_and_recovery_structure()
    print('OK structure')
