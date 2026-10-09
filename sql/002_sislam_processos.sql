-- Todas as Coordenadas do Sislam (não só as visíveis no mapa) e os atributos do Processo usados nos
-- filtros do mapa da Transparência. Aplicar depois do 001, conectado como sislam_gis_data (ver 001):
--   psql -h <host> -U sislam_gis_data -d sislam_gis_data -f sql/002_sislam_processos.sql
--
-- Separação por tenant: as duas tabelas têm tenant_id em toda linha e na chave única, o join entre
-- elas é sempre por (tenant_id, processo_id) e cada SQL View do GeoServer fixa o tenant_id
-- (management command sislam_provision_tenant).

BEGIN;

-- Coordenadas inválidas para o mapa (0,0, placeholders, UTM sem conversão) também são gravadas:
-- ficam sem geometria, mas o dado não se perde.
ALTER TABLE sislam_coordenadas
  DROP CONSTRAINT IF EXISTS sislam_coordenadas_latitude_check,
  DROP CONSTRAINT IF EXISTS sislam_coordenadas_longitude_check,
  DROP COLUMN geom,
  ADD COLUMN tipo       integer,
  ADD COLUMN datum      varchar(255),
  ADD COLUMN nao_aplica boolean NOT NULL DEFAULT false,
  ADD COLUMN grupo      integer NOT NULL DEFAULT 1,
  ADD COLUMN ponto      integer NOT NULL DEFAULT 1,
  -- Processo da coordenada (coordenable Processo, ProcessoInfoAdicional ou SolicitaFiscalizacao),
  -- nulo nos demais tipos. Liga a coordenada a sislam_processos.
  ADD COLUMN processo_id integer;

-- Só pontos dentro do Brasil (mesmo retângulo do BRASIL_BBOX do sislam_provision_tenant) viram geometria.
ALTER TABLE sislam_coordenadas
  ADD COLUMN geom geometry(Point, 4674)
    GENERATED ALWAYS AS (
      CASE WHEN latitude BETWEEN -34 AND 6 AND longitude BETWEEN -74 AND -34
        THEN ST_Transform(ST_SetSRID(ST_MakePoint(longitude, latitude), srid), 4674)
      END
    ) STORED;

-- Recriado: o índice antigo caiu junto com a coluna.
CREATE INDEX IF NOT EXISTS idx_sislam_coordenadas_geom
  ON sislam_coordenadas USING gist (geom);

CREATE INDEX IF NOT EXISTS idx_sislam_coordenadas_tenant_processo
  ON sislam_coordenadas (tenant_id, processo_id);

-- Uma linha por Processo de cada tenant, com os campos já resolvidos pelo Sislam a partir dos mesmos
-- caminhos dos ransack_alias transparencia_coordenada_processo_* (app/models/coordenada.rb).
CREATE TABLE IF NOT EXISTS sislam_processos (
  id                    bigserial PRIMARY KEY,
  tenant_id             text NOT NULL,
  processo_id           integer NOT NULL,
  tp_solicitacao_id     integer,
  status                varchar(255),
  processo_created_at   timestamp,
  -- Todos os valores encontrados nos caminhos do alias, separados por ' | ' (filtro "contém")
  bairro                text,
  endereco              text,
  -- Listas no formato ',3,7,' para o filtro CQL do GeoServer: atividade_ids LIKE '%,3,%'
  atividade_ids         text,
  tp_documento_ids      text,
  atividade_servico_id  integer,
  -- CoordenadaVisibilidadeService: denúncia com tp_denuncia oculto no mapa, ou processo sem denúncia
  -- e sem serviço. Vale só para as coordenadas do próprio Processo (coordenable_type = 'Processo').
  visivel_transparencia boolean NOT NULL DEFAULT true,
  updated_at            timestamp NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sislam_processos_tenant_processo
  ON sislam_processos (tenant_id, processo_id);

COMMIT;
