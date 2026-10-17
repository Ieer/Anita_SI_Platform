(() => {
    const mobile = window.matchMedia('(max-width: 700px)');
    let currentSidebar = null;
    let sidebarObserver = null;
    let modalOpen = false;
    let previousFocus = null;
    let activeContent = null;

    const closeDrawer = () => document.getElementById('platform-navigation-close')?.click();
    const syncDrawer = () => {
        const sidebar = document.getElementById('menu-collapse-sider');
        const content = document.querySelector('.platform-content-column');
        const agentPanel = document.getElementById('agent-panel');
        const agentOpen = Boolean(mobile.matches && agentPanel && !agentPanel.hidden);
        const open = Boolean(mobile.matches && sidebar && !sidebar.classList.contains('ant-layout-sider-collapsed') && !agentOpen);
        const toggle = document.getElementById('btn-menu-collapse-sider-menu');
        toggle?.setAttribute('aria-expanded', String(Boolean(sidebar && !sidebar.classList.contains('ant-layout-sider-collapsed'))));
        toggle?.setAttribute('aria-controls', 'platform-navigation');
        if (activeContent && activeContent !== content) activeContent.inert = false;
        if (content) content.inert = open || agentOpen;
        activeContent = content;
        if (sidebar) {
            if (open) {
                sidebar.setAttribute('role', 'dialog');
                sidebar.setAttribute('aria-modal', 'true');
                sidebar.setAttribute('aria-label', document.getElementById('platform-navigation')?.getAttribute('aria-label') || '');
            } else {
                sidebar.removeAttribute('role');
                sidebar.removeAttribute('aria-modal');
                sidebar.removeAttribute('aria-label');
            }
        }
        if (open && !modalOpen) {
            previousFocus = document.activeElement;
            document.getElementById('platform-navigation-close')?.focus();
        } else if (!open && modalOpen) {
            const target = previousFocus?.isConnected && !sidebar?.contains(previousFocus) ? previousFocus : toggle;
            target?.focus();
        }
        modalOpen = open;
    };

    const attachSidebar = () => {
        const sidebar = document.getElementById('menu-collapse-sider');
        if (sidebar !== currentSidebar) {
            sidebarObserver?.disconnect();
            currentSidebar = sidebar;
            if (sidebar) {
                sidebarObserver = new MutationObserver(syncDrawer);
                sidebarObserver.observe(sidebar, {attributes: true, attributeFilter: ['class']});
            }
            syncDrawer();
        }
    };
    new MutationObserver(attachSidebar).observe(document.documentElement, {childList: true, subtree: true});
    mobile.addEventListener('change', syncDrawer);
    document.addEventListener('click', (event) => {
        if (modalOpen && event.target.closest('#platform-navigation a[href]')) closeDrawer();
    });
    document.addEventListener('keydown', (event) => {
        if (!modalOpen) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            closeDrawer();
        } else if (event.key === 'Tab') {
            const controls = [...currentSidebar.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), [tabindex="0"]')]
                .filter((element) => element.getClientRects().length && !element.closest('[inert]'));
            const first = controls[0];
            const last = controls[controls.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
            }
        }
    });
    attachSidebar();
})();