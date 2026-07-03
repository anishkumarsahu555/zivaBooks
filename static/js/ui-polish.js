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


    function initPageRevealTransition() {
        window.addEventListener("pagereveal", function (e) {
            if (!e.viewTransition) return;

            var direction = sessionStorage.getItem('nav-direction') || 'forward';

            if (window.navigation && window.navigation.activation) {
                var activation = window.navigation.activation;
                if (activation.navigationType === 'traverse') {
                    var from = activation.from;
                    var entry = activation.entry;
                    if (from && entry && typeof entry.index === 'number' && typeof from.index === 'number') {
                        direction = entry.index < from.index ? 'back' : 'forward';
                    }
                }
            }

            e.viewTransition.types.add(direction);
            sessionStorage.setItem('nav-direction', 'forward');
        });
    }

    function initTitleBarBackButton() {
        var menu = document.querySelector('.main-content .ui.pointing.secondary.menu');
        if (!menu) return;

        var currentPath = window.location.pathname;
        if (currentPath === '/home/' || currentPath === '/' || currentPath === '/home') {
            return;
        }

        if (document.getElementById('titleBarBackButton')) return;

        var backBtn = document.createElement('a');
        backBtn.href = '#';
        backBtn.className = 'item title-back-button';
        backBtn.id = 'titleBarBackButton';
        backBtn.innerHTML = '<i class="chevron left icon" style="margin: 0 !important;"></i>';
        
        backBtn.style.paddingLeft = '8px';
        backBtn.style.paddingRight = '12px';
        backBtn.style.display = 'inline-flex';
        backBtn.style.alignItems = 'center';

        menu.insertBefore(backBtn, menu.firstChild);

        // Custom tooltip matching side nav style
        var tooltipTimeout = null;
        backBtn.addEventListener('mouseenter', function () {
            if (tooltipTimeout) {
                clearTimeout(tooltipTimeout);
                tooltipTimeout = null;
            }
            var $tip = window.jQuery('#titleBackButtonTooltip');
            if (!$tip.length) {
                $tip = window.jQuery('<div id="titleBackButtonTooltip" class="title-tooltip-floating"></div>').appendTo('body');
            }
            $tip.text('Go Back');
            var rect = backBtn.getBoundingClientRect();
            $tip.css({
                top: rect.bottom + 8,
                left: rect.left + (rect.width / 2)
            });
            $tip.addClass('is-active');
        });

        backBtn.addEventListener('mouseleave', function () {
            tooltipTimeout = setTimeout(function () {
                var $tip = window.jQuery('#titleBackButtonTooltip');
                if ($tip.length) {
                    $tip.removeClass('is-active');
                }
            }, 80);
        });

        backBtn.addEventListener('click', function () {
            var $tip = window.jQuery('#titleBackButtonTooltip');
            if ($tip.length) {
                $tip.removeClass('is-active');
            }
        });

        backBtn.addEventListener('click', function (e) {
            e.preventDefault();
            sessionStorage.setItem('nav-direction', 'back');

            if (document.referrer && document.referrer.indexOf(window.location.host) !== -1) {
                window.history.back();
            } else {
                window.location.href = '/home/';
            }
        });
    }

    initPageRevealTransition();
    onReady(function () {
        initThemeFlash();
        initTitleBarBackButton();
    });
})();


