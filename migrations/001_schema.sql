-- Squad state. Every deal has an ORIGIN and a NEXT ACTION + DATE (NOT NULL: the database enforces it),
-- and 'lost' requires a reason.

create extension if not exists vector;

-- Full-text search language for customer memory. Change 'portuguese' to your customers' language.
create text search configuration squad (copy = portuguese);

create type stage as enum
  ('target','contacted','preview','negotiation','won','delivered','recurring','lost');
create type ticket_type as enum ('briefing','bug','change','new_scope');
create type ticket_status as enum
  ('open','queued','running','preview','approved','published','cancelled');
create type approval_status as enum ('pending','approved','rejected');

-- One hunter round: which lead source, which criteria, where, and the lead cap.
create table campaigns (
  id         serial primary key,
  source     text  not null,
  criteria   jsonb not null default '{}',
  location   text  not null,
  lead_cap   int   not null check (lead_cap between 1 and 10),
  created_at timestamptz not null default now()
);

-- One row per business, from lead to customer (the stage says where it is).
create table deals (
  slug                     text primary key,
  campaign_id              int references campaigns(id),
  origin                   text not null check (origin <> ''),
  dossier                  jsonb not null default '{}',
  score                    int check (score between 0 and 10),
  stage                    stage not null default 'target',
  next_action              text not null check (next_action <> ''),
  next_action_at           date not null,
  lost_reason              text,
  phone                    text,
  repo                     text,
  domain                   text,
  deploy_target            text,
  behavior_profile         text,
  monthly_changes_included int  not null default 3,
  priority                 int  not null default 0,
  expires_at               timestamptz,  -- retention: untouched leads expire (purge); cleared when leaving 'target'
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now(),
  check (stage <> 'lost' or lost_reason is not null)
);

create table tickets (
  id               serial primary key,
  slug             text not null references deals(slug) on delete cascade,
  type             ticket_type not null,
  status           ticket_status not null default 'open',
  description      text not null,
  priority         int  not null default 0,
  preview_url      text,
  public_url       text,
  human_hours      numeric(6,2) not null default 0,  -- the metric that validates pricing
  machine_minutes  numeric(8,1) not null default 0,
  cost_usd         numeric(8,4) not null default 0,
  attempts         int  not null default 0,
  run_note         text,
  preview_notified boolean not null default false,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create table approvals (
  id          serial primary key,
  kind        text not null,
  ref         text not null,
  context     text not null,
  status      approval_status not null default 'pending',
  created_at  timestamptz not null default now(),
  decided_at  timestamptz,
  notified_at timestamptz
);

-- Audit trail: which role did what.
create table events (
  id      bigserial primary key,
  actor   text not null,
  action  text not null,
  ref     text,
  payload jsonb not null default '{}',
  at      timestamptz not null default now()
);

-- Standing instructions from the human to a role. Every directive expires.
create table directives (
  id         serial primary key,
  role       text not null check (role in ('hunter','sales','account','builder','all')),
  text       text not null check (text <> '' and length(text) <= 500),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  active     boolean not null default true
);

-- Soft parameters, human-writable only (validated by the CLI). Safety caps live in code and config.
create table params (
  key        text primary key,
  value      text not null,
  updated_at timestamptz not null default now()
);
insert into params (key, value) values
  ('leads_per_round', '5'),
  ('hunter_paused', 'no'),
  ('builder_mode', 'spawn'),
  ('preview_revisions', '2');

-- Per-customer memory (RAG). Embedding dimension must match memory.MODEL.
create table memories (
  id         bigserial primary key,
  slug       text not null references deals(slug) on delete cascade,
  kind       text not null check (kind in ('fact','preference','decision','conversation')),
  text       text not null,
  embedding  vector(384),
  meta       jsonb not null default '{}',
  created_at timestamptz not null default now(),
  tsv        tsvector generated always as (to_tsvector('squad', text)) stored
);

create unique index on approvals (kind, ref) where status = 'pending';
create index on deals (stage);
create index on tickets (slug, type, created_at);
create index on tickets (status, priority);
create index on directives (role, active, expires_at);
create index on memories (slug);
create index on memories using gin (tsv);
create index on memories using hnsw (embedding vector_cosine_ops);
