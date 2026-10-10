(function () {
    'use strict';
    if (window.platformTheme || !window.platformThemeConfig) return;
    const config = window.platformThemeConfig;
    const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    let connected = false;
    let preference = normalize(read());
    let appearance = null;
    let login = location.pathname === '/login';
    let state;

    function normalize(value) {
        value = value && typeof value === 'object' ? value : {};
        return {
            mode: ['light', 'dark', 'system'].includes(value.mode) ? value.mode : config.defaults.mode,
            palette: ['teal', 'blue', 'rose'].includes(value.palette) ? value.palette : config.defaults.palette,
            contrast: ['normal', 'high'].includes(value.contrast) ? value.contrast : config.defaults.contrast
        };
    }

    function read() {
        try { return JSON.parse(localStorage.getItem(config.storageKey)); }
        catch (_) { return null; }
    }

    function apply() {
        const resolved = login ? 'dark' : preference.mode === 'system'
            ? media && media.matches ? 'dark' : 'light' : preference.mode;
        const palette = login ? 'teal' : preference.palette;
        const spec = config.themes[`${resolved}:${palette}:${preference.contrast}`];
        const root = document.documentElement;
        root.dataset.theme = resolved;
        root.dataset.palette = palette;
        root.dataset.contrast = preference.contrast;
        root.style.colorScheme = resolved;
        Object.entries(spec.colors).forEach(([name, value]) => root.style.setProperty(`--platform-${name}`, value));
        state = Object.assign({}, preference, {resolvedMode: resolved, login: login});
        document.querySelectorAll('[data-theme-mode]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.themeMode === preference.mode));
        });
        document.querySelectorAll('[data-theme-palette]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.themePalette === preference.palette));
        });
        if (connected && window.dash_clientside && window.dash_clientside.set_props) {
            window.dash_clientside.set_props('platform-theme', {data: state});
            if (appearance) {
                window.dash_clientside.set_props('platform-theme-contrast', {checked: preference.contrast === 'high'});
                window.dash_clientside.set_props('main-menu', {theme: resolved});
            }
        }
        const monaco = window.monaco;
        if (monaco && monaco.editor) {
            monaco.editor.setTheme(preference.contrast === 'high'
                ? resolved === 'dark' ? 'hc-black' : 'hc-light'
                : resolved === 'dark' ? 'vs-dark' : 'vs');
        }
        return state;
    }

    function update(changes) {
        const next = normalize(Object.assign({}, preference, changes));
        if (JSON.stringify(next) === JSON.stringify(preference)) return state;
        preference = next;
        try { localStorage.setItem(config.storageKey, JSON.stringify(preference)); }
        catch (_) {}
        return apply();
    }

    function scan() {
        const container = document.getElementById('root-container');
        if (!container || !container.firstElementChild) return;
        const nextAppearance = document.getElementById('platform-appearance');
        const nextLogin = !!document.querySelector('.platform-login');
        if (nextAppearance !== appearance || nextLogin !== login) {
            appearance = nextAppearance;
            login = nextLogin;
            apply();
        }
    }

    window.platformTheme = {
        connect: function () { connected = true; scan(); return apply(); },
        update: update,
        state: function () { return state; },
        provider: function (value) {
            value = value || state;
            const palette = value.login ? 'teal' : normalize(value).palette;
            return config.themes[`${value.resolvedMode === 'dark' ? 'dark' : 'light'}:${palette}:${normalize(value).contrast}`];
        }
    };
    apply();
    if (media) {
        const changed = () => { if (preference.mode === 'system') apply(); };
        if (media.addEventListener) media.addEventListener('change', changed);
        else media.addListener(changed);
    }
    window.addEventListener('storage', event => {
        if (event.key !== config.storageKey && event.key !== null) return;
        preference = normalize(read());
        apply();
    });
    document.addEventListener('click', event => {
        const button = event.target.closest('[data-theme-mode], [data-theme-palette]');
        if (button) {
            update(button.dataset.themeMode ? {mode: button.dataset.themeMode} : {palette: button.dataset.themePalette});
        } else if (appearance && appearance.open && !appearance.contains(event.target)) {
            appearance.open = false;
        }
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && appearance && appearance.open) {
            event.preventDefault();
            event.stopImmediatePropagation();
            appearance.open = false;
            appearance.querySelector('summary').focus();
        }
    }, true);
    function watch() {
        new MutationObserver(scan).observe(document.body, {childList: true, subtree: true});
        scan();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch, {once: true});
    else watch();
})();