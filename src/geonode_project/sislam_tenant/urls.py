from django.urls import path

from . import views

urlpatterns = [
    path("sislam/transparencia/<str:tenant>/", views.transparencia_mapa, name="sislam_transparencia_mapa"),
]
