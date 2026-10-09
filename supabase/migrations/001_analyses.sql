-- Apply in the Supabase SQL editor. Server service-role access only.
create table public.analyses (
  id text primary key check (id ~ '^[0-9a-f]{32}$'),
  owner text not null,
  status text not null check (status in ('queued', 'extracting', 'transcribing', 'transcribed', 'failed')),
  created_at double precision not null,
  updated_at double precision not null,
  sha256 text not null,
  size_bytes bigint not null,
  metadata jsonb,
  transcript jsonb,
  error jsonb,
  attempts integer not null default 1 check (attempts between 1 and 3),
  investigation_status text not null default 'not_started' check (investigation_status = 'not_started')
);
create index analyses_status_created on public.analyses(status, created_at);
create index analyses_owner on public.analyses(owner);
alter table public.analyses enable row level security;
revoke all on public.analyses from anon, authenticated;
grant all on public.analyses to service_role;
-- No public policies. Browsers access session-filtered FastAPI endpoints only.
