# -*- coding: utf-8 -*-
"""
Mapa público da Transparência de um tenant, embutido em iframe pelo Sislam.

O formulário de filtros fica no Sislam, que monta a URL desta página. Aqui só os filtros conhecidos são
aceitos e convertidos em CQL_FILTER sobre a camada sislam_<tenant>_transparencia; a SQL View dela já
fixa o tenant e as regras de visibilidade (sislam_provision_tenant --transparencia).
"""
import re
from datetime import date

from django.conf import settings
from django.db import connections
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_GET

from geonode.layers.models import Dataset

from .management.commands.sislam_provision_tenant import BRASIL_BBOX, GEOSERVER_WORKSPACE, TENANT_SLUG_RE

STATUS_VALIDOS = {"D", "C", "EM"}
# Mesmo limite do formulário do Sislam (Transparencia::MapasController#check_activitie_selection_limit)
MAX_ATIVIDADES = 10
LISTA_IDS_RE = re.compile(r"^\d+(,\d+)*$")


def _inteiro(valor):
    if not valor.isdigit():
        raise ValueError
    return int(valor)


def _ids(valor):
    if not LISTA_IDS_RE.match(valor):
        raise ValueError
    ids = sorted({int(i) for i in valor.split(",")})
    if len(ids) > MAX_ATIVIDADES:
        raise ValueError
    return ids


def _texto(valor):
    # Literal CQL: aspas dobradas. O LIKE do GeoServer não tem escape, então % e _ digitados valem como
    # curinga (no ransack do Sislam valem como texto)
    return valor.replace("'", "''")


def montar_cql(params):
    """Filtros do formulário do Sislam -> CQL_FILTER. ValueError se algum valor for inválido."""
    filtros = []

    if params.get("tp_solicitacao"):
        filtros.append(f"tp_solicitacao_id = {_inteiro(params['tp_solicitacao'])}")
    if params.get("tp_documento"):
        filtros.append(f"tp_documento_ids LIKE '%,{_inteiro(params['tp_documento'])},%'")
    # Como o ransack do Sislam: as datas viram meia-noite, inclusive a final (o Sislam manda amanhã por padrão)
    if params.get("de"):
        filtros.append(f"processo_created_at >= '{date.fromisoformat(params['de']).isoformat()}'")
    if params.get("ate"):
        filtros.append(f"processo_created_at <= '{date.fromisoformat(params['ate']).isoformat()}'")
    if params.get("bairro"):
        filtros.append(f"bairro ILIKE '%{_texto(params['bairro'])}%'")
    if params.get("endereco"):
        filtros.append(f"endereco ILIKE '%{_texto(params['endereco'])}%'")
    if params.get("status"):
        status = params["status"].split(",")
        if not set(status) <= STATUS_VALIDOS:
            raise ValueError
        filtros.append("status IN ({})".format(", ".join(f"'{s}'" for s in status)))

    # Atividades e atividades de serviço se somam (OU), como o q_atv com m: "or" do Sislam
    atividades = []
    if params.get("atividades"):
        atividades += [f"atividade_ids LIKE '%,{i},%'" for i in _ids(params["atividades"])]
    if params.get("atividades_servico"):
        atividades.append("atividade_servico_id IN ({})".format(", ".join(map(str, _ids(params["atividades_servico"])))))
    if atividades:
        filtros.append("({})".format(" OR ".join(atividades)))

    return " AND ".join(filtros) or "INCLUDE"


def _extensao(tenant):
    """
    Retângulo dos pontos do tenant, para o mapa abrir enquadrado no município. Percentis 10% e 90% em vez
    do ST_Extent: cadastros antigos têm coordenadas de outros estados que alargariam o mapa para o Brasil.
    """
    with connections["datastore"].cursor() as cursor:
        cursor.execute(
            "SELECT percentile_cont(0.1) WITHIN GROUP (ORDER BY ST_X(geom)), "
            "       percentile_cont(0.1) WITHIN GROUP (ORDER BY ST_Y(geom)), "
            "       percentile_cont(0.9) WITHIN GROUP (ORDER BY ST_X(geom)), "
            "       percentile_cont(0.9) WITHIN GROUP (ORDER BY ST_Y(geom)) "
            "FROM sislam_coordenadas WHERE tenant_id = %s AND geom IS NOT NULL",
            [tenant],
        )
        extensao = cursor.fetchone()
    if extensao is None or extensao[0] is None:
        return [BRASIL_BBOX["minx"], BRASIL_BBOX["miny"], BRASIL_BBOX["maxx"], BRASIL_BBOX["maxy"]]
    return list(extensao)


@require_GET
@xframe_options_exempt
def transparencia_mapa(request, tenant):
    camada = f"{GEOSERVER_WORKSPACE}:sislam_{tenant}_transparencia"
    if not TENANT_SLUG_RE.match(tenant) or not Dataset.objects.filter(alternate=camada).exists():
        raise Http404("Mapa da Transparência não habilitado para este cliente.")

    try:
        cql = montar_cql(request.GET)
    except ValueError:
        return HttpResponseBadRequest("Filtro inválido.")

    return render(request, "sislam_tenant/transparencia_mapa.html", {
        "camada": camada,
        "cql": cql,
        "extensao": _extensao(tenant),
        "wms_url": f"{settings.GEOSERVER_PUBLIC_LOCATION.rstrip('/')}/ows",
    })
