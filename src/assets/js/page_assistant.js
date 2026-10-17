(() => {
    let initializing = false;
    let retryTimer = null;
    let loadingSince = null;

    function translate(text) {
        const root = document.getElementById('page-assistant');
        const labels = JSON.parse(root?.dataset.pageAgentI18n || '{}');
        return labels[text] || text;
    }

    function disposeAgent() {
        if (retryTimer) {
            window.clearTimeout(retryTimer);
            retryTimer = null;
        }
        loadingSince = null;
        initializing = false;
        if (!window.pageAgent) return;
        try {
            window.pageAgent.dispose?.();
        } catch (error) {
            console.error('Could not dispose page assistant.', error);
        } finally {
            window.pageAgent = undefined;
        }
    }

    function setState(state, text) {
        const status = document.getElementById('page-agent-status');
        const button = document.getElementById('page-agent-launcher');
        if (!status || !button) return;
        const className = `page-agent-status page-agent-status-${state}`;
        const message = translate(text);
        const statusProps = {};
        if (status.className !== className) statusProps.className = className;
        if (status.textContent !== message) statusProps.children = message;
        if (Object.keys(statusProps).length) {
            window.dash_clientside?.set_props('page-agent-status', statusProps);
            if (statusProps.className) status.className = className;
            if (statusProps.children) status.textContent = message;
        }
        const disabled = state !== 'error';
        if (button.disabled !== disabled) {
            window.dash_clientside?.set_props('page-agent-launcher', {disabled});
            button.disabled = disabled;
        }
        button.dataset.state = state;
        const label = button.querySelector('span:last-child');
        const labelText = translate({loading: '加载中…', ready: '已启用', error: '重试'}[state]);
        if (label && label.textContent !== labelText) label.textContent = labelText;
        button.setAttribute('aria-label', labelText);
        button.setAttribute('aria-busy', String(state === 'loading'));
        if (state === 'error') {
            initializing = false;
            loadingSince = null;
            if (retryTimer) window.clearTimeout(retryTimer);
            retryTimer = null;
        }
    }

    function initializeAgent() {
        const status = document.getElementById('page-agent-status');
        if (!status) {
            disposeAgent();
            return;
        }
        if (status.classList.contains('page-agent-status-error')) {
            setState('error', status.textContent);
            return;
        }
        if (!status.classList.contains('page-agent-status-loading') || initializing) return;
        setState('loading', '正在加载页面辅助…');
        if (typeof window.PageAgent !== 'function') {
            loadingSince ??= Date.now();
            if (Date.now() - loadingSince >= 15000) {
                setState('error', '加载失败或超时，请重试。');
                return;
            }
            if (!retryTimer) {
                retryTimer = window.setTimeout(() => {
                    retryTimer = null;
                    initializeAgent();
                }, 100);
            }
            return;
        }
        initializing = true;
        loadingSince = null;
        if (retryTimer) window.clearTimeout(retryTimer);
        retryTimer = null;
        try {
            window.pageAgent?.dispose?.();
            window.pageAgent = new window.PageAgent({
                model: 'platform-default',
                baseURL: `${window.location.origin}/page_agent_llm/v1`,
                apiKey: 'page-agent-browser',
                language: document.documentElement.lang || 'zh-CN',
            });
            setState('ready', '工具已就绪，模型连接将在使用时建立。');
        } catch (error) {
            window.pageAgent = undefined;
            setState('error', '加载失败或超时，请重试。');
            console.error('Could not initialize page assistant.', error);
        }
    }

    function updateScope() {
        const scope = document.getElementById('page-agent-scope');
        if (!scope) {
            disposeAgent();
            return;
        }
        const context = Array.from(document.querySelectorAll('.page-agent-context[data-agent-policy]'))
            .find(element => element.parentElement?.getClientRects().length
                && !element.parentElement.closest('[hidden], [aria-hidden="true"]'));
        const pages = {monitor: '系统监控', 'task-overview': '任务概览', 'task-log': '任务日志'};
        const tools = {
            read_metrics: '读取指标', open_task_center: '打开任务中心', list_tasks: '查询任务',
            open_task_log: '打开任务日志', filter_task: '筛选任务',
            read_sanitized_log: '读取脱敏日志', open_task_overview: '打开任务概览',
        };
        const page = context?.dataset.page;
        const text = translate(page ? (pages[page] || '当前页面') : '等待页面上下文');
        if (scope.textContent !== text) scope.textContent = text;
        scope.title = text;
        const capabilities = (context?.dataset.capabilities || '').split(',')
            .map(value => tools[value.trim()]).filter(Boolean).map(translate);
        const description = capabilities.join(' / ') || translate('当前页面暂无辅助能力');
        const target = document.getElementById('page-agent-capabilities');
        if (target && target.textContent !== description) target.textContent = description;
    }

    const detailSelector = '.agent-tools-toolbar details';
    document.addEventListener('click', event => {
        document.querySelectorAll(`${detailSelector}[open]`).forEach(details => {
            if (!details.contains(event.target)) details.open = false;
        });
    });
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        const details = document.querySelector(`${detailSelector}[open]`);
        if (!details) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        details.open = false;
        details.querySelector('summary')?.focus();
    }, true);
    document.addEventListener('toggle', event => {
        if (!event.target.matches?.(detailSelector) || !event.target.open) return;
        document.querySelectorAll(`${detailSelector}[open]`).forEach(details => {
            if (details !== event.target) details.open = false;
        });
    }, true);

    const observer = new MutationObserver(() => {
        updateScope();
        initializeAgent();
    });
    observer.observe(document.body, {childList: true, subtree: true, attributes: true,
        attributeFilter: ['class', 'style', 'hidden', 'aria-hidden', 'data-page-agent-i18n']});
    updateScope();
    initializeAgent();
})();
