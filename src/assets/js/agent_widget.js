(() => {
    const mobile = window.matchMedia('(max-width: 700px)');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let widget = null;
    let scene = null;
    let loadingHost = null;
    let previousFocus = null;
    let inertElements = [];
    let responseSignature = '';
    let lastState = 'idle';

    function agentI18n() {
        try { return JSON.parse(widget?.dataset.agentI18n || '{}'); }
        catch { return {}; }
    }

    function localizedLabel(key) {
        const labels = agentI18n().labels || {};
        return labels[key] || key;
    }

    function localizedMessage(message) {
        if (typeof message !== 'string') return message ?? '';
        const translations = agentI18n();
        const translated = translations.messages?.[message];
        if (translated) return translated;
        for (const entry of translations.patterns || []) {
            const match = message.match(new RegExp(entry.pattern));
            if (!match) continue;
            return entry.groups.reduce((result, name, index) => {
                const value = entry.translations?.[name]?.[match[index + 1]] || match[index + 1];
                return result.replace(`{${name}}`, value);
            }, entry.template);
        }
        return message;
    }

    function restoreBackground() {
        inertElements.forEach(([element, previous]) => {
            if (element.classList.contains('platform-content-column')) {
                const sidebar = document.getElementById('menu-collapse-sider');
                element.inert = Boolean(mobile.matches && sidebar &&
                    !sidebar.classList.contains('ant-layout-sider-collapsed') && widget?.querySelector('#agent-panel')?.hidden);
            } else element.inert = previous;
        });
        inertElements = [];
    }

    function syncModal() {
        restoreBackground();
        const panel = widget?.querySelector('#agent-panel');
        if (!panel) return;
        const modal = mobile.matches && !panel.hidden;
        panel.setAttribute('aria-modal', String(modal));
        if (modal) {
            const shell = widget.closest('.platform-shell');
            [...(shell?.children || [])].filter(element => element !== widget).forEach(element => {
                inertElements.push([element, element.inert]);
                element.inert = true;
            });
        }
    }

    function updateViewport() {
        const viewport = window.visualViewport;
        if (widget && viewport) {
            widget.style.setProperty('--agent-viewport-height', `${viewport.height}px`);
            widget.style.setProperty('--agent-viewport-top', `${viewport.offsetTop}px`);
        }
    }

    function focusInput() {
        const panel = widget?.querySelector('#agent-panel');
        if (!panel || panel.hidden) return;
        const input = panel.querySelector('textarea:not(:disabled)');
        (input || panel).focus({preventScroll: true});
    }

    function setOpen(open) {
        if (!widget) return;
        const panel = widget.querySelector('#agent-panel');
        const launcher = widget.querySelector('#agent-launcher');
        if (open) {
            previousFocus = document.activeElement;
            if (mobile.matches) document.getElementById('platform-navigation-close')?.click();
        }
        panel.hidden = !open;
        widget.dataset.open = String(open);
        launcher.setAttribute('aria-expanded', String(open));
        launcher.setAttribute('aria-label', localizedLabel(open ? 'closeChat' : 'openChat'));
        launcher.title = localizedLabel(open ? 'closeChat' : 'openChat');
        syncModal();
        if (open) {
            if (!widget.dataset.initialized) {
                widget.dataset.initialized = 'true';
                window.dash_clientside.set_props('agent-init', {disabled: false});
            }
            scene?.gesture('Wave');
            window.requestAnimationFrame(() => {
                syncModal();
                focusInput();
            });
        } else {
            const target = previousFocus?.isConnected && !panel.contains(previousFocus) ? previousFocus : launcher;
            target?.focus({preventScroll: true});
        }
    }

    function updateConversation() {
        if (!widget) return;
        const progress = widget.querySelector('#agent-progress');
        const error = widget.querySelector('#agent-status');
        const state = progress?.textContent.trim() ? 'thinking' : error?.textContent.trim() ? 'error' : 'idle';
        widget.dataset.agentState = state;
        if (state !== lastState) {
            lastState = state;
            scene?.state(state);
            widget.dispatchEvent(new CustomEvent('platform-agent-state', {detail: {state}, bubbles: true}));
        }
        const response = widget.querySelector('#agent-response-event');
        if (response?.dataset.turnId) {
            const text = response.textContent || '';
            const signature = response.dataset.turnId;
            if (signature !== responseSignature) {
                responseSignature = signature;
                if (state !== 'thinking') scene?.gesture('Yes');
                widget.dispatchEvent(new CustomEvent('platform-agent-response', {detail: {text}, bubbles: true}));
                const scroll = widget.querySelector('.agent-panel-scroll');
                scroll.scrollTop = scroll.scrollHeight;
            }
        }
    }

    function disposeModel(model) {
        const textures = new Set();
        model?.traverse(object => {
            object.geometry?.dispose();
            const materials = Array.isArray(object.material) ? object.material : [object.material];
            materials.filter(Boolean).forEach(material => {
                Object.values(material).filter(value => value?.isTexture).forEach(texture => textures.add(texture));
                material.dispose();
            });
        });
        textures.forEach(texture => texture.dispose());
    }

    async function buildScene(host) {
        const [THREE, {GLTFLoader}] = await Promise.all([
            import('/assets/vendor/three/three.module.min.js'),
            import('/assets/vendor/three/addons/loaders/GLTFLoader.js'),
        ]);
        if (!host.isConnected) return null;
        const gltf = await new GLTFLoader().loadAsync('/assets/vendor/three/models/RobotExpressive.glb');
        if (!host.isConnected) {
            disposeModel(gltf.scene);
            return null;
        }
        let renderer;
        try {
            renderer = new THREE.WebGLRenderer({alpha: true, antialias: true, preserveDrawingBuffer: true});
        } catch (error) {
            disposeModel(gltf.scene);
            throw error;
        }
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
        renderer.setSize(host.clientWidth, host.clientHeight);
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        renderer.setClearColor(0x000000, 0);
        const world = new THREE.Scene();
        world.add(new THREE.HemisphereLight(0xffffff, 0x708580, 2.4));
        const light = new THREE.DirectionalLight(0xffffff, 3);
        light.position.set(2, 6, 6);
        world.add(light);
        const model = gltf.scene;
        world.add(model);
        const mixer = new THREE.AnimationMixer(model);
        const actions = Object.fromEntries(gltf.animations.map(clip => [clip.name, mixer.clipAction(clip)]));
        let currentAction = actions.Idle;
        currentAction?.play();
        mixer.update(0.5);
        const bounds = new THREE.Box3().setFromObject(model);
        const center = bounds.getCenter(new THREE.Vector3());
        const height = bounds.getSize(new THREE.Vector3()).y * 0.7;
        const aspect = host.clientWidth / host.clientHeight;
        const camera = new THREE.OrthographicCamera(-height * aspect, height * aspect, height, -height, 0.1, 100);
        camera.position.set(center.x, center.y + 0.1, 10);
        camera.lookAt(center);
        let request = 0;
        let disposed = false;
        let failed = false;
        let previous = performance.now();
        let frame = 0;
        let state = 'idle';
        let pointer = 0;

        function gesture(name) {
            if (disposed || failed || reducedMotion.matches || !actions[name]) return;
            currentAction?.fadeOut(0.15);
            currentAction = actions[name];
            currentAction.reset().setLoop(THREE.LoopOnce, 1).setEffectiveWeight(1).fadeIn(0.15).play();
            currentAction.clampWhenFinished = true;
        }
        function finishGesture() {
            currentAction?.fadeOut(0.2);
            currentAction = actions.Idle;
            currentAction?.reset().fadeIn(0.2).play();
        }
        mixer.addEventListener('finished', finishGesture);
        function draw(now) {
            const delta = Math.min((now - previous) / 1000, 0.06);
            previous = now;
            if (!reducedMotion.matches) mixer.update(delta);
            model.rotation.y = reducedMotion.matches ? 0.12 : 0.12 + pointer * 0.2;
            const head = model.getObjectByName('Head_4');
            if (head?.morphTargetDictionary?.Surprised !== undefined) {
                head.morphTargetInfluences[head.morphTargetDictionary.Surprised] = state === 'thinking' ? 0.35 : 0;
            }
            renderer.render(world, camera);
            host.dataset.sceneFrame = String(++frame);
        }
        function animate(now) {
            request = 0;
            if (disposed || failed || document.hidden || reducedMotion.matches) return;
            if (now - previous >= 32) draw(now);
            request = window.requestAnimationFrame(animate);
        }
        function schedule() {
            window.cancelAnimationFrame(request);
            request = 0;
            if (disposed || failed) return;
            previous = performance.now();
            if (!document.hidden) draw(previous);
            if (!document.hidden && !reducedMotion.matches) request = window.requestAnimationFrame(animate);
        }
        function contextLost(event) {
            event.preventDefault();
            failed = true;
            window.cancelAnimationFrame(request);
            host.dataset.sceneState = 'fallback';
            host.closest('#agent-widget')?.setAttribute('data-scene-state', 'fallback');
        }
        function move(event) {
            const rect = host.getBoundingClientRect();
            pointer = Math.max(-1, Math.min(1, (event.clientX - rect.left) / rect.width * 2 - 1));
        }
        function leave() { pointer = 0; }
        function resize() {
            renderer.setSize(host.clientWidth, host.clientHeight);
            const ratio = host.clientWidth / host.clientHeight;
            camera.left = -height * ratio;
            camera.right = height * ratio;
            camera.updateProjectionMatrix();
            schedule();
        }
        const resizeObserver = new ResizeObserver(resize);
        resizeObserver.observe(host);
        renderer.domElement.addEventListener('webglcontextlost', contextLost);
        host.addEventListener('pointermove', move, {passive: true});
        host.addEventListener('pointerleave', leave);
        document.addEventListener('visibilitychange', schedule);
        reducedMotion.addEventListener('change', schedule);
        host.appendChild(renderer.domElement);
        host.dataset.sceneState = 'ready';
        host.closest('#agent-widget').dataset.sceneState = 'ready';
        schedule();
        return {
            host, gesture,
            state(value) { state = value; schedule(); },
            dispose() {
                disposed = true;
                window.cancelAnimationFrame(request);
                resizeObserver.disconnect();
                document.removeEventListener('visibilitychange', schedule);
                reducedMotion.removeEventListener('change', schedule);
                host.removeEventListener('pointermove', move);
                host.removeEventListener('pointerleave', leave);
                renderer.domElement.removeEventListener('webglcontextlost', contextLost);
                mixer.removeEventListener('finished', finishGesture);
                mixer.stopAllAction();
                mixer.uncacheRoot(model);
                disposeModel(model);
                renderer.dispose();
                renderer.forceContextLoss();
                renderer.domElement.remove();
            },
        };
    }

    function reconcile() {
        const current = document.getElementById('agent-widget');
        if (current !== widget) {
            restoreBackground();
            scene?.dispose();
            scene = null;
            widget = current;
            responseSignature = '';
            lastState = 'idle';
            updateViewport();
        }
        const host = widget?.querySelector('#agent-robot-scene');
        if (host && !scene && loadingHost !== host && host.dataset.sceneState !== 'fallback') {
            loadingHost = host;
            buildScene(host).then(result => {
                if (host.isConnected && widget?.contains(host)) {
                    scene = result;
                    scene?.state(lastState);
                } else result?.dispose();
            }).catch(() => {
                if (host.isConnected) {
                    host.dataset.sceneState = 'fallback';
                    host.closest('#agent-widget').dataset.sceneState = 'fallback';
                }
            }).finally(() => { if (loadingHost === host) loadingHost = null; });
        }
        updateConversation();
    }

    function setAgentProps(id, properties) {
        if (typeof window.dash_clientside?.set_props !== 'function') {
            throw new Error(localizedLabel('controlsUnavailable'));
        }
        window.dash_clientside.set_props(id, properties);
    }

    async function sendAgentMessage() {
        const promptControl = widget?.querySelector('#agent-prompt');
        const input = promptControl?.matches('textarea, input')
            ? promptControl : promptControl?.querySelector('textarea, input');
        const conversationId = widget?.querySelector('#agent-conversation-title')?.dataset.conversationId;
        const profileId = widget?.querySelector('#agent-conversation-title')?.dataset.profileId;
        const prompt = input?.value?.trim();
        if (!prompt || !conversationId || widget.dataset.streaming === 'true') return;

        const consentControl = widget.querySelector('#agent-log-consent');
        const consentInput = consentControl?.matches('input[type="checkbox"]')
            ? consentControl : consentControl?.querySelector('input[type="checkbox"]');
        const consent = consentInput ? consentInput.checked :
            consentControl?.getAttribute('aria-checked') === 'true';
        let text = `${localizedLabel('youPrefix')}${prompt}\nAnita：`;
        let receivedCompletion = false;
        widget.dataset.streaming = 'true';
        setAgentProps('agent-stream-output', {children: text});
        setAgentProps('agent-status', {children: ''});
        setAgentProps('agent-progress', {children: localizedLabel('connecting')});
        setAgentProps('agent-send', {loading: true, disabled: true});
        setAgentProps('agent-prompt', {disabled: true});
        setAgentProps('agent-provider-profile', {disabled: true});

        const refreshMessages = () => setAgentProps('agent-stream-refresh', {data: `${Date.now()}-${Math.random()}`});
        const handleFrame = frame => {
            if (!frame.trim()) return;
            let eventName = 'message';
            let data = '';
            for (const line of frame.split(/\r?\n/)) {
                if (line.startsWith('event:')) eventName = line.slice(6).trim();
                else if (line.startsWith('data:')) data += line.slice(5).trim();
            }
            let payload;
            try { payload = JSON.parse(data || '{}'); }
            catch { throw new Error(localizedLabel('invalidStreamEvent')); }
            if (eventName === 'delta') {
                text += payload.text || '';
                setAgentProps('agent-stream-output', {children: text});
                const scroll = widget.querySelector('.agent-panel-scroll');
                if (scroll) scroll.scrollTop = scroll.scrollHeight;
            } else if (eventName === 'status') {
                setAgentProps('agent-progress', {
                    children: localizedMessage(payload.text) || localizedLabel('processing'),
                });
            } else if (eventName === 'completed') {
                receivedCompletion = true;
                setAgentProps('agent-progress', {children: ''});
                setAgentProps('agent-stream-output', {children: ''});
                refreshMessages();
            } else if (eventName === 'error') {
                setAgentProps('agent-status', {
                    children: localizedMessage(payload.message || localizedLabel('requestFailed')),
                });
                setAgentProps('agent-stream-output', {children: ''});
                refreshMessages();
                throw new Error('stream-error');
            }
        };

        try {
            const response = await fetch('/agent_stream', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json', 'Accept': 'text/event-stream'},
                body: JSON.stringify({
                    conversation_id: conversationId,
                    profile_id: profileId || null,
                    prompt,
                    allow_log_export: consent === true,
                }),
            });
            if (!response.ok) {
                let message = `Agent 请求失败（HTTP ${response.status}）。`;
                try { message = (await response.json()).error || message; } catch {}
                throw new Error(localizedMessage(message));
            }
            if (!response.body) throw new Error(localizedLabel('unsupportedStream'));
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            while (true) {
                const {value, done} = await reader.read();
                buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
                let boundary;
                while ((boundary = buffer.search(/\r?\n\r?\n/)) >= 0) {
                    const frame = buffer.slice(0, boundary);
                    const delimiter = buffer.slice(boundary).match(/^\r?\n\r?\n/)[0];
                    buffer = buffer.slice(boundary + delimiter.length);
                    handleFrame(frame);
                }
                if (done) break;
            }
            if (buffer.trim()) handleFrame(buffer);
            if (!receivedCompletion) throw new Error(localizedLabel('streamInterrupted'));
        } catch (error) {
            if (error.message !== 'stream-error') {
                setAgentProps('agent-status', {
                    children: localizedMessage(error.message || localizedLabel('requestFailed')),
                });
            }
            setAgentProps('agent-progress', {children: ''});
            setAgentProps('agent-stream-output', {children: ''});
        } finally {
            widget.dataset.streaming = 'false';
            setAgentProps('agent-send', {loading: false, disabled: false});
            setAgentProps('agent-prompt', {disabled: false, value: ''});
            setAgentProps('agent-provider-profile', {disabled: false});
            input?.focus({preventScroll: true});
        }
    }

    document.addEventListener('click', event => {
        const send = event.target.closest?.('#agent-send');
        if (!send || !widget || widget.dataset.streaming === 'true') return;
        event.preventDefault();
        event.stopImmediatePropagation();
        sendAgentMessage();
    }, true);

    document.addEventListener('click', event => {
        if (!widget) return;
        if (event.target.closest('#agent-launcher')) setOpen(widget.querySelector('#agent-panel').hidden);
        if (event.target.closest('#agent-close')) setOpen(false);
        if (event.target.closest('#agent-expand')) {
            const expanded = widget.dataset.expanded !== 'true';
            widget.dataset.expanded = String(expanded);
            const button = widget.querySelector('#agent-expand');
            button.title = localizedLabel(expanded ? 'restoreChat' : 'expandChat');
            button.setAttribute('aria-label', button.title);
            button.setAttribute('aria-pressed', String(expanded));
        }
    });
    document.addEventListener('keydown', event => {
        const panel = widget?.querySelector('#agent-panel');
        if (!panel || panel.hidden) return;
        if (event.key === 'Escape' && !document.querySelector('.ant-modal-wrap:not([style*="display: none"])')) {
            event.preventDefault();
            event.stopImmediatePropagation();
            setOpen(false);
        } else if (event.key === 'Tab' && mobile.matches) {
            const controls = [...panel.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]')]
                .filter(element => element.getClientRects().length && !element.closest('[inert]'));
            const first = controls[0];
            const last = controls[controls.length - 1];
            if (event.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
                event.preventDefault();
                last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
            }
        }
    });
    window.platformAgent = Object.freeze({
        setInput(text) {
            if (!widget || typeof text !== 'string' || text.length > 4000) return false;
            const input = widget.querySelector('textarea');
            if (!input || input.disabled) return false;
            window.dash_clientside.set_props('agent-prompt', {value: text});
            setOpen(true);
            return true;
        },
    });
    new MutationObserver(reconcile).observe(document.documentElement, {childList: true, subtree: true, characterData: true});
    mobile.addEventListener('change', () => {
        updateViewport();
        if (mobile.matches && widget && !widget.querySelector('#agent-panel').hidden) {
            document.getElementById('platform-navigation-close')?.click();
        }
        window.requestAnimationFrame(syncModal);
    });
    window.visualViewport?.addEventListener('resize', updateViewport);
    window.visualViewport?.addEventListener('scroll', updateViewport);
    window.addEventListener('pagehide', () => { restoreBackground(); scene?.dispose(); scene = null; });
    window.addEventListener('pageshow', reconcile);
    reconcile();
})();