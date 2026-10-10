-- Least privilege for agents and the runner: no superuser, no deciding approvals, no params or directives.
-- Change the password before real use: alter role squad_agent password '...'; and update SQUAD_AGENT_DSN.
do $$ begin
  if not exists (select from pg_roles where rolname = 'squad_agent') then
    create role squad_agent login password 'squad_agent_dev' nosuperuser nocreatedb nocreaterole;
  end if;
end $$;

grant usage on schema public to squad_agent;
grant select on all tables in schema public to squad_agent;
grant usage on all sequences in schema public to squad_agent;
grant insert on campaigns, deals, tickets, approvals, events, memories to squad_agent;
grant update on deals, tickets to squad_agent;
