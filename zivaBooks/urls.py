"""zivaBooks URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import path, include
from django.urls import re_path
from django.views.static import serve as media_serve

from .health import health_check

urlpatterns = [
    path('health/', health_check, name='health_check'),
    re_path(
        rf'^{settings.MEDIA_URL.lstrip("/")}(?P<path>.*)$',
        media_serve,
        {'document_root': settings.MEDIA_ROOT},
    ),
    path('', include(('home.urls', 'home'), namespace='homeApp')),
    path('admin/', admin.site.urls),
    path('activate/', include(('activation.urls', 'activation'), namespace='activationApp')),
    path('', include('pwa.urls')),

]

handler403 = 'home.views.custom_permission_denied'
handler404 = 'home.views.custom_page_not_found'
handler500 = 'home.views.custom_server_error'
