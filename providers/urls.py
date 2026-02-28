from django.urls import path

from providers.api import marketplace_search, zone_list

urlpatterns = [
    path("zones/", zone_list),
    path("marketplace/search/", marketplace_search),
]
