import os
from uuid import uuid4
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.environ.get('PLATFORM_TASK_UI_PROBE') != '1', reason='opt-in isolated demo browser')
def test_demo_provider_catalog_and_offline_connection():
    from playwright.sync_api import expect, sync_playwright

    url = os.environ.get('PLATFORM_TASK_DEMO_URL', 'http://127.0.0.1:8097')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url + '/demo')
        page.goto(url + '/setting_/agent_provider')
        page.locator('#agent-provider-add').click()
        page.locator('.ant-select:has(#agent-provider-kind) .ant-select-selector').click()
        dropdown = page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)')
        for label in ('OpenAI 兼容接口', '企业内部 Agent 接口', '企业模型'):
            expect(dropdown.get_by_title(label, exact=True)).to_be_visible()
        dropdown.get_by_title('企业模型', exact=True).click()
        page.locator('#agent-provider-name').fill('展示企业模型')
        page.locator('#agent-enterprise-check').click()
        expect(page.locator('#agent-enterprise-login-status')).to_contain_text('登录文件可用')
        page.locator('#agent-provider-test').click()
        expect(page.locator('#agent-provider-status')).to_contain_text('离线演示检查通过', timeout=15000)
        expect(page.locator('#agent-provider-status')).to_contain_text('未请求外部服务')
        assert 'demo-only-enterprise-access' not in page.content()
        report = ROOT / 'cache' / 'report' / 'ui'
        report.mkdir(parents=True, exist_ok=True)
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 960 if width > 700 else 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
            page.screenshot(path=str(report / f'demo-provider-catalog-{width}.png'))
        page.set_viewport_size({'width': 1440, 'height': 960})
        page.locator('#agent-provider-save').click()
        expect(page.locator('#agent-provider-status')).to_contain_text('配置已保存', timeout=15000)
        page.locator('#agent-launcher').click()
        page.locator('#agent-new-conversation').click()
        for prompt, answer in (('分析当前告警', '93%'), ('运行任务票据归档', '只提供只读分析')):
            expect(page.locator('#agent-send')).to_be_enabled(timeout=15000)
            page.locator('#agent-prompt').fill(prompt)
            page.locator('#agent-send').click()
            expect(page.locator('.agent-message-assistant').last).to_contain_text(answer, timeout=15000)
        page.locator('#agent-close').click()
        page.locator('#agent-provider-delete').click()
        page.get_by_role('button', name='确认删除', exact=True).click()
        expect(page.locator('#agent-provider-name')).to_have_value('', timeout=15000)
        assert not errors, errors
        browser.close()


@pytest.mark.skipif(os.environ.get('PLATFORM_TASK_UI_PROBE') != '1', reason='opt-in isolated demo browser')
def test_hackathon_demo_story_and_synthetic_monitor():
    from playwright.sync_api import expect, sync_playwright

    url = os.environ.get('PLATFORM_TASK_DEMO_URL', 'http://127.0.0.1:8097')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url + '/demo')
        banner = page.locator('#platform-demo-banner')
        banner.wait_for(timeout=30000)
        assert banner.inner_text().startswith('演示模式')
        assert banner.evaluate('(node) => getComputedStyle(node).position') == 'static'
        assert page.locator('.platform-nav-footer').inner_text().startswith('SI-PLATFORM')
        page.goto(url + '/dashboard_/monitor')
        page.locator('#monitor-summary .monitor-metric-value').first.wait_for(timeout=20000)
        metrics = page.locator('#monitor-summary').inner_text()
        assert '93.0%' in metrics and '86.0%' in metrics and '92.0%' in metrics

        page.locator('#agent-launcher').click()
        page.locator('#agent-panel').wait_for(state='visible')
        expect(page.locator('#agent-send')).to_be_enabled(timeout=30000)
        page.locator('#agent-new-conversation').click()
        expect(page.locator('#agent-send')).to_be_enabled(timeout=15000)
        assert page.locator('#agent-model-status').inner_text() == '演示剧本 · 非模型'
        page.locator('#agent-prompt').fill('分析当前告警')
        expect(page.locator('#agent-prompt')).to_have_value('分析当前告警')
        page.locator('#agent-send').click()
        expect(page.locator('.agent-message-user .agent-message-avatar-user .ant-avatar-string')).to_have_text('演', timeout=15000)
        expect(page.locator('.agent-message-assistant').last).to_contain_text('93%', timeout=15000)

        page.locator('#agent-prompt').fill('查看票据归档的最近运行日志')
        expect(page.locator('#agent-prompt')).to_have_value('查看票据归档的最近运行日志')
        page.locator('#agent-send').click()
        expect(page.locator('.agent-message-assistant').last).to_contain_text('没有读取任何日志', timeout=15000)
        assert page.locator('.agent-trace').count() == 0

        page.locator('#agent-log-consent').check()
        page.locator('#agent-prompt').fill('查看票据归档的最近运行日志')
        expect(page.locator('#agent-prompt')).to_have_value('查看票据归档的最近运行日志')
        page.locator('#agent-send').click()
        answer = page.locator('.agent-message-assistant').last
        expect(answer).to_contain_text('最近运行状态为 failed', timeout=15000)
        assert 'DEMO-INCIDENT-01' in answer.inner_text()
        assert '没有提议或执行任务' in answer.inner_text()
        assert page.locator('.agent-trace').count() == 1

        page.locator('#agent-prompt').fill('运行任务票据归档')
        expect(page.locator('#agent-prompt')).to_have_value('运行任务票据归档')
        page.locator('#agent-send').click()
        expect(page.locator('.agent-message-assistant').last).to_contain_text('只提供只读分析', timeout=15000)
        assert page.locator('.agent-trace').count() == 1
        assert page.locator('#agent-approvals').inner_text() == ''
        assert not errors, errors
        browser.close()


