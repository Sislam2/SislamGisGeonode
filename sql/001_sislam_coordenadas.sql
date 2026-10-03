-- Base geo intermediária: 1 linha por Coordenada (Sislam) visível no mapa, por tenant.
-- Aplicar uma vez no banco `sislam_gis_data` (GEONODE_GEODATABASE, já provisionado pelo
-- geonode-project como datastore padrão do GeoServer). Ver IMPLEMENTACAO_SISLAM.md.
--
-- Rodar CONECTADO COMO o usuário GEONODE_GEODATABASE_USER (dono do banco), para que ele seja o
-- dono da tabela: é com esse usuário que o GeoServer lê (SQL View) e o Sislam grava. Criada como
-- `postgres`, a tabela fica inacessível para os dois ("permission denied").
--   psql -h <host> -U sislam_gis_data -d sislam_gis_data -f sql/001_sislam_coordenadas.sql
--
-- Escrito/mantido pelo Sislam (app/models/geo_coordenada.rb, SyncCoordenadaGeonodeWorker),
-- lido pelo GeoServer via SQL View por tenant (1 por tenant, WHERE tenant_id fixo — ver
-- management command sislam_provision_tenant).

CREATE TABLE IF NOT EXISTS sislam_coordenadas (
  id               bigserial PRIMARY KEY,
  tenant_id        text NOT NULL,
  coordenada_id    integer NOT NULL,
  coordenable_type varchar(255) NOT NULL,
  coordenable_id   integer NOT NULL,
  latitude         double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
  longitude        double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
  -- SRID do datum de origem (SyncCoordenadaGeonodeWorker::DATUM_TO_SRID)
  srid             integer NOT NULL DEFAULT 4674 CHECK (srid IN (4674, 4326, 4618, 4225)),
  descricao        text,
  updated_at       timestamp NOT NULL DEFAULT now(),
  -- Ponto já convertido para SIRGAS 2000, calculado pelo banco a cada gravação: o GeoServer filtra
  -- por bbox a cada tile/feature request e precisa de índice espacial sobre a geometria final.
  geom             geometry(Point, 4674) NOT NULL
                   GENERATED ALWAYS AS (ST_Transform(ST_SetSRID(ST_MakePoint(longitude, latitude), srid), 4674)) STORED
);

-- Também atende às consultas só por tenant_id (prefixo do índice).
CREATE UNIQUE INDEX IF NOT EXISTS idx_sislam_coordenadas_tenant_coordenada
  ON sislam_coordenadas (tenant_id, coordenada_id);

CREATE INDEX IF NOT EXISTS idx_sislam_coordenadas_geom
  ON sislam_coordenadas USING gist (geom);
