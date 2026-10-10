(() => {
    let lastDisabled;
    const updateLogPolling = () => {
        const logPage = document.querySelector('.tc-log-page');
        const active = document.getElementById('tc-log-active');
        const output = document.getElementById('tc-log-output');
        if (!logPage || !active || !output || !window.dash_clientside?.set_props) {
            lastDisabled = undefined;
            return;
        }
        const disabled = document.hidden || !logPage.getClientRects().length ||
            !output.getClientRects().length || active.getAttribute('data-active') !== 'true';
        if (disabled === lastDisabled) return;
        lastDisabled = disabled;
        window.dash_clientside.set_props('tc-log-refresh', {disabled});
    };
    const start = () => {
        updateLogPolling();
        document.addEventListener('visibilitychange', updateLogPolling);
        new MutationObserver(updateLogPolling).observe(document.body, {
            subtree: true, childList: true, attributes: true,
            attributeFilter: ['class', 'style', 'hidden', 'data-active']
        });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
    else start();

    document.addEventListener('click', (event) => {
        const button = event.target.closest('[data-tc-copy]');
        if (!button) return;
        const value = button.getAttribute('data-tc-copy') || '';
        navigator.clipboard?.writeText(value).then(() => {
            button.setAttribute('aria-label', '已复制运行编号');
        }).catch(() => {});
    });
})();