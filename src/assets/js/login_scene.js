(() => {
    let active = null;
    let pending = null;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');

    function buildScene(THREE, host) {
        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
        renderer.setClearColor(0x11191a, 1);
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
        renderer.domElement.setAttribute('aria-hidden', 'true');
        host.appendChild(renderer.domElement);
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
        camera.position.set(0, 1, 18);
        camera.lookAt(0, 0, 0);
        const network = new THREE.Group();
        scene.add(network);
        scene.add(new THREE.AmbientLight(0xe7f6f1, 2.1));
        const keyLight = new THREE.DirectionalLight(0xffffff, 3.8);
        keyLight.position.set(-3, 6, 8);
        scene.add(keyLight);
        const rimLight = new THREE.DirectionalLight(0x68dec0, 2.6);
        rimLight.position.set(5, -2, -3);
        scene.add(rimLight);

        const geometry = new THREE.BoxGeometry(0.56, 0.56, 0.56);
        const centerGeometry = new THREE.IcosahedronGeometry(1.45, 0);
        const palette = [0x72dec1, 0xdde9e4, 0xe8a569];
        const materials = palette.map(color => new THREE.MeshStandardMaterial({
            color, roughness: 0.28, metalness: 0.6, flatShading: true,
        }));
        const center = new THREE.Mesh(centerGeometry, materials[1]);
        network.add(center);
        const edgesGeometry = new THREE.EdgesGeometry(centerGeometry);
        const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x96eed8, transparent: true, opacity: 0.48 });
        center.add(new THREE.LineSegments(edgesGeometry, edgeMaterial));
        const vertices = [new THREE.Vector3(0, 0, 0)];
        const satellites = [];
        for (let index = 0; index < 18; index += 1) {
            const angle = index * Math.PI * 2 / 9;
            const radius = index < 9 ? 3.4 : 5.8;
            const position = new THREE.Vector3(
                Math.cos(angle) * radius, Math.sin(angle) * radius * 0.62,
                Math.sin(index * 2.1) * 1.8,
            );
            vertices.push(position);
            const node = new THREE.Mesh(geometry, materials[index % 3]);
            node.position.copy(position);
            node.rotation.set(index * 0.31, index * 0.52, 0.35);
            node.scale.setScalar(index < 9 ? 1 : 0.65);
            satellites.push(node);
            network.add(node);
        }
        const connections = [];
        const linkPoints = [];
        for (let index = 1; index < vertices.length; index += 1) {
            const parent = index <= 9 ? 0 : index - 9;
            connections.push([vertices[parent], vertices[index]]);
            linkPoints.push(vertices[parent], vertices[index]);
            if (index < 9) {
                linkPoints.push(vertices[index], vertices[index + 1]);
            }
        }
        const linkGeometry = new THREE.BufferGeometry().setFromPoints(linkPoints);
        const linkMaterial = new THREE.LineBasicMaterial({ color: 0x50756e, transparent: true, opacity: 0.65 });
        network.add(new THREE.LineSegments(linkGeometry, linkMaterial));
        const markerGeometry = new THREE.BoxGeometry(0.09, 0.09, 0.09);
        const markerMaterial = new THREE.MeshBasicMaterial({ color: 0xb3ffe3 });
        const markers = connections.map(() => {
            const marker = new THREE.Mesh(markerGeometry, markerMaterial);
            network.add(marker);
            return marker;
        });
        const grid = new THREE.GridHelper(40, 32, 0x36524a, 0x233732);
        grid.position.y = -5;
        grid.material.transparent = true;
        grid.material.opacity = 0.3;
        scene.add(grid);

        let frame = 0;
        let request = 0;
        let disposed = false;
        let elapsed = 0;
        let previous = performance.now();
        let pointerX = 0;
        let pointerY = 0;
        let narrow = false;
        function resize() {
            const width = host.clientWidth;
            const height = host.clientHeight;
            if (!width || !height) return;
            narrow = width <= 700;
            renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, narrow ? 1 : 1.5));
            renderer.setSize(width, height);
            camera.aspect = width / height;
            camera.position.z = narrow ? 9 : 18;
            camera.lookAt(0, 0, 0);
            camera.updateProjectionMatrix();
            network.position.set(narrow ? 0 : -2.8, narrow ? -1.35 : 0, 0);
            network.scale.setScalar(narrow ? 0.42 : 1);
            draw(performance.now());
        }
        function draw(now) {
            if (disposed) return;
            elapsed += Math.min((now - previous) / 1000, 0.05);
            previous = now;
            const time = motion.matches ? 0 : elapsed;
            network.rotation.y = 0.25 + Math.sin(time * 0.16) * 0.2 + pointerX * 0.16;
            network.rotation.x = -0.1 + pointerY * 0.1;
            center.rotation.set(time * 0.08, 0.6 + time * 0.13, 0.2);
            satellites.forEach((node, index) => {
                node.rotation.y = index * 0.52 + time * 0.12;
            });
            markers.forEach((marker, index) => {
                marker.position.lerpVectors(connections[index][0], connections[index][1],
                    (time * 0.12 + index * 0.19) % 1);
            });
            renderer.render(scene, camera);
            frame += 1;
            host.dataset.sceneFrame = String(frame);
        }
        function animate(now) {
            request = 0;
            if (disposed || document.hidden || motion.matches) return;
            draw(now);
            request = window.requestAnimationFrame(animate);
        }
        function schedule() {
            window.cancelAnimationFrame(request);
            request = 0;
            pointerX = 0;
            pointerY = 0;
            previous = performance.now();
            draw(previous);
            if (!document.hidden && !motion.matches) request = window.requestAnimationFrame(animate);
        }
        function move(event) {
            if (motion.matches) return;
            pointerX = (event.clientX / window.innerWidth - 0.5) * 2;
            pointerY = (event.clientY / window.innerHeight - 0.5) * 2;
        }
        function contextLost(event) {
            event.preventDefault();
            window.cancelAnimationFrame(request);
            host.dataset.sceneState = 'fallback';
        }
        renderer.domElement.addEventListener('webglcontextlost', contextLost);
        const observer = new ResizeObserver(resize);
        observer.observe(host);
        window.addEventListener('pointermove', move, { passive: true });
        document.addEventListener('visibilitychange', schedule);
        motion.addEventListener('change', schedule);
        resize();
        schedule();
        host.dataset.sceneState = 'ready';
        return {
            host,
            dispose() {
                disposed = true;
                window.cancelAnimationFrame(request);
                observer.disconnect();
                window.removeEventListener('pointermove', move);
                document.removeEventListener('visibilitychange', schedule);
                motion.removeEventListener('change', schedule);
                renderer.domElement.removeEventListener('webglcontextlost', contextLost);
                [geometry, centerGeometry, edgesGeometry, linkGeometry, markerGeometry, grid.geometry].forEach(item => item.dispose());
                [...materials, edgeMaterial, linkMaterial, markerMaterial, grid.material].forEach(item => item.dispose());
                renderer.dispose();
                renderer.forceContextLoss();
                renderer.domElement.remove();
            },
        };
    }

    function reconcile() {
        const host = document.getElementById('login-webgl');
        if (active && active.host === host) return;
        if (active) {
            active.dispose();
            active = null;
        }
        if (!host || pending === host || host.dataset.sceneState === 'fallback') return;
        pending = host;
        import('/assets/vendor/three/three.module.min.js').then(THREE => {
            if (document.getElementById('login-webgl') !== host) return;
            try {
                active = buildScene(THREE, host);
            } catch (_error) {
                host.dataset.sceneState = 'fallback';
            }
        }).catch(() => {
            if (host.isConnected) host.dataset.sceneState = 'fallback';
        }).finally(() => {
            if (pending === host) pending = null;
        });
    }
    const observer = new MutationObserver(reconcile);
    observer.observe(document.documentElement, { childList: true, subtree: true });
    reconcile();
    window.addEventListener('pagehide', () => {
        if (active) active.dispose();
        active = null;
    });
    window.addEventListener('pageshow', reconcile);
})();