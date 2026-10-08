# -*- coding: utf-8 -*-
"""
Cookies de sessão e CSRF particionados (CHIPS) para o mapa embutido em iframe pelo Sislam.

Os tenants ficam em mais de um domínio-base (*.sislam.com.br, *.msmeioambiente.com.br). Num iframe
vindo de outro site o navegador não envia cookie SameSite=Lax e, com cookies de terceiros bloqueados,
também não envia SameSite=None. Com "Partitioned" o navegador guarda um cookie separado para cada
site que embute o GeoNode, e o login do SSO feito dentro do iframe passa a valer nele.
"""
from http.cookies import Morsel

from django.conf import settings

_PARTITIONED_COOKIES = (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME)


class _PartitionedMorsel(Morsel):
    # O Morsel do Python 3.12 não conhece o atributo Partitioned (só a partir do 3.14)
    def OutputString(self, attrs=None):
        return super().OutputString(attrs) + "; Partitioned"


class PartitionedCookiesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        for name in _PARTITIONED_COOKIES:
            morsel = response.cookies.get(name)
            if morsel is None:
                continue
            partitioned = _PartitionedMorsel()
            partitioned.update(morsel)
            partitioned.set(morsel.key, morsel.value, morsel.coded_value)
            partitioned["samesite"] = "None"
            partitioned["secure"] = True
            dict.__setitem__(response.cookies, name, partitioned)
        return response