@pytest.mark.skipif(os.environ.get('PLATFORM_TASK_UI_PROBE') != '1', reason='opt-in isolated demo browser')
def test_page_agent_uses_local_bundle_and_same_origin_model_proxy():
    from playwright.sync_api import expect, sync_playwright

    url = os.environ.get('PLATFORM_TASK_DEMO_URL', 'http://127.0.0.1:8097')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        requests = []
        errors = []
        page.on('request', lambda request: requests.append(request.url))
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url + '/demo')
        page.locator('#agent-launcher').click()
        page.locator('.page-agent-details > summary').click()
        page.locator('#page-agent-launcher').click()
        page.wait_for_function(
            "window.pageAgent?.config?.baseURL === window.location.origin + '/page_agent_llm/v1'",
            timeout=30000,
        )
        expect(page.locator('#page-agent-status')).to_have_text('工具已就绪，模型连接将在使用时建立。')
        config = page.evaluate("""() => ({
            model: window.pageAgent.config.model,
            baseURL: window.pageAgent.config.baseURL,
            apiKey: window.pageAgent.config.apiKey,
        })""")
        assert config == {
            'model': 'platform-default',
            'baseURL': url + '/page_agent_llm/v1',
            'apiKey': 'page-agent-browser',
        }
        local_assets = [item for item in requests if '/assets/vendor/page-agent/' in item]
        public_requests = [
            item for item in requests
            if any(host in item for host in ('registry.npm', 'cdn.jsdelivr', 'page-ag-testing-'))
        ]
        assert local_assets and 'autoInit=false' in local_assets[-1]
        assert not public_requests
        assert not errors, errors

        page.locator('#agent-widget').evaluate('(node) => node.remove()')
        page.wait_for_function("window.pageAgent == null")
        assert page.locator('page-agent').count() == 0
        assert not errors, errors
        browser.close()


