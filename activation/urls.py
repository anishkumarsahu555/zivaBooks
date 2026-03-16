from django.urls import path
from .views import *

urlpatterns = [
    # pages
    path('', activate, name='activate'),
]
