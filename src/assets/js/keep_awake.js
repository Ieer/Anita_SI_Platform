(() => {
    let enabled = false;
    let wakeLock = null;

    async function acquire() {
        if (!enabled || !('wakeLock' in navigator)) return false;
        try {
            wakeLock = await navigator.wakeLock.request('screen');
            wakeLock.addEventListener('release', () => {
                wakeLock = null;
                if (enabled && document.visibilityState === 'visible') acquire();
            });
            return true;
        } catch (error) {
            wakeLock = null;
            console.warn('Unable to keep the device awake:', error);
            return false;
        }
    }

    document.addEventListener('visibilitychange', () => {
        if (enabled && document.visibilityState === 'visible') acquire();
    });

    window.platformKeepAwake = {
        set(value) {
            enabled = Boolean(value);
            if (!enabled) {
                wakeLock?.release();
                wakeLock = null;
                return true;
            }
            return acquire();
        },
    };
})();
