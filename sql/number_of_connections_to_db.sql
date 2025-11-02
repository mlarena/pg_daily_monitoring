SELECT 
    datname,
    count(*) as connections,
    count(*) FILTER (WHERE state = 'active') as active_connections
FROM pg_stat_activity 
GROUP BY datname
ORDER BY connections DESC;