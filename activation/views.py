from functools import wraps

from django.shortcuts import render, redirect

# Create your views here.
from .models import *
from datetime import datetime, date


def is_activated():
    def _is_activated(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            try:
                val = Validity.objects.last()

                if val.expiryDate < datetime.today().date():
                    return redirect('/activate/')
                return view_func(request, *args, **kwargs)
            except:
                return view_func(request, *args, **kwargs)

        return wrapper

    return _is_activated



def is_ecom_activated():
    def _is_ecom_activated(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            try:
                val = EcomValidity.objects.last()

                if val.expiryDate < datetime.today().date():
                    return redirect('/activate/')
                return view_func(request, *args, **kwargs)
            except:
                return view_func(request, *args, **kwargs)

        return wrapper

    return _is_ecom_activated


def activate(request):
    return render(request, 'activation/activate.html')
