"""
URL configuration for lookout_backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
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
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('core.urls')),
]

# The detection lab (core/lab.py): drop in an image, see what the detector
# found, what it would score and what the checker makes of it.
#
# DEBUG only, and deliberately so. It runs models synchronously and has no
# authentication -- mounting it beside the operational dashboard in production
# would be an unauthenticated way to spend every CPU the server has.
if settings.DEBUG:
    from core import lab
    urlpatterns += [
        path('lab/', lab.page, name='lab'),
        path('lab/analyse/', lab.analyse, name='lab_analyse'),
        path('lab/config/', lab.config, name='lab_config'),
    ]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
