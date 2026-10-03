from django.urls import path

from . import views

urlpatterns = [
    path("sso/sislam-login/", views.sislam_sso_login, name="sislam_sso_login"),
]
