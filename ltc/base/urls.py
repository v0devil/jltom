from django.conf.urls import include
from django.urls import path
from django.contrib import admin
# from django.conf.urls.static import static
from . import views

urlpatterns = [
    path('', views.index, name='ltc.index'),
    path('analyzer', include('ltc.analyzer.urls'), name='ltc.analyzer'),
    path('admin', admin.site.urls),
    path('controller', include('ltc.controller.urls'), name='ltc.controller'),
]
