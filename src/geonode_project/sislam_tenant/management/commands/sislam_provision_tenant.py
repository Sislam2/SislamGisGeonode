# -*- coding: utf-8 -*-
import re
from uuid import uuid4

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

GEOSERVER_WORKSPACE = "geonode"
GEOSERVER_STORE = settings.OGC_SERVER["default"]["DATASTORE"]

# Nome do banco do tenant no Sislam. Vai para o SQL da view e para o nome do layer, por isso só
# aceita caracteres seguros nos dois contextos.
TENANT_SLUG_RE = re.compile(r"^[a-z0-9_]+$")

# Área de uso do SIRGAS 2000 (Brasil). Fixa porque a SQL View pode estar vazia na criação
# (GeoServer não consegue calcular a extensão) e cresce depois com o sync do Sislam.
BRASIL_BBOX = {"minx": -74.0, "maxx": -34.0, "miny": -34.0, "maxy": 6.0}

# geom já vem convertido para 4674 e indexado, e é nula fora do Brasil (sql/001 e sql/002).
# Interna: todas as coordenadas do tenant, com os atributos do processo quando houver.
SQL_VIEW_TEMPLATE = """
SELECT
  c.id,
  c.coordenada_id,
  c.coordenable_type,
  c.coordenable_id,
  c.processo_id,
  c.tipo,
  c.datum,
  c.nao_aplica,
  c.grupo,
  c.ponto,
  c.descricao,
  p.tp_solicitacao_id,
  p.status,
  p.processo_created_at,
  p.bairro,
  p.endereco,
  p.atividade_ids,
  p.tp_documento_ids,
  p.atividade_servico_id,
  p.visivel_transparencia,
  c.geom
FROM sislam_coordenadas c
LEFT JOIN sislam_processos p ON p.tenant_id = c.tenant_id AND p.processo_id = c.processo_id
WHERE c.tenant_id = '{tenant_id}'
""".strip()

# Pública: só os pontos que o mapa da Transparência mostra (CoordenadaVisibilidadeService no Sislam)
# e os campos dos filtros da tela. O CQL_FILTER da requisição soma-se a este WHERE, nunca o substitui.
SQL_VIEW_TRANSPARENCIA_TEMPLATE = """
SELECT
  c.id,
  c.descricao,
  p.tp_solicitacao_id,
  p.status,
  p.processo_created_at,
  p.bairro,
  p.endereco,
  p.atividade_ids,
  p.tp_documento_ids,
  p.atividade_servico_id,
  c.geom
FROM sislam_coordenadas c
JOIN sislam_processos p ON p.tenant_id = c.tenant_id AND p.processo_id = c.processo_id
WHERE c.tenant_id = '{tenant_id}'
  AND c.geom IS NOT NULL
  AND (c.coordenable_type <> 'Processo' OR p.visivel_transparencia)
""".strip()


