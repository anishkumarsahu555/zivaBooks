from __future__ import annotations

from threading import local


_state = local()


def set_current_user(user):
    _state.user = user


def get_current_user():
    return getattr(_state, 'user', None)


def clear_current_user():
    if hasattr(_state, 'user'):
        delattr(_state, 'user')
