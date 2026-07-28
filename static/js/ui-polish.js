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

    function initMobileGestures() {
        var isTouchDevice = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0);
        if (!isTouchDevice) return;

        var sidebar = document.getElementById('sidebar');
        var backdrop = document.querySelector('[data-sidebar-backdrop]');
        if (!sidebar || !backdrop) return;

        var touchStartX = 0;
        var touchStartY = 0;
        var currentDeltaX = 0;
        var currentDeltaY = 0;
        var isEdgeSwipe = false;
        var isSidebarSwipeClose = false;
        var isGoBackSwipe = false;
        var isModalSwipe = false;
        var activeModal = null;
        var sidebarWidth = 288;
        var isTracking = false;
        var hasGestureDetermined = false;
        var lastTouchTime = 0;
        var rafId = null;

        function clearSidebarStyles() {
            sidebar.style.removeProperty('transition');
            sidebar.style.removeProperty('transform');
            sidebar.style.removeProperty('visibility');
            backdrop.style.removeProperty('transition');
            backdrop.style.removeProperty('opacity');
            backdrop.style.removeProperty('visibility');
            backdrop.style.removeProperty('pointer-events');
        }

        document.addEventListener('touchstart', function (e) {
            if (e.touches.length !== 1) return;

            var touch = e.touches[0];
            touchStartX = touch.clientX;
            touchStartY = touch.clientY;
            currentDeltaX = 0;
            currentDeltaY = 0;
            isTracking = true;
            hasGestureDetermined = false;
            
            isEdgeSwipe = false;
            isSidebarSwipeClose = false;
            isGoBackSwipe = false;
            isModalSwipe = false;
            activeModal = null;
            
            lastTouchTime = Date.now();
        }, { passive: true });

        document.addEventListener('touchmove', function (e) {
            if (!isTracking || e.touches.length !== 1) return;

            var touch = e.touches[0];
            currentDeltaX = touch.clientX - touchStartX;
            currentDeltaY = touch.clientY - touchStartY;

            var absX = Math.abs(currentDeltaX);
            var absY = Math.abs(currentDeltaY);

            // Determine gesture once threshold is crossed
            if (!hasGestureDetermined) {
                if (absX > 10 || absY > 10) {
                    hasGestureDetermined = true;
                    var sidebarOpen = window.zivaSidebar && typeof window.zivaSidebar.isOpen === 'function' && window.zivaSidebar.isOpen();

                    if (absX > absY * 1.4) {
                        // Horizontal swipe candidate
                        if (sidebarOpen) {
                            if (currentDeltaX < 0) {
                                isSidebarSwipeClose = true;
                                sidebarWidth = sidebar.offsetWidth || 288;
                                sidebar.style.setProperty('transition', 'none', 'important');
                                backdrop.style.setProperty('transition', 'none', 'important');
                            }
                        } else {
                            if (touchStartX < 40 && currentDeltaX > 0 && window.innerWidth < 768) {
                                isEdgeSwipe = true;
                                sidebarWidth = sidebar.offsetWidth || 288;
                                sidebar.style.setProperty('visibility', 'visible', 'important');
                                sidebar.style.setProperty('transition', 'none', 'important');
                                backdrop.style.setProperty('transition', 'none', 'important');
                                backdrop.style.setProperty('visibility', 'visible', 'important');
                                backdrop.style.setProperty('pointer-events', 'auto', 'important');
                            } else {
                                var backBtn = document.getElementById('titleBarBackButton');
                                if (backBtn && touchStartX >= 50 && touchStartX < 140 && currentDeltaX > 0) {
                                    isGoBackSwipe = true;
                                }
                            }
                        }
                    } else if (absY > absX * 1.4) {
                        // Vertical swipe candidate
                        if (currentDeltaY > 0) {
                            var openModal = document.querySelector('.ui.modal.active, .ui.modal.visible, .ui.active.modal');
                            if (openModal && e.target.closest('.ui.modal')) {
                                activeModal = openModal;
                                isModalSwipe = true;
                                activeModal.style.setProperty('transition', 'none', 'important');
                            }
                        }
                    }
                }
            }

            // Perform active tracking transformations
            if (isSidebarSwipeClose) {
                var translation = currentDeltaX;
                if (translation < -sidebarWidth) {
                    translation = -sidebarWidth + (translation + sidebarWidth) * 0.2;
                }
                if (e.cancelable) e.preventDefault();
                if (rafId) cancelAnimationFrame(rafId);
                rafId = requestAnimationFrame(function () {
                    sidebar.style.setProperty('transform', 'translate3d(' + translation + 'px, 0, 0)', 'important');
                    var opacity = Math.max(0, Math.min(1, 1 - Math.abs(translation / sidebarWidth)));
                    backdrop.style.setProperty('opacity', opacity, 'important');
                });
                return;
            }

            if (isEdgeSwipe) {
                var translation = -sidebarWidth + currentDeltaX;
                if (translation > 0) {
                    translation = translation * 0.2;
                }
                if (e.cancelable) e.preventDefault();
                if (rafId) cancelAnimationFrame(rafId);
                rafId = requestAnimationFrame(function () {
                    sidebar.style.setProperty('transform', 'translate3d(' + translation + 'px, 0, 0)', 'important');
                    var opacity = Math.max(0, Math.min(1, currentDeltaX / sidebarWidth));
                    backdrop.style.setProperty('opacity', opacity, 'important');
                });
                return;
            }

            if (isGoBackSwipe) {
                if (e.cancelable) e.preventDefault();
                return;
            }

            if (isModalSwipe && activeModal) {
                if (e.cancelable) e.preventDefault();
                if (rafId) cancelAnimationFrame(rafId);
                rafId = requestAnimationFrame(function () {
                    activeModal.style.setProperty('transform', 'translate3d(0, ' + currentDeltaY + 'px, 0)', 'important');
                    activeModal.style.setProperty('opacity', Math.max(0.4, 1 - (currentDeltaY / 400)), 'important');
                });
                return;
            }
        }, { passive: false });

        document.addEventListener('touchend', function (e) {
            if (!isTracking) return;
            isTracking = false;
            if (rafId) cancelAnimationFrame(rafId);

            var duration = Date.now() - lastTouchTime;
            var velocityX = currentDeltaX / (duration || 1);
            var velocityY = currentDeltaY / (duration || 1);

            if (isSidebarSwipeClose) {
                isSidebarSwipeClose = false;
                var shouldClose = currentDeltaX < -sidebarWidth * 0.35 || velocityX < -0.25;
                
                var currentX = Math.max(-sidebarWidth, Math.min(0, currentDeltaX));
                var targetX = shouldClose ? -sidebarWidth : 0;
                var distance = Math.abs(currentX - targetX);
                var animDuration = Math.min(0.28, Math.max(0.12, distance / (Math.abs(velocityX) || 1) / 1000));

                sidebar.style.setProperty('transition', 'transform ' + animDuration + 's cubic-bezier(0.16, 1, 0.3, 1)', 'important');
                backdrop.style.setProperty('transition', 'opacity ' + animDuration + 's ease', 'important');
                
                sidebar.style.setProperty('transform', 'translate3d(' + targetX + 'px, 0, 0)', 'important');
                backdrop.style.setProperty('opacity', shouldClose ? '0' : '1', 'important');

                setTimeout(function () {
                    clearSidebarStyles();
                    if (window.zivaSidebar) {
                        if (shouldClose) window.zivaSidebar.close();
                        else window.zivaSidebar.open();
                    }
                }, animDuration * 1000 + 30);
                return;
            }

            if (isEdgeSwipe) {
                isEdgeSwipe = false;
                var shouldOpen = currentDeltaX > sidebarWidth * 0.35 || velocityX > 0.25;
                
                var currentX = Math.max(-sidebarWidth, Math.min(0, -sidebarWidth + currentDeltaX));
                var targetX = shouldOpen ? 0 : -sidebarWidth;
                var distance = Math.abs(currentX - targetX);
                var animDuration = Math.min(0.28, Math.max(0.12, distance / (Math.abs(velocityX) || 1) / 1000));

                sidebar.style.setProperty('transition', 'transform ' + animDuration + 's cubic-bezier(0.16, 1, 0.3, 1)', 'important');
                backdrop.style.setProperty('transition', 'opacity ' + animDuration + 's ease', 'important');
                
                sidebar.style.setProperty('transform', 'translate3d(' + targetX + 'px, 0, 0)', 'important');
                backdrop.style.setProperty('opacity', shouldOpen ? '1' : '0', 'important');

                setTimeout(function () {
                    clearSidebarStyles();
                    if (window.zivaSidebar) {
                        if (shouldOpen) window.zivaSidebar.open();
                        else window.zivaSidebar.close();
                    }
                }, animDuration * 1000 + 30);
                return;
            }

            if (isGoBackSwipe) {
                isGoBackSwipe = false;
                var absY = Math.abs(currentDeltaY);
                if (currentDeltaX > 100 && currentDeltaX > absY * 1.5) {
                    var backBtn = document.getElementById('titleBarBackButton');
                    if (backBtn) {
                        backBtn.click();
                    }
                }
                return;
            }

            if (isModalSwipe && activeModal) {
                isModalSwipe = false;
                var modal = activeModal;
                activeModal = null;

                var shouldDismiss = currentDeltaY > 120 || velocityY > 0.25;
                var animDuration = 0.24;

                var modalRect = modal.getBoundingClientRect();
                var targetY = shouldDismiss ? (window.innerHeight - modalRect.top + 20) : 0;
                var targetOpacity = shouldDismiss ? 0 : 1;

                modal.style.setProperty('transition', 'transform ' + animDuration + 's cubic-bezier(0.16, 1, 0.3, 1), opacity ' + animDuration + 's ease', 'important');
                
                var dimmer = modal.closest('.ui.dimmer.modals');

                if (shouldDismiss) {
                    if (dimmer) {
                        dimmer.style.setProperty('transition', 'opacity ' + animDuration + 's ease', 'important');
                        dimmer.style.setProperty('opacity', '0', 'important');
                    }
                    modal.style.setProperty('transform', 'translate3d(0, ' + targetY + 'px, 0)', 'important');
                    modal.style.setProperty('opacity', '0', 'important');

                    // Call hide immediately to let Fomantic UI start fading out the dimmer/backdrop
                    window.jQuery(modal).modal('hide');

                    // Defer clearing inline styles until Fomantic UI has finished transition (typically 500ms)
                    setTimeout(function () {
                        modal.style.removeProperty('transition');
                        modal.style.removeProperty('transform');
                        modal.style.removeProperty('opacity');
                        if (dimmer) {
                            dimmer.style.removeProperty('transition');
                            dimmer.style.removeProperty('opacity');
                        }
                    }, 650);
                } else {
                    modal.style.setProperty('transform', 'translate3d(0, 0, 0)', 'important');
                    modal.style.setProperty('opacity', '1', 'important');
                    
                    setTimeout(function () {
                        modal.style.removeProperty('transition');
                        modal.style.removeProperty('transform');
                        modal.style.removeProperty('opacity');
                    }, animDuration * 1000 + 40);
                }
            }
        }, { passive: true });

        document.addEventListener('touchcancel', function () {
            if (!isTracking) return;
            isTracking = false;
            if (rafId) cancelAnimationFrame(rafId);

            if (isSidebarSwipeClose || isEdgeSwipe) {
                clearSidebarStyles();
            }

            if (isModalSwipe && activeModal) {
                activeModal.style.removeProperty('transition');
                activeModal.style.removeProperty('transform');
                activeModal.style.removeProperty('opacity');
                activeModal = null;
            }

            isSidebarSwipeClose = false;
            isEdgeSwipe = false;
            isGoBackSwipe = false;
            isModalSwipe = false;
        }, { passive: true });
    }

    initPageRevealTransition();
    onReady(function () {
        initThemeFlash();
        initTitleBarBackButton();
        initMobileGestures();
    });
})();


