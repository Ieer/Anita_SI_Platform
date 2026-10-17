"""Record the isolated SI-Platform demo with Playwright's built-in video capture."""

import argparse
import os
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = os.environ.get('PLATFORM_TASK_DEMO_URL', 'http://127.0.0.1:8097')
DEFAULT_OUTPUT = ROOT / 'screenshots' / 'si-platform-mvp-demo.webm'
INTRO_HTML = '''<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <style>
        * { box-sizing: border-box; }
        html, body { width: 100%; height: 100%; margin: 0; overflow: hidden; }
        body { background: #111a1d; color: #edf4f1; font-family: "Bahnschrift", "Segoe UI", sans-serif; }
        #demo-app { position: fixed; inset: 0; width: 100%; height: 100%; border: 0; background: #f1f4f4; }
        #demo-intro { position: fixed; inset: 0; z-index: 2; display: flex; flex-direction: column;
            justify-content: center; padding: 0 10%; background: #111a1d; opacity: 1;
            transition: opacity 650ms ease; }
        #demo-intro.is-hidden { opacity: 0; pointer-events: none; }
        .brand { display: flex; align-items: center; gap: 18px; }
        .brand img { display: block; object-fit: contain; }
        .mark { width: 68px; height: 68px; border-radius: 16px; }
        .wordmark { width: 132px; height: 42px; filter: brightness(1.4); }
        .rule { width: 112px; height: 3px; margin: 48px 0 28px; background: #5bd4be; }
        h1 { margin: 0; font-size: 54px; line-height: 1.12; font-weight: 600; }
        .subtitle { margin: 18px 0 0; color: #becbc9; font-size: 22px; }
        .foot { position: absolute; right: 10%; bottom: 54px; color: #8fa29f; font-size: 12px;
            letter-spacing: 1.5px; }
        @media (prefers-reduced-motion: reduce) { #demo-intro { transition: none; } }
    </style>
</head>
<body>
    <iframe id="demo-app" title="SI-Platform isolated demo" src="__BASE__/dashboard_/monitor"></iframe>
    <main id="demo-intro" aria-label="SI-Platform MVP demo">
        <div class="brand">
            <img class="mark" src="__BASE__/assets/imgs/digital-human-mark.svg" alt="">
            <img class="wordmark" src="__BASE__/assets/imgs/anita-wordmark.svg" alt="Anita">
        </div>
        <div class="rule"></div>
        <h1>SI-Platform</h1>
        <p class="subtitle">Human-governed AI operations</p>
        <div class="foot">DETERMINISTIC DEMO &nbsp; / &nbsp; NO EXTERNAL EXECUTION</div>
    </main>
</body>
</html>'''


