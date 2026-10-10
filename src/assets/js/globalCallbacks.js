// 改造console.error()以隐藏无关痛痒的警告信息
const originalConsoleError = console.error;
console.error = function (...args) {
    // 检查args中是否包含需要过滤的内容
    const shouldFilter = args.some(arg => typeof arg === 'string' && arg.includes('Warning:'));

    if (!shouldFilter) {
        originalConsoleError.apply(console, args);
    }
};

// function getCookie(name) {
//     const cookies = document.cookie.split(';');
//     for (const cookie of cookies) {
//       const [cookieName, cookieValue] = cookie.trim().split('=');
//       if (cookieName === name) {
//         return decodeURIComponent(cookieValue); // 解码特殊字符（如空格、中文）
//       }
//     }
//     return null; // 未找到返回 null
//   }
  
//   document.addEventListener('DOMContentLoaded', function() {
//     const originalFetch = window.fetch;
//     window.fetch = function(url, config) {
//         // if (url.includes('/_dash-update-component')) {
//             config = config || {};
//             let authToken = null;
//             // 检查授权 cookie
//             authToken = getCookie("access_token");
//             // 如果存在 Token，添加 Header
//             if (authToken !== null && authToken !== '' && authToken != '""') {
//                 authToken = authToken.replace(/"/g, '')
//                 if (!authToken.startsWith('Bearer ')) {
//                     authToken = 'Bearer ' + authToken;
//                 }
//                 config.headers = {
//                     ...(config.headers || {}),
//                     Authorization: authToken // 添加 Authorization 
//                 };
//             }
//         // }
//         return originalFetch(url, config);
//     };
// });

// 自動量測甘特圖 Header 高度並注入 CSS 變數，避免手動同步 Python 與 CSS 的 HEADER_HEIGHT
// 透過設定 --scheduler-header-height 與 --scheduler-header-delta (相對於 34px 的差值)
// 並在 CSS 內以 transform: translateY(var(--scheduler-header-delta)) 統一修正任務與當前時間線的位置
(function(){
    const BASE_HEADER_HEIGHT = 34; // 與 Python create_scheduler.py 的常數保持一致

    function applyHeaderVars(root){
        try{
            if(!root) return;
            const header = root.querySelector('.scheduler-header');
            if(!header) return;
            const rect = header.getBoundingClientRect();
            const h = Math.max(0, Math.round(rect.height));
            const delta = h - BASE_HEADER_HEIGHT;
            root.style.setProperty('--scheduler-header-height', h + 'px');
            root.style.setProperty('--scheduler-header-delta', (delta >= 0 ? '+' : '') + delta + 'px');
        }catch(e){
            // 靜默失敗，避免影響其他功能
        }
    }

    function setupForRoot(root){
        if(!root || root.__schedulerMeasureSetup__) return;
        root.__schedulerMeasureSetup__ = true;
        applyHeaderVars(root);
        const header = root.querySelector('.scheduler-header');
        const ro = new ResizeObserver(()=>applyHeaderVars(root));
        ro.observe(root);
        if(header){
            ro.observe(header);
        }
        window.addEventListener('load', ()=>applyHeaderVars(root));
    }

    function initSchedulerMeasure(){
        const root = document.getElementById('scheduler-component');
        if(root){
            setupForRoot(root);
            return;
        }
        // 若初始不存在，觀察 DOM 直到插入
        const mo = new MutationObserver(()=>{
            const r = document.getElementById('scheduler-component');
            if(r){
                setupForRoot(r);
                mo.disconnect();
            }
        });
        mo.observe(document.body, {subtree:true, childList:true});
    }

    if(document.readyState === 'loading'){
        document.addEventListener('DOMContentLoaded', initSchedulerMeasure);
    }else{
        initSchedulerMeasure();
    }
})();