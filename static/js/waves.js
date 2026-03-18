/* Lightweight "waves" ripple for buttons + sidebar items.
 * Applies to:
 * - `.ui.button` (except disabled/loading)
 * - `#sidebar .item` (sidebar navigation)
 *
 * Opt-out: add `data-waves="off"` on the clicked element.
 */
(function () {
    function closest(el, selector) {
        while (el && el !== document.documentElement) {
            if (el.matches && el.matches(selector)) return el;
            el = el.parentElement;
        }
        return null;
    }

    function shouldIgnore(target) {
        if (!target) return true;
        if (target.getAttribute && target.getAttribute('data-waves') === 'off') return true;
        if (target.classList && (target.classList.contains('disabled') || target.classList.contains('loading'))) return true;
        if (target.closest && target.closest('.disabled')) return true;
        return false;
    }

    function addRipple(e, el) {
        if (!el || shouldIgnore(el)) return;

        // Ensure positioning context.
        var style = window.getComputedStyle(el);
        if (style.position === 'static') {
            el.style.position = 'relative';
        }
        el.style.overflow = 'hidden';

        var rect = el.getBoundingClientRect();
        var x = (e.touches && e.touches.length ? e.touches[0].clientX : e.clientX) - rect.left;
        var y = (e.touches && e.touches.length ? e.touches[0].clientY : e.clientY) - rect.top;

        var size = Math.max(rect.width, rect.height) * 1.15;
        var ripple = document.createElement('span');
        ripple.className = 'waves-ripple';
        ripple.style.width = ripple.style.height = size + 'px';
        ripple.style.left = (x - size / 2) + 'px';
        ripple.style.top = (y - size / 2) + 'px';

        el.appendChild(ripple);

        // Cleanup.
        window.setTimeout(function () {
            if (ripple && ripple.parentNode) ripple.parentNode.removeChild(ripple);
        }, 650);
    }

    document.addEventListener('pointerdown', function (e) {
        var btn = closest(e.target, '.ui.button');
        var sideItem = closest(e.target, '#sidebar .item');
        var el = btn || sideItem;
        if (!el) return;
        addRipple(e, el);
    }, { passive: true });

    // Fallback for older iOS Safari.
    document.addEventListener('touchstart', function (e) {
        var btn = closest(e.target, '.ui.button');
        var sideItem = closest(e.target, '#sidebar .item');
        var el = btn || sideItem;
        if (!el) return;
        addRipple(e, el);
    }, { passive: true });
})();

