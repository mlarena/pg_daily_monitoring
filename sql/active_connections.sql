SELECT 
    datname as database,
    usename as username,
    application_name,
    client_addr as client_ip,
    state,
    query_start,
    query
FROM pg_stat_activity 
WHERE state = 'active' 
  AND datname = current_database();