def record_demo(url=DEFAULT_URL, output=DEFAULT_OUTPUT):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    page_errors = []

    with tempfile.TemporaryDirectory(prefix='si-platform-playwright-video-') as video_dir:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            bootstrap = browser.new_context(
                viewport={'width': 1600, 'height': 900},
                locale='zh-CN',
                timezone_id='Asia/Shanghai',
            )
            bootstrap_page = bootstrap.new_page()
            bootstrap_page.goto(url.rstrip('/') + '/demo')
            bootstrap_page.locator('#platform-demo-banner').wait_for(timeout=30000)
            storage_state = bootstrap.storage_state()
            bootstrap.close()

            context = browser.new_context(
                viewport={'width': 1600, 'height': 900},
                record_video_dir=video_dir,
                record_video_size={'width': 1600, 'height': 900},
                color_scheme='light',
                locale='zh-CN',
                timezone_id='Asia/Shanghai',
                reduced_motion='reduce',
                storage_state=storage_state,
            )
            page = context.new_page()
            page.on('pageerror', lambda error: page_errors.append(str(error)))
            video = page.video
            page.goto(url.rstrip('/') + '/dashboard_/monitor')
            app = page
            app.locator('#platform-demo-banner').wait_for(timeout=30000)
            app.locator('#system-monitor').wait_for(timeout=45000)
            app.locator('#monitor-summary .monitor-metric-value').first.wait_for(
                state='attached', timeout=45000)
            for metric in ['93.0%', '86.0%', '92.0%']:
                app.locator('#monitor-summary').get_by_text(metric, exact=True).wait_for(timeout=15000)
            app.locator('#monitor-disk-desc .ant-table-row:not(.ant-table-measure-row)').filter(
                has_text='DEMO-VOLUME').wait_for(state='visible', timeout=20000)
            app.locator('#monitor-sys-desc').get_by_text('DEMO-NODE-01', exact=True).wait_for(timeout=15000)
            plot = app.locator('#monitor-trend .js-plotly-plot')
            plot.wait_for(state='visible', timeout=15000)
            page.wait_for_function(
                """() => {
                    const plot = document.querySelector('#monitor-trend .js-plotly-plot');
                    return plot?.data?.some(trace => Array.isArray(trace.x) && trace.x.length > 0);
                }""",
                timeout=15000,
            )
            app.locator('#monitor-refresh').click()
            for _ in range(60):
                point_count = plot.evaluate(
                    "node => Math.min(...(node.data || []).map(trace => trace.x?.length || 0))")
                if point_count >= 2:
                    break
                page.wait_for_timeout(200)
            else:
                raise AssertionError('The synthetic monitor trend did not receive two samples.')

            app.locator('#agent-launcher').click()
            app.locator('#agent-panel').wait_for(state='visible', timeout=15000)
            page_agent = app.locator('#page-agent-launcher')
            page_agent.wait_for(state='visible', timeout=15000)
            page_agent.click()
            app.locator('#page-agent-status.page-agent-status-ready').wait_for(
                state='visible', timeout=15000)
            page.wait_for_function(
                "() => document.querySelector('#page-agent-status')?.classList.contains('page-agent-status-ready')",
                timeout=15000,
            )
            page.wait_for_timeout(1200)

            page.wait_for_function(
                "() => { const button = document.querySelector('#agent-send'); return button && !button.disabled; }",
                timeout=20000,
            )
            app.locator('#agent-new-conversation').click()
            page.wait_for_function(
                "() => { const button = document.querySelector('#agent-send'); return button && !button.disabled; }",
                timeout=15000,
            )
            page.wait_for_timeout(400)

            prompt = app.locator('#agent-prompt')
            send = app.locator('#agent-send')
            prompt.fill('\u5206\u6790\u5f53\u524d\u544a\u8b66')
            send.click()
            app.locator('.agent-message-assistant').last.wait_for(state='visible', timeout=20000)
            page.wait_for_function(
                "() => [...document.querySelectorAll('.agent-message-assistant')].some(node => node.innerText.includes('93%'))",
                timeout=15000,
            )
            app.locator('.agent-panel-scroll').evaluate('(node) => { node.scrollTop = node.scrollHeight; }')
            page.wait_for_timeout(900)

            prompt.fill('\u67e5\u770b\u7968\u636e\u5f52\u6863\u7684\u6700\u8fd1\u8fd0\u884c\u65e5\u5fd7')
            send.click()
            app.locator('.agent-message-assistant').last.wait_for(state='visible', timeout=15000)
            page.wait_for_function(
                "() => [...document.querySelectorAll('.agent-message-assistant')].some(node => node.innerText.includes('\u6ca1\u6709\u8bfb\u53d6\u4efb\u4f55\u65e5\u5fd7'))",
                timeout=15000,
            )
            app.locator('.agent-panel-scroll').evaluate('(node) => { node.scrollTop = node.scrollHeight; }')
            page.wait_for_timeout(700)

            app.locator('#agent-log-consent').check()
            prompt.fill('\u67e5\u770b\u7968\u636e\u5f52\u6863\u7684\u6700\u8fd1\u8fd0\u884c\u65e5\u5fd7')
            send.click()
            app.locator('.agent-message-assistant').last.wait_for(state='visible', timeout=15000)
            page.wait_for_function(
                "() => [...document.querySelectorAll('.agent-message-assistant')].some(node => node.innerText.includes('\u6700\u8fd1\u8fd0\u884c\u72b6\u6001\u4e3a failed'))",
                timeout=15000,
            )
            app.locator('.agent-trace').last.wait_for(state='visible', timeout=10000)
            app.locator('.agent-panel-scroll').evaluate('(node) => { node.scrollTop = node.scrollHeight; }')
            page.wait_for_timeout(900)

            prompt.fill('\u8fd0\u884c\u4efb\u52a1\u7968\u636e\u5f52\u6863')
            send.click()
            app.locator('.agent-message-assistant').last.wait_for(state='visible', timeout=15000)
            page.wait_for_function(
                "() => [...document.querySelectorAll('.agent-message-assistant')].some(node => node.innerText.includes('\u53ea\u63d0\u4f9b\u53ea\u8bfb\u5206\u6790'))",
                timeout=15000,
            )
            if app.locator('#agent-approvals').inner_text().strip():
                raise AssertionError('The isolated demo must not create task approvals.')
            app.locator('.agent-panel-scroll').evaluate('(node) => { node.scrollTop = node.scrollHeight; }')
            page.wait_for_timeout(1800)

            context.close()
            video_path = Path(video.path())
            shutil.copy2(video_path, output)
            browser.close()

    if page_errors:
        raise AssertionError(f'Browser page errors occurred during recording: {page_errors}')
    if not output.is_file() or output.stat().st_size < 100_000:
        raise AssertionError(f'Playwright video was not created correctly: {output}')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=DEFAULT_URL, help='Base URL of the isolated demo service.')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT, help='Destination WebM video path.')
    args = parser.parse_args()
    print(record_demo(args.url, args.output))


if __name__ == '__main__':
    main()
