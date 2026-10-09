-- Apply after 001 and 002. No public policies or source URL fetching.
alter table public.analyses drop constraint analyses_status_check;
alter table public.analyses add constraint analyses_status_check check
  (status in ('queued','downloading','extracting','transcribing','transcribed','completed','failed'));
alter table public.analyses drop constraint analyses_investigation_status_check;
alter table public.analyses add constraint analyses_investigation_status_check check
  (investigation_status in ('not_started','queued','retrieving','interpreting','completed',
    'source_not_found','inconclusive','setup_required','unavailable','demo_only'));
alter table public.analyses add column report jsonb;
alter table public.analyses add column investigation_error jsonb;
alter table public.analyses add column investigation_attempts integer not null default 0
  check (investigation_attempts between 0 and 5);
create index analyses_investigation_created on public.analyses(investigation_status, created_at);
