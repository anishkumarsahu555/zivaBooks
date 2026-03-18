/* Pull-to-refresh (mobile) for zivaBooks.
 * - Triggers `window.onPullToRefresh()` if provided, else reloads the page.
 * - Opt out per page: add `data-ptr="off"` on <body>.
 */
(function () {
    var hasTouch = ('ontouchstart' in window) || (navigator.maxTouchPoints && navigator.maxTouchPoints > 0);
    if (!hasTouch) return;
    if (!document.body || document.body.getAttribute('data-ptr') === 'off') return;

    var scrollEl = document.scrollingElement || document.documentElement;
    var activeScroller = scrollEl;
    var threshold = 70;
    var maxPull = 120;
    var startY = null;
    var pulling = false;
    var dist = 0;
    var refreshing = false;

    function $(sel) { return document.querySelector(sel); }
    function createBar() {
        var bar = document.createElement('div');
        bar.className = 'ptr-bar';
        bar.innerHTML =
            '<div class="ptr-pill">' +
            '  <i class="ptr-icon sync icon"></i>' +
            '  <span class="ptr-text">Pull to refresh</span>' +
            '</div>';
        document.body.appendChild(bar);

        // Place below fixed top nav if present.
        var nav = $('nav.ui.top.fixed.menu');
        var top = nav ? (nav.offsetHeight || 0) : 0;
        bar.style.top = top + 'px';
        return bar;
    }

    var bar = createBar();
    var textEl = bar.querySelector('.ptr-text');

    function setText(t) {
        if (textEl) textEl.textContent = t;
    }

    function setPull(y) {
        var eased = Math.min(maxPull, y);
        bar.style.transform = 'translateY(' + (eased - maxPull) + 'px)';
        bar.classList.toggle('is-ready', eased >= threshold);
        // Rotate icon a bit as you pull (nice feedback).
        var rotate = Math.min(180, Math.round((eased / threshold) * 180));
        bar.style.setProperty('--ptr-rotate', rotate + 'deg');
        if (!refreshing) {
            setText(eased >= threshold ? 'Release to refresh' : 'Pull to refresh');
        }
    }

    function reset() {
        bar.classList.remove('is-active', 'is-ready', 'is-refreshing');
        bar.style.transform = 'translateY(' + (-maxPull) + 'px)';
        setText('Pull to refresh');
        bar.style.setProperty('--ptr-rotate', '0deg');
        dist = 0;
        startY = null;
        pulling = false;
        refreshing = false;
        activeScroller = scrollEl;
    }

    function isModalOpen() {
        return !!document.querySelector('.ui.active.modal');
    }

    function findScrollParent(target) {
        var el = target;
        while (el && el !== document.body) {
            try {
                var style = window.getComputedStyle(el);
                var overflowY = style ? style.overflowY : '';
                var isScrollable = (overflowY === 'auto' || overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 2;
                if (isScrollable) return el;
            } catch (e) { /* ignore */ }
            el = el.parentElement;
        }
        return scrollEl;
    }

    function canStart(e) {
        if (refreshing) return false;
        if (isModalOpen()) return false;
        activeScroller = findScrollParent(e.target);
        if ((activeScroller || scrollEl).scrollTop > 0) return false;
        return true;
    }

    function triggerRefresh() {
        refreshing = true;
        bar.classList.add('is-refreshing');
        setText('Refreshing…');
        bar.style.setProperty('--ptr-rotate', '0deg');
        bar.style.transform = 'translateY(0px)';

        try {
            if (typeof window.onPullToRefresh === 'function') {
                var result = window.onPullToRefresh();
                if (result && typeof result.then === 'function') {
                    result.then(function () { setTimeout(reset, 250); })
                        .catch(function () { setTimeout(reset, 250); });
                } else {
                    setTimeout(reset, 450);
                }
            } else {
                window.location.reload();
            }
        } catch (err) {
            window.location.reload();
        }
    }

    // Start hidden.
    bar.style.transform = 'translateY(' + (-maxPull) + 'px)';

    document.addEventListener('touchstart', function (e) {
        if (e.touches.length !== 1) return;
        if (!canStart(e)) return;
        startY = e.touches[0].clientY;
        pulling = true;
        dist = 0;
        bar.classList.add('is-active');
    }, { passive: true });

    document.addEventListener('touchmove', function (e) {
        if (!pulling || startY === null) return;
        var y = e.touches[0].clientY;
        dist = Math.max(0, y - startY);
        if (dist <= 0) return;

        // Stop native pull-to-refresh / overscroll.
        if (e.cancelable) e.preventDefault();
        setPull(dist);
    }, { passive: false });

    document.addEventListener('touchend', function () {
        if (!pulling) return;
        if (dist >= threshold) {
            triggerRefresh();
        } else {
            reset();
        }
    }, { passive: true });
})();
