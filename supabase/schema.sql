create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  quota_reports int not null default 0,
  used_reports int not null default 0,
  quota_reset_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create table public.reports (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  niche text not null,
  region text not null,
  model text,
  file_name text,
  file_path text,
  status text not null,          -- 'done' | 'failed'
  warning text,
  report_md text not null default '',
  created_at timestamptz not null default now()
);

-- профиль создаётся при регистрации
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer as $$
begin
  insert into public.profiles (id, email) values (new.id, new.email);
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- атомарное списание квоты с ленивым сбросом окна; вызывает service-role
create or replace function public.consume_report_quota(p_user uuid)
returns boolean language plpgsql security definer as $$
declare v_used int; v_quota int; v_reset timestamptz;
begin
  select used_reports, quota_reports, quota_reset_at
    into v_used, v_quota, v_reset
    from public.profiles where id = p_user for update;
  if not found then return false; end if;
  if now() >= v_reset then
    v_used := 0; v_reset := now() + interval '30 days';
  end if;
  if v_used >= v_quota then
    update public.profiles set used_reports = v_used, quota_reset_at = v_reset
      where id = p_user;
    return false;
  end if;
  update public.profiles set used_reports = v_used + 1, quota_reset_at = v_reset
    where id = p_user;
  return true;
end $$;

-- RLS: deny-by-default + право читать свои строки (защита, если однажды
-- запрос пойдёт с user-JWT вместо service-role)
alter table public.profiles enable row level security;
alter table public.reports enable row level security;
create policy "own profile read" on public.profiles
  for select using (auth.uid() = id);
create policy "own reports read" on public.reports
  for select using (auth.uid() = user_id);

insert into storage.buckets (id, name, public) values ('reports', 'reports', false)
  on conflict do nothing;
create policy "own files read" on storage.objects for select
  using (bucket_id = 'reports' and (storage.foldername(name))[1] = auth.uid()::text);
