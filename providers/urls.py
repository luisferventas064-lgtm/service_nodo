from django.urls import path

from providers.api import marketplace_search

urlpatterns = [
    path("marketplace/search/", marketplace_search),
]