class Command(BaseCommand):
    help = "Provisiona um tenant Sislam no GeoNode (Group, SQL View, Dataset, Map canônico)."

    def add_arguments(self, parser):
        parser.add_argument("tenant_slug", help="Nome do banco do tenant no Sislam (ex: pinhais)")
        parser.add_argument("--title", default=None, help="Nome de exibição (ex: 'Pinhais'). Default: tenant_slug.")
        parser.add_argument("--zoom", type=int, default=4, help="Zoom inicial do Map canônico. Default: 4.")
        parser.add_argument(
            "--transparencia",
            action="store_true",
            help="Cria também a camada pública do mapa da Transparência (visualização anônima, sem download). "
                 "Só para tenants com o mapa da Transparência habilitado no Sislam.",
        )

    def handle(self, *args, **options):
        tenant_slug = options["tenant_slug"]
        if not TENANT_SLUG_RE.match(tenant_slug):
            raise CommandError("tenant_slug deve conter apenas letras minúsculas, números e '_'.")
        title = options["title"] or tenant_slug
        layer_name = f"sislam_{tenant_slug}"
        sql_views = {layer_name: SQL_VIEW_TEMPLATE}
        if options["transparencia"]:
            sql_views[f"{layer_name}_transparencia"] = SQL_VIEW_TRANSPARENCIA_TEMPLATE

        datasets = {}
        for name, template in sql_views.items():
            self.stdout.write(f"1/5 Criando/atualizando SQL View '{name}' no GeoServer...")
            self.create_sql_view(name, template.format(tenant_id=tenant_slug))

            self.stdout.write(f"2/5 Importando '{name}' como Dataset no GeoNode (updatelayers)...")
            datasets[name] = self.import_dataset(name)

        dataset = datasets[layer_name]

        self.stdout.write(f"3/5 Criando/obtendo Group do tenant '{tenant_slug}'...")
        group_profile = self.get_or_create_group(tenant_slug, title)

        self.stdout.write("4/5 Restringindo os Datasets ao Group do tenant...")
        self.restrict_to_group(dataset, group_profile)
        if options["transparencia"]:
            # WMS (mapa e popup) liberado ao anônimo; o WFS (download dos dados) fica só com o grupo
            self.restrict_to_group(
                datasets[f"{layer_name}_transparencia"], group_profile, anonymous_perms=["view_resourcebase"]
            )

        self.stdout.write("5/5 Criando/obtendo o Map canônico...")
        map_obj = self.get_or_create_canonical_map(dataset, tenant_slug, title, options["zoom"])
        self.restrict_to_group(map_obj, group_profile)

        self.stdout.write(self.style.SUCCESS(
            f"Tenant '{tenant_slug}' provisionado. Map canônico id={map_obj.id} "
            f"(embed: /maps/{map_obj.id}/embed)."
        ))

    def import_dataset(self, layer_name):
        from geonode.layers.models import Dataset

        call_command("updatelayers", filter=layer_name, store=GEOSERVER_STORE, workspace=GEOSERVER_WORKSPACE)
        dataset = Dataset.objects.filter(alternate=f"{GEOSERVER_WORKSPACE}:{layer_name}").first()
        if not dataset:
            raise CommandError(
                f"Dataset '{layer_name}' não apareceu no catálogo do GeoNode após updatelayers. "
                "Confira manualmente em /catalogue/#/search se o layer foi importado."
            )
        self.stdout.write(self.style.SUCCESS(f"    Dataset encontrado: id={dataset.id}"))
        return dataset

    def create_sql_view(self, layer_name, sql):
        from geonode.geoserver.helpers import create_geoserver_db_featurestore

        # O GeoNode só cria o store do geodatabase no primeiro upload; garante que ele exista.
        create_geoserver_db_featurestore(store_name=GEOSERVER_STORE, workspace=GEOSERVER_WORKSPACE)

        geoserver_url = settings.OGC_SERVER["default"]["LOCATION"].rstrip("/")
        auth = (settings.OGC_SERVER["default"]["USER"], settings.OGC_SERVER["default"]["PASSWORD"])
        featuretypes_url = (
            f"{geoserver_url}/rest/workspaces/{GEOSERVER_WORKSPACE}/datastores/{GEOSERVER_STORE}/featuretypes"
        )

        bbox = dict(BRASIL_BBOX, crs="EPSG:4674")
        payload = {
            "featureType": {
                "name": layer_name,
                "nativeName": layer_name,
                "srs": "EPSG:4674",
                "nativeBoundingBox": bbox,
                "latLonBoundingBox": dict(BRASIL_BBOX, crs="EPSG:4326"),
                "metadata": {
                    "entry": [
                        {"@key": "JDBC_VIRTUAL_TABLE", "virtualTable": {
                            "name": layer_name,
                            "sql": sql,
                            "escapeSql": False,
                            "keyColumn": "id",
                            "geometry": {"name": "geom", "type": "Point", "srid": 4674},
                        }}
                    ]
                },
            }
        }
        # Já existente: regrava o SQL, para um tenant provisionado antes receber as colunas novas
        if requests.get(f"{featuretypes_url}/{layer_name}.json", auth=auth).status_code == 200:
            response = requests.put(f"{featuretypes_url}/{layer_name}", json=payload, auth=auth)
        else:
            response = requests.post(featuretypes_url, json=payload, auth=auth)
        if response.status_code not in (200, 201):
            raise CommandError(f"Falha ao gravar SQL View no GeoServer ({response.status_code}): {response.text}")

    def get_or_create_group(self, tenant_slug, title):
        from geonode.groups.models import GroupProfile

        group_profile, _created = GroupProfile.objects.get_or_create(
            slug=tenant_slug,
            defaults={
                "title": title,
                "description": f"Tenant Sislam: {tenant_slug}",
                "access": "private",
            },
        )
        return group_profile

    def restrict_to_group(self, resource, group_profile, anonymous_perms=()):
        resource.set_permissions({
            "users": {"AnonymousUser": list(anonymous_perms)},
            "groups": {group_profile.group.name: ["view_resourcebase", "download_resourcebase"]},
        })

    def get_or_create_canonical_map(self, dataset, tenant_slug, title, zoom):
        from geonode.maps.models import Map, MapLayer
        from geonode.resource.registry import map_manager

        map_obj = Map.objects.filter(maplayers__dataset=dataset).first()
        if map_obj:
            self.stdout.write(f"    Map canônico já existe (id={map_obj.id}), mantido.")
            return map_obj

        owner = get_user_model().objects.get(username=getattr(settings, "ADMIN_USERNAME", "admin"))
        center_x = (BRASIL_BBOX["minx"] + BRASIL_BBOX["maxx"]) / 2
        center_y = (BRASIL_BBOX["miny"] + BRASIL_BBOX["maxy"]) / 2
        if dataset.ll_bbox_polygon:
            center = dataset.ll_bbox_polygon.centroid
            center_x, center_y = center.x, center.y

        # O cliente MapStore acrescenta ao mapa os MapLayer com dataset que não estejam no blob,
        # então o blob só precisa do viewport e dos fundos.
        blob = {
            "version": 2,
            "map": {
                "projection": settings.DEFAULT_MAP_CRS,
                "units": "m",
                "center": {"x": center_x, "y": center_y, "crs": "EPSG:4326"},
                "zoom": zoom,
                "layers": settings.MAPSTORE_BASELAYERS,
            },
        }
        map_obj = map_manager.create(
            str(uuid4()),
            resource_type=Map,
            defaults={
                "title": f"Mapa oficial - {title}",
                "abstract": f"Mapa canônico do tenant Sislam: {tenant_slug}",
                "owner": owner,
                "blob": blob,
            },
        )
        MapLayer.objects.create(
            map=map_obj,
            dataset=dataset,
            name=dataset.alternate,
            store=dataset.store,
            ows_url=dataset.ows_url,
            local=True,
            order=0,
            visibility=True,
        )
        map_obj.compute_bbox()
        map_obj.save()
        return map_obj
