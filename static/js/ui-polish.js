(function () {
    function prefersReducedMotion() {
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    function onReady(callback) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', callback, {once: true});
        } else {
            callback();
        }
    }

    function initThemeFlash() {
        var toggle = document.getElementById('themeToggleBtn') || document.getElementById('loginThemeToggle');
        if (!toggle || prefersReducedMotion()) return;
        toggle.addEventListener('click', function () {
            document.documentElement.classList.remove('app-theme-flash');
            window.requestAnimationFrame(function () {
                document.documentElement.classList.add('app-theme-flash');
                window.setTimeout(function () {
                    document.documentElement.classList.remove('app-theme-flash');
                }, 380);
            });
        });
    }

    onReady(initThemeFlash);
})();
