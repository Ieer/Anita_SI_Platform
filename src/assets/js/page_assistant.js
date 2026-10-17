(() => {
    let initializing = false;
    let retryTimer = null;

    function disposeAgent() {
        if (retryTimer) {
            window.clearTimeout(retryTimer);
            retryTimer = null;
        }
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

    function initializeAgent() {
        const baseURL = `${window.location.origin}/page_agent_llm/v1`;
        if (window.pageAgent?.config?.baseURL === baseURL || initializing) return;
        const status = document.getElementById('page-agent-status');
        if (!status) {
            disposeAgent();
            return;
        }
        if (!status.classList.contains('page-agent-status-ready')) return;
        if (typeof window.PageAgent !== 'function') {
            if (!retryTimer) {
                retryTimer = window.setTimeout(() => {
                    retryTimer = null;
                    initializeAgent();
                }, 100);
            }
            return;
        }
        initializing = true;
        try {
            window.pageAgent?.dispose?.();
            window.pageAgent = new window.PageAgent({
                model: 'platform-default',
                baseURL,
                apiKey: 'page-agent-browser',
                language: document.documentElement.lang || 'zh-CN',
            });
            status.textContent = '页面助手已连接平台内网模型；任务执行仍需平台审批。';
        } catch (error) {
            initializing = false;
            status.textContent = '页面助手初始化失败，请检查内网 OpenAI 兼容模型配置。';
            status.classList.remove('page-agent-status-ready');
            status.classList.add('page-agent-status-error');
            console.error('Could not initialize page assistant.', error);
        }
    }

    function updateScope() {
        const scope = document.getElementById('page-agent-scope');
        if (!scope) {
            disposeAgent();
            return;
        }
        const context = document.querySelector('.page-agent-context[data-agent-policy]');
        if (!context) {
            const text = '当前页面：等待页面上下文';
            if (scope.textContent !== text) scope.textContent = text;
            return;
        }
        const page = context.dataset.page || 'unknown';
        const capabilities = (context.dataset.capabilities || '')
            .split(',')
            .map(value => value.trim())
            .filter(Boolean);
        const text = `当前页面：${page} · 辅助能力：${capabilities.join('、') || '无'} · 禁止执行任务`;
        if (scope.textContent !== text) scope.textContent = text;
    }

    const observer = new MutationObserver(() => {
        updateScope();
        initializeAgent();
    });
    observer.observe(document.body, {childList: true, subtree: true});
    updateScope();
    initializeAgent();
})();
