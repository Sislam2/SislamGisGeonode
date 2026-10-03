# -*- coding: utf-8 -*-
"""
Prototipo de SSO entre o Sislam e o GeoNode.

Troca um JWT assinado pelo Sislam (RS256, mesmo padrao usado em app/controllers/api/integrador
no monolito) por uma sessao Django autenticada no GeoNode, garantindo que o usuario esteja no
Group correspondente ao tenant (prefeitura) informado no token.

Em producao, a chave publica em keys/sislam_pub.pem deveria ser a mesma (ou equivalente) usada
pelo Sislam para assinar esses tokens -- a privada nunca sai do Sislam.
"""
import logging
import os
from pathlib import Path

import jwt
from django.contrib.auth import get_user_model, login
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET

from geonode.groups.models import GroupProfile

logger = logging.getLogger(__name__)

User = get_user_model()

_PUBLIC_KEY = (Path(__file__).resolve().parent / "keys" / "sislam_pub.pem").read_text()

_ISSUER = os.getenv("SISLAM_SSO_ISSUER", "sislam")
_AUDIENCE = os.getenv("SISLAM_SSO_AUDIENCE", "sislam-gis")

_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "tenant", "username"]


@require_GET
def sislam_sso_login(request):
    """
    GET /sso/sislam-login/?token=<jwt>&next=/maps/embed/...
    """
    token = request.GET.get("token")
    if not token:
        return HttpResponseBadRequest("token ausente")

    try:
        claims = jwt.decode(
            token,
            _PUBLIC_KEY,
            algorithms=["RS256"],
            issuer=_ISSUER,
            audience=_AUDIENCE,
            leeway=10,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError:
        return HttpResponseForbidden("token expirado")
    except jwt.InvalidTokenError as exc:
        logger.warning("Token JWT invalido recebido no SSO Sislam: %s", exc)
        return HttpResponseForbidden("token invalido")

    tenant_slug = claims["tenant"]
    sislam_user_id = claims["sub"]
    local_username = f"{tenant_slug}__{sislam_user_id}"

    user, created = User.objects.get_or_create(
        username=local_username,
        defaults={
            "email": claims.get("email", ""),
            "first_name": (claims.get("name") or claims["username"])[:30],
        },
    )
    if created:
        user.set_unusable_password()
        user.save()

    group_profile, _ = GroupProfile.objects.get_or_create(
        slug=tenant_slug,
        defaults={
            "title": claims.get("tenant_name", tenant_slug),
            "description": f"Tenant Sislam: {tenant_slug}",
            "access": "private",
        },
    )
    group_profile.join(user)

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    logger.info("SSO Sislam: usuario %s (tenant=%s) autenticado", local_username, tenant_slug)

    next_url = request.GET.get("next", "/")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "/"
    return redirect(next_url)
