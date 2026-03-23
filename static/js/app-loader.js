(function () {
    function getLoader() {
        return document.querySelector('[data-app-loader]');
    }

    function hideLoader() {
        var loader = getLoader();
        if (!loader) return;
        loader.classList.add('is-hidden');
        window.setTimeout(function () {
            if (loader && loader.parentNode) {
                loader.parentNode.removeChild(loader);
            }
        }, 260);
    }

    function showLoader(label) {
        var loader = getLoader();
        if (!loader) return;
        if (label) {
            var subtitle = loader.querySelector('[data-app-loader-subtitle]');
            if (subtitle) subtitle.textContent = label;
        }
        loader.classList.remove('is-hidden');
    }

    window.AppLoader = {
        show: showLoader,
        hide: hideLoader
    };

    document.addEventListener('DOMContentLoaded', function () {
        document.addEventListener('click', function (event) {
            var anchor = event.target.closest('a[href]');
            if (!anchor) return;
            if (anchor.target === '_blank' || anchor.hasAttribute('download')) return;
            if (anchor.getAttribute('href').charAt(0) === '#') return;
            if (anchor.getAttribute('href').indexOf('javascript:') === 0) return;
            showLoader('Opening...');
        });

        document.addEventListener('submit', function () {
            showLoader('Please wait...');
        }, true);
    });

    window.addEventListener('load', hideLoader);
})();