@pytest.mark.skipif(os.environ.get('PLATFORM_TASK_UI_PROBE') != '1', reason='opt-in isolated demo browser')
def test_gantt_readability():
    from playwright.sync_api import sync_playwright

    url = os.environ.get('PLATFORM_TASK_DEMO_URL', 'http://127.0.0.1:8097')
    output = ROOT / 'cache' / 'report' / 'ui'
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url + '/demo')
        page.locator('.tc-overview-page').wait_for(timeout=30000)
        page.wait_for_function("document.querySelector('#tc-overview-chart .js-plotly-plot')?.data?.length > 0")
        assert page.locator('.tc-overview-page .tc-summary-value').count() == 4
        assert not page.locator('.tc-overview-settings').evaluate('(node) => node.open')
        for width in [1920, 1440, 1366, 390, 320]:
            page.set_viewport_size({'width': width, 'height': 960 if width > 700 else 844})
            page.locator('.tc-overview-settings > summary').click()
            settings = page.locator('.tc-overview-settings-panel')
            bounds = settings.bounding_box()
            assert bounds is not None and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width + 1
            assert settings.evaluate('(node) => node.scrollWidth <= node.clientWidth + 1')
            page.locator('#tc-scheduler-policy-save').click()
            page.locator('#tc-scheduler-policy-notice').get_by_text('已保存', exact=False).wait_for()
            page.locator('.tc-overview-settings > summary').click()
            page.locator('.tc-scheduler-details > summary').click()
            detail_bounds = page.locator('.tc-scheduler-detail-popover').bounding_box()
            assert detail_bounds is not None and detail_bounds['x'] >= 0
            assert detail_bounds['x'] + detail_bounds['width'] <= width + 1
            page.locator('.tc-scheduler-details > summary').click()
            for mode, label, direction in [('schedule', '计划', '未来'), ('actual', '实际运行', '过去')]:
                page.locator('#tc-overview-mode').get_by_text(label, exact=True).click()
                page.locator('.tc-overview-page .tc-toolbar').get_by_text(direction + ' 24 小时', exact=True).wait_for()
                page.wait_for_function("""mode => {
                    const plot = document.querySelector('#tc-overview-chart .js-plotly-plot');
                    return plot?.data?.some(trace => trace.name === (mode === 'schedule' ? '计划预览' : '运行时间标记'))
                        && plot.getBoundingClientRect().height >= 320
                        && Math.abs(plot.getBoundingClientRect().width - document.querySelector('#tc-overview-chart').getBoundingClientRect().width) < 2;
                }""", arg=mode)
                metrics = page.evaluate("""() => {
                    const plot = document.querySelector('#tc-overview-chart .js-plotly-plot');
                    const axis = plot._fullLayout.xaxis;
                    const ticks = [...plot.querySelectorAll('.xtick text')].map(node => node.getBoundingClientRect());
                    const title = plot.querySelector('.xtitle').getBoundingClientRect();
                    return {span: axis.r2l(axis.range[1]) - axis.r2l(axis.range[0]),
                        rows: plot.layout.yaxis.tickvals, labels: plot.layout.yaxis.ticktext,
                        points: plot.data.filter(trace => trace.type === 'scatter').flatMap(trace => trace.customdata),
                        minMarker: Math.min(...plot.data.filter(trace => trace.type === 'scatter').map(trace =>
                            Array.isArray(trace.marker.size) ? Math.min(...trace.marker.size) : trace.marker.size)),
                        graphWidth: plot.getBoundingClientRect().width,
                        overlap: ticks.some(tick => tick.bottom >= title.top)};
                }""")
                assert abs(metrics['span'] - 86400000) < 1000, (width, mode, metrics)
                assert len(set(metrics['rows'])) == len(metrics['rows']), (width, mode)
                assert len(set(metrics['labels'])) == len(metrics['labels']), (width, mode)
                assert metrics['minMarker'] >= 12, (width, mode)
                assert metrics['graphWidth'] >= 640, (width, mode)
                assert not metrics['overlap'], (width, mode)
                if mode == 'actual':
                    assert len(metrics['points']) == len(metrics['rows']), (width, mode)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), (width, mode)
                page.screenshot(path=str(output / f'gantt-{mode}-{width}.png'))
                if width < 700:
                    wrapper = page.locator('#tc-overview-chart-wrap')
                    assert wrapper.evaluate('(node) => node.scrollWidth > node.clientWidth'), (width, mode)
                    wrapper.evaluate('(node) => { node.scrollLeft = node.scrollWidth; }')
                    assert wrapper.evaluate('(node) => node.scrollLeft > 0'), (width, mode)
                    page.screenshot(path=str(output / f'gantt-{mode}-{width}-scrolled.png'))
                    wrapper.evaluate('(node) => { node.scrollLeft = 0; }')
        page.set_viewport_size({'width': 1440, 'height': 960})
        page.locator('.tc-overview-page .tc-toolbar').get_by_text('过去 24 小时', exact=True).click()
        page.locator('.ant-select-dropdown:visible').get_by_text('过去 7 天', exact=True).click()
        page.wait_for_function("""() => {
            const plot = document.querySelector('#tc-overview-chart .js-plotly-plot');
            return Math.abs(plot._fullLayout.xaxis.r2l(plot.layout.xaxis.range[1]) -
                plot._fullLayout.xaxis.r2l(plot.layout.xaxis.range[0]) - 7 * 86400000) < 1000;
        }""")
        page.locator('.modebar-btn[data-attr="zoom"][data-val="in"]').click()
        page.wait_for_function("""() => {
            const axis = document.querySelector('#tc-overview-chart .js-plotly-plot')._fullLayout.xaxis;
            return axis.r2l(axis.range[1]) - axis.r2l(axis.range[0]) < 7 * 86400000 - 1000;
        }""")
        page.locator('#tc-overview-reset').click()
        page.wait_for_function("""() => {
            const axis = document.querySelector('#tc-overview-chart .js-plotly-plot')._fullLayout.xaxis;
            return Math.abs(axis.r2l(axis.range[1]) - axis.r2l(axis.range[0]) - 7 * 86400000) < 1000;
        }""")
        point = page.locator('#tc-overview-chart .scatterlayer .points .point').first
        point.scroll_into_view_if_needed()
        bounds = point.bounding_box()
        assert bounds is not None
        point_x = bounds['x'] + bounds['width'] / 2
        point_y = bounds['y'] + bounds['height'] / 2
        page.mouse.move(point_x, point_y)
        page.locator('#tc-overview-chart .hoverlayer').get_by_text('时长：', exact=False).wait_for()
        page.mouse.click(point_x, point_y)
        page.wait_for_url('**/task_/task_log?run_id=*')
        page.locator('.tc-log-page').wait_for()
        assert not errors, errors
        browser.close()


@pytest.mark.skipif(os.environ.get('PLATFORM_TASK_UI_PROBE') != '1', reason='opt-in isolated demo browser')
def test_task_center_showcase():
    from playwright.sync_api import sync_playwright

    url = os.environ.get('PLATFORM_TASK_DEMO_URL', 'http://127.0.0.1:8097')
    output = ROOT / 'cache' / 'report' / 'ui'
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url + '/demo')
        page.locator('.tc-overview-page').wait_for(timeout=30000)
        page.get_by_text('当前页任务', exact=True).wait_for()
        for width in [1440, 1366, 390, 320]:
            page.set_viewport_size({'width': width, 'height': 960 if width > 700 else 844})
            for route, selector in [('overview', '.tc-overview-page'), ('task_mgmt', '.tc-platform-page'),
                                     ('windows_scheduler', '.tc-windows-page'), ('task_log', '.tc-log-page')]:
                page.goto(url + '/task_/' + route)
                page.locator(selector).wait_for(timeout=20000)
                table = page.locator(selector + ' .ant-table-tbody tr').first
                if route == 'overview':
                    page.wait_for_function("""() => {
                        const plot = document.querySelector('#tc-overview-chart .js-plotly-plot');
                        return plot?.data?.length > 0 && plot._fullLayout
                            && plot.querySelectorAll('.scatterlayer .points .point').length > 0;
                    }""")
                else:
                    table.wait_for(timeout=20000)
                    page.wait_for_function("document.querySelector('.tc-page .ant-table-tbody')?.innerText.includes('订单') || document.querySelector('.tc-page .ant-table-tbody')?.innerText.includes('Demo')")
                page.screenshot(path=str(output / f'task-center-{route}-{width}.png'))
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), (route, width)
                assert not page.locator('.tc-page .ant-alert-error').count(), (route, width)
        page.set_viewport_size({'width': 1440, 'height': 960})
        page.goto(url + '/task_/task_mgmt')
        page.locator('.tc-platform-page .ant-table-tbody').wait_for()
        page.locator('.tc-platform-page').get_by_text('新建任务', exact=True).click()
        page.locator('#tc-edit-job-id').wait_for()
        created_name = '展示验证-' + uuid4().hex[:8]
        page.locator('#tc-edit-job-id').fill(created_name)
        page.locator('#tc-edit-script').fill("print('DEMO ONLY')")
        page.get_by_text('预览未来 5 次', exact=True).click()
        page.locator('#tc-edit-preview-result').get_by_text('2026', exact=False).wait_for()
        page.screenshot(path=str(output / 'task-center-editor-1440.png'))
        for width in [390, 320]:
            page.set_viewport_size({'width': width, 'height': 844})
            page.screenshot(path=str(output / f'task-center-editor-{width}.png'))
            assert page.locator('.ant-modal:visible .ant-modal-footer').bounding_box()['y'] < 844
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        page.set_viewport_size({'width': 1440, 'height': 960})
        footer = page.locator('.ant-modal:visible .ant-modal-footer')
        footer.locator('button').last.click()
        page.get_by_text('已保存并暂停。核对后请显式恢复。', exact=True).wait_for()
        page.locator('#tc-platform-search').fill(created_name)
        created = page.locator('.tc-platform-page .ant-table-tbody tr').filter(has_text=created_name)
        created.get_by_text('恢复', exact=True).click()
        page.locator('.ant-modal:visible .ant-modal-footer button').last.click()
        created.get_by_text('启用', exact=True).wait_for()
        created.get_by_text('运行', exact=True).click()
        page.locator('.ant-modal:visible .ant-modal-footer button').last.click()
        page.get_by_text('调度器已接收请求，不代表运行成功。', exact=True).wait_for()
        page.goto(url + '/task_/windows_scheduler')
        native = page.locator('.tc-windows-page .ant-table-tbody tr').first
        native.get_by_text('暂停', exact=True).click()
        page.locator('.ant-modal:visible .ant-modal-footer button').last.click()
        native.get_by_text('启用', exact=True).wait_for()
        native.get_by_text('启用', exact=True).click()
        page.locator('.ant-modal:visible .ant-modal-footer button').last.click()
        native.get_by_text('暂停', exact=True).wait_for()
        with page.expect_download() as download:
            page.locator('.tc-windows-page').get_by_text('导出', exact=True).click()
        assert download.value.suggested_filename == 'windows-tasks.csv'
        page.goto(url + '/task_/task_log?run_id=demo-run-6-2')
        page.locator('.tc-log-page .ant-table-tbody').get_by_text('查看', exact=True).click()
        page.locator('#tc-log-resolve-reason').fill('演示核实：所有执行均为模拟，不存在外部进程。')
        page.get_by_text('提交核实', exact=True).click()
        page.get_by_text('运行已人工核实，审计记录已保存。', exact=True).wait_for()
        page.goto(url + '/task_/task_log')
        page.locator('.tc-log-page .ant-table-tbody').wait_for()
        page.get_by_text('查看', exact=True).first.click()
        page.locator('#tc-log-output').wait_for()
        page.wait_for_function("document.querySelector('#tc-log-output')?.innerText.includes('[DEMO]')")
        page.screenshot(path=str(output / 'task-center-log-detail-1440.png'))
        for width in [390, 320]:
            page.set_viewport_size({'width': width, 'height': 844})
            page.screenshot(path=str(output / f'task-center-log-detail-{width}.png'))
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        assert not errors, errors
        browser.close()


@pytest.mark.skipif(os.environ.get('PLATFORM_TASK_UI_PROBE') != '1', reason='opt-in isolated demo browser')
def test_scheduler_policy_panel_layout():
    from playwright.sync_api import sync_playwright

    url = os.environ.get('PLATFORM_TASK_DEMO_URL', 'http://127.0.0.1:8097')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url + '/demo')
        page.locator('.tc-overview-page').wait_for(timeout=30000)
        page.locator('.tc-scheduler-status-inner').wait_for(timeout=10000)
        page.locator('.tc-overview-settings > summary').click()
        page.locator('#tc-scheduler-policy-mode').wait_for()
        page.get_by_text('保存下次启动策略', exact=True).wait_for()
        page.get_by_text('确认启用任务调度', exact=True).wait_for(state='attached')
        assert not page.get_by_text('确认启用任务调度', exact=True).is_visible()
        for width in [1440, 1366, 390, 320]:
            page.set_viewport_size({'width': width, 'height': 960 if width > 700 else 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), width
        assert not errors, errors
        browser.close()