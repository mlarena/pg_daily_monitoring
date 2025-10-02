from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import psycopg2
import json
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'

CONFIG_FILE = 'config.json'

# Кастомные фильтры для шаблонов
@app.template_filter('number_format')
def number_format(value):
    """Форматирует число с разделителями тысяч"""
    try:
        if value is None:
            return "0"
        return f"{int(value):,}".replace(",", " ")
    except (ValueError, TypeError):
        return str(value)

@app.template_filter('tojson')
def tojson_filter(obj):
    """Фильтр для отображения JSON в шаблоне"""
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)

def load_config():
    """Загрузка конфигурации из файла"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
        return {}
    except (json.JSONDecodeError, Exception) as e:
        print(f"Ошибка загрузки конфигурации: {e}")
        if os.path.exists(CONFIG_FILE):
            backup_name = f"{CONFIG_FILE}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            os.rename(CONFIG_FILE, backup_name)
            print(f"Создан backup поврежденного файла: {backup_name}")
        return {}

def save_config(config):
    """Сохранение конфигурации в файл"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Ошибка сохранения конфигурации: {e}")
        return False

def test_postgres_connection(connection_string):
    """Тестирование подключения к PostgreSQL"""
    try:
        conn = psycopg2.connect(connection_string)
        conn.close()
        return True, "Подключение успешно!"
    except Exception as e:
        return False, f"Ошибка подключения: {str(e)}"

def check_pg_stat_statements(connection_string):
    """Проверка наличия расширения pg_stat_statements"""
    try:
        conn = psycopg2.connect(connection_string)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM pg_extension WHERE extname = 'pg_stat_statements';")
        result = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        return result is not None
    except Exception as e:
        print(f"Ошибка при проверке расширения pg_stat_statements: {e}")
        return False

def get_databases_list(connection_string):
    """Получение списка всех баз данных"""
    try:
        base_conn_string = connection_string.replace("dbname='postgres'", "dbname='postgres'")
        conn = psycopg2.connect(base_conn_string)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT datname 
            FROM pg_database 
            WHERE datistemplate = false 
            AND datname NOT LIKE 'template%'
            ORDER BY datname;
        """)
        
        databases = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        
        return databases
    except Exception as e:
        print(f"Ошибка при получении списка БД: {e}")
        return []

def get_postgres_info(connection_string):
    """Получение информации о PostgreSQL"""
    try:
        conn = psycopg2.connect(connection_string)
        cursor = conn.cursor()
        
        cursor.execute("SELECT version();")
        version = cursor.fetchone()[0]
        
        cursor.execute("SELECT pg_postmaster_start_time();")
        start_time = cursor.fetchone()[0]
        
        cursor.execute("SELECT pg_current_wal_lsn();")
        wal_lsn = cursor.fetchone()[0]
        
        # Проверяем наличие расширения
        has_pg_stat_statements = check_pg_stat_statements(connection_string)
        
        cursor.close()
        conn.close()
        
        return {
            'version': version,
            'start_time': start_time.strftime('%Y-%m-%d %H:%M:%S'),
            'wal_lsn': wal_lsn,
            'has_pg_stat_statements': has_pg_stat_statements,
            'success': True
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def get_key_metrics(connection_string):
    """Получение ключевых метрик базы данных"""
    try:
        conn = psycopg2.connect(connection_string)
        cursor = conn.cursor()
        
        query = """
        SELECT 
            datname,
            numbackends as connections,
            xact_commit as commits,
            xact_rollback as rollbacks,
            blks_read as disk_reads,
            blks_hit as cache_hits,
            tup_returned as rows_returned,
            tup_fetched as rows_fetched,
            tup_inserted as rows_inserted,
            tup_updated as rows_updated,
            tup_deleted as rows_deleted
        FROM pg_stat_database 
        WHERE datname = current_database();
        """
        
        cursor.execute(query)
        columns = [desc[0] for desc in cursor.description]
        result = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if result:
            metrics = dict(zip(columns, result))
            
            total_reads = metrics['disk_reads'] + metrics['cache_hits']
            if total_reads > 0:
                metrics['cache_hit_ratio'] = round((metrics['cache_hits'] / total_reads) * 100, 2)
            else:
                metrics['cache_hit_ratio'] = 0
                
            total_transactions = metrics['commits'] + metrics['rollbacks']
            if total_transactions > 0:
                metrics['rollback_ratio'] = round((metrics['rollbacks'] / total_transactions) * 100, 2)
            else:
                metrics['rollback_ratio'] = 0
                
            metrics['success'] = True
            return metrics
        else:
            return {'success': False, 'error': 'No data found'}
            
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def get_table_statistics(connection_string):
    """Получение статистики по таблицам"""
    try:
        conn = psycopg2.connect(connection_string)
        cursor = conn.cursor()
        
        query = """
        SELECT 
            schemaname,
            relname as table_name,
            COALESCE(NULLIF(seq_scan::text, '')::bigint, 0) as sequential_scans,
            COALESCE(NULLIF(seq_tup_read::text, '')::bigint, 0) as seq_rows_read,
            COALESCE(NULLIF(idx_scan::text, '')::bigint, 0) as index_scans,
            COALESCE(NULLIF(idx_tup_fetch::text, '')::bigint, 0) as index_rows_fetched,
            COALESCE(NULLIF(n_tup_ins::text, '')::bigint, 0) as inserts,
            COALESCE(NULLIF(n_tup_upd::text, '')::bigint, 0) as updates,
            COALESCE(NULLIF(n_tup_del::text, '')::bigint, 0) as deletes,
            COALESCE(NULLIF(n_tup_hot_upd::text, '')::bigint, 0) as hot_updates,
            COALESCE(NULLIF(n_live_tup::text, '')::bigint, 0) as live_rows,
            COALESCE(NULLIF(n_dead_tup::text, '')::bigint, 0) as dead_rows
        FROM pg_stat_all_tables
        WHERE schemaname NOT LIKE 'pg_%' 
        ORDER BY COALESCE(NULLIF(n_dead_tup::text, '')::bigint, 0) DESC;
        """
        
        cursor.execute(query)
        columns = [desc[0] for desc in cursor.description]
        results = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        if results:
            tables = []
            for row in results:
                table_data = dict(zip(columns, row))
                
                sequential_scans = table_data['sequential_scans']
                index_scans = table_data['index_scans']
                total_scans = sequential_scans + index_scans
                
                if total_scans > 0:
                    table_data['index_scan_ratio'] = round((index_scans / total_scans) * 100, 2)
                else:
                    table_data['index_scan_ratio'] = 0
                    
                live_rows = table_data['live_rows']
                dead_rows = table_data['dead_rows']
                total_rows = live_rows + dead_rows
                
                if total_rows > 0:
                    table_data['dead_row_ratio'] = round((dead_rows / total_rows) * 100, 2)
                else:
                    table_data['dead_row_ratio'] = 0
                    
                tables.append(table_data)
            
            return {
                'tables': tables,
                'total_tables': len(tables),
                'success': True
            }
        else:
            return {'success': False, 'error': 'No table statistics found'}
            
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def get_full_detailed_metrics(connection_string):
    """Получение полной детальной статистики"""
    try:
        conn = psycopg2.connect(connection_string)
        cursor = conn.cursor()
        
        # Комплексный запрос с множеством метрик
        query = """
        SELECT 
            -- Базовая информация
            current_database() as database_name,
            current_user as current_user,
            inet_server_addr() as server_address,
            inet_server_port() as server_port,
            
            -- Статистика базы данных
            (SELECT count(*) FROM pg_stat_activity) as total_connections,
            (SELECT count(*) FROM pg_stat_activity WHERE state = 'active') as active_connections,
            (SELECT count(*) FROM pg_stat_activity WHERE state = 'idle') as idle_connections,
            
            -- Размер базы данных
            pg_database_size(current_database()) as database_size_bytes,
            
            -- Статистика транзакций
            xact_commit as total_commits,
            xact_rollback as total_rollbacks,
            
            -- Статистика ввода/вывода
            blks_read as blocks_read,
            blks_hit as blocks_hit,
            
            -- Статистика запросов
            tup_returned as tuples_returned,
            tup_fetched as tuples_fetched,
            tup_inserted as tuples_inserted,
            tup_updated as tuples_updated,
            tup_deleted as tuples_deleted,
            
            -- Время работы
            (SELECT extract(epoch from now() - pg_postmaster_start_time())) as uptime_seconds,
            
            -- Настройки
            (SELECT setting FROM pg_settings WHERE name = 'shared_buffers') as shared_buffers,
            (SELECT setting FROM pg_settings WHERE name = 'work_mem') as work_mem,
            (SELECT setting FROM pg_settings WHERE name = 'maintenance_work_mem') as maintenance_work_mem
            
        FROM pg_stat_database 
        WHERE datname = current_database();
        """
        
        cursor.execute(query)
        columns = [desc[0] for desc in cursor.description]
        result = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if result:
            metrics = dict(zip(columns, result))
            
            # Рассчитываем дополнительные метрики
            if metrics['blocks_read'] + metrics['blocks_hit'] > 0:
                metrics['cache_hit_ratio'] = round((metrics['blocks_hit'] / (metrics['blocks_read'] + metrics['blocks_hit'])) * 100, 2)
            else:
                metrics['cache_hit_ratio'] = 0
                
            if metrics['total_commits'] + metrics['total_rollbacks'] > 0:
                metrics['rollback_ratio'] = round((metrics['total_rollbacks'] / (metrics['total_commits'] + metrics['total_rollbacks'])) * 100, 2)
            else:
                metrics['rollback_ratio'] = 0
                
            # Форматируем размер базы данных
            metrics['database_size_mb'] = round(metrics['database_size_bytes'] / (1024 * 1024), 2)
            metrics['database_size_gb'] = round(metrics['database_size_bytes'] / (1024 * 1024 * 1024), 2)
            
            metrics['success'] = True
            return metrics
        else:
            return {'success': False, 'error': 'No detailed metrics found'}
            
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def get_problematic_queries(connection_string):
    """Поиск проблемных запросов через pg_stat_statements"""
    try:
        conn = psycopg2.connect(connection_string)
        cursor = conn.cursor()
        
        # Проверяем наличие расширения
        if not check_pg_stat_statements(connection_string):
            return {'success': False, 'error': 'Расширение pg_stat_statements не установлено'}
        
        query = """
        SELECT 
            query,
            calls as total_calls,
            total_exec_time as total_time,
            mean_exec_time as avg_time,
            rows as rows_processed,
            shared_blks_hit as cache_hits,
            shared_blks_read as disk_reads,
            100.0 * shared_blks_hit / nullif(shared_blks_hit + shared_blks_read, 0) as cache_hit_ratio
        FROM pg_stat_statements 
        WHERE query NOT LIKE '%pg_stat_statements%'
        ORDER BY total_exec_time DESC
        LIMIT 50;
        """
        
        cursor.execute(query)
        columns = [desc[0] for desc in cursor.description]
        results = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        if results:
            queries = []
            for row in results:
                query_data = dict(zip(columns, row))
                
                # Форматируем запрос для лучшего отображения
                if query_data['query']:
                    query_data['short_query'] = query_data['query'][:100] + '...' if len(query_data['query']) > 100 else query_data['query']
                
                queries.append(query_data)
            
            return {
                'queries': queries,
                'total_queries': len(queries),
                'success': True
            }
        else:
            return {'success': False, 'error': 'No query statistics found'}
            
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def get_performance_metrics(connection_string):
    """Мониторинг производительности"""
    try:
        conn = psycopg2.connect(connection_string)
        cursor = conn.cursor()
        
        # Комплексные метрики производительности
        query = """
        WITH db_stats AS (
            SELECT 
                datname,
                xact_commit,
                xact_rollback,
                blks_read,
                blks_hit,
                tup_returned,
                tup_fetched,
                tup_inserted,
                tup_updated,
                tup_deleted
            FROM pg_stat_database 
            WHERE datname = current_database()
        ),
        table_stats AS (
            SELECT 
                count(*) as total_tables,
                sum(n_live_tup) as total_live_rows,
                sum(n_dead_tup) as total_dead_rows,
                sum(seq_scan) as total_seq_scans,
                sum(idx_scan) as total_idx_scans
            FROM pg_stat_all_tables 
            WHERE schemaname NOT LIKE 'pg_%'
        ),
        index_stats AS (
            SELECT 
                count(*) as total_indexes,
                sum(idx_scan) as total_index_scans
            FROM pg_stat_all_indexes
        ),
        connection_stats AS (
            SELECT 
                count(*) as total_connections,
                count(*) FILTER (WHERE state = 'active') as active_connections
            FROM pg_stat_activity
            WHERE datname = current_database()
        )
        SELECT 
            -- Статистика БД
            d.xact_commit as commits,
            d.xact_rollback as rollbacks,
            d.blks_read as disk_reads,
            d.blks_hit as cache_hits,
            
            -- Статистика таблиц
            t.total_tables,
            t.total_live_rows,
            t.total_dead_rows,
            t.total_seq_scans,
            t.total_idx_scans,
            
            -- Статистика индексов
            i.total_indexes,
            i.total_index_scans,
            
            -- Статистика подключений
            c.total_connections,
            c.active_connections,
            
            -- Расчетные метрики
            CASE 
                WHEN (d.blks_read + d.blks_hit) > 0 THEN 
                    round(100.0 * d.blks_hit / (d.blks_read + d.blks_hit), 2)
                ELSE 0 
            END as cache_hit_ratio,
            
            CASE 
                WHEN (t.total_seq_scans + t.total_idx_scans) > 0 THEN 
                    round(100.0 * t.total_idx_scans / (t.total_seq_scans + t.total_idx_scans), 2)
                ELSE 0 
            END as index_usage_ratio,
            
            CASE 
                WHEN (t.total_live_rows + t.total_dead_rows) > 0 THEN 
                    round(100.0 * t.total_dead_rows / (t.total_live_rows + t.total_dead_rows), 2)
                ELSE 0 
            END as dead_rows_ratio
            
        FROM db_stats d, table_stats t, index_stats i, connection_stats c;
        """
        
        cursor.execute(query)
        columns = [desc[0] for desc in cursor.description]
        result = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if result:
            metrics = dict(zip(columns, result))
            metrics['success'] = True
            return metrics
        else:
            return {'success': False, 'error': 'No performance metrics found'}
            
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

# Маршруты
@app.route('/')
def index():
    config = load_config()
    has_pg_stat_statements = False
    
    if 'postgres' in config and 'connection_string' in config['postgres']:
        connection_string = config['postgres']['connection_string']
        has_pg_stat_statements = check_pg_stat_statements(connection_string)
    
    return render_template('index.html', 
                         has_pg_stat_statements=has_pg_stat_statements)

@app.route('/connect_to_postgres', methods=['GET', 'POST'])
def connect_to_postgres():
    config = load_config()
    connection_status = None
    connection_string = ""
    databases_list = []
    has_pg_stat_statements = False
    
    if request.method == 'POST':
        dbname = request.form.get('dbname', 'postgres')
        user = request.form.get('user', 'postgres')
        password = request.form.get('password', '')
        host = request.form.get('host', 'localhost')
        port = request.form.get('port', '5432')
        
        connection_string = f"dbname='{dbname}' user='{user}' password='{password}' host='{host}' port='{port}'"
        
        success, message = test_postgres_connection(connection_string)
        connection_status = {
            'success': success,
            'message': message
        }
        
        if success:
            base_conn_string = f"dbname='postgres' user='{user}' password='{password}' host='{host}' port='{port}'"
            databases_list = get_databases_list(base_conn_string)
            
            # Проверяем наличие расширения
            has_pg_stat_statements = check_pg_stat_statements(connection_string)
            
            try:
                conn = psycopg2.connect(connection_string)
                cursor = conn.cursor()
                cursor.execute("SELECT current_database(), version()")
                db_info = cursor.fetchone()
                cursor.close()
                conn.close()
                
                print(f"DEBUG: Подключение установлено к базе: {db_info[0]}")
                print(f"DEBUG: Доступные БД: {databases_list}")
                print(f"DEBUG: pg_stat_statements доступен: {has_pg_stat_statements}")
                
            except Exception as e:
                print(f"DEBUG: Ошибка при получении информации о БД: {e}")
            
            config['postgres'] = {
                'dbname': dbname,
                'user': user,
                'password': password,
                'host': host,
                'port': port,
                'connection_string': connection_string,
                'has_pg_stat_statements': has_pg_stat_statements
            }
            if save_config(config):
                session['postgres_connected'] = True
                session['connection_string'] = connection_string
                session['has_pg_stat_statements'] = has_pg_stat_statements
            else:
                connection_status = {
                    'success': False,
                    'message': 'Ошибка сохранения конфигурации'
                }
    
    postgres_config = config.get('postgres', {})
    
    return render_template('connect_to_postgres.html', 
                         connection_status=connection_status,
                         connection_string=connection_string or postgres_config.get('connection_string', ''),
                         config=postgres_config,
                         databases_list=databases_list,
                         has_pg_stat_statements=has_pg_stat_statements)

@app.route('/version_and_information')
def version_and_information():
    config = load_config()
    postgres_info = None
    has_pg_stat_statements = False
    
    if 'postgres' in config and 'connection_string' in config['postgres']:
        connection_string = config['postgres']['connection_string']
        postgres_info = get_postgres_info(connection_string)
        has_pg_stat_statements = config['postgres'].get('has_pg_stat_statements', False)
    
    return render_template('version_and_information.html', 
                         postgres_info=postgres_info,
                         connected='postgres' in config,
                         has_pg_stat_statements=has_pg_stat_statements)

@app.route('/key_metrics')
def key_metrics():
    config = load_config()
    metrics = None
    has_pg_stat_statements = False
    
    if 'postgres' in config and 'connection_string' in config['postgres']:
        connection_string = config['postgres']['connection_string']
        metrics = get_key_metrics(connection_string)
        has_pg_stat_statements = config['postgres'].get('has_pg_stat_statements', False)
    
    from datetime import datetime
    now = datetime.now()
    
    return render_template('key_metrics.html', 
                         metrics=metrics,
                         connected='postgres' in config,
                         now=now,
                         has_pg_stat_statements=has_pg_stat_statements)

@app.route('/general_statistics_for_tables')
def general_statistics_for_tables():
    config = load_config()
    table_stats = None
    has_pg_stat_statements = False
    
    # Получаем параметры из URL
    sort_by = request.args.get('sort_by', 'dead_rows')
    sort_order = request.args.get('sort_order', 'desc')
    group_by_schema = request.args.get('group_by_schema', 'true').lower() == 'true'
    
    if 'postgres' in config and 'connection_string' in config['postgres']:
        connection_string = config['postgres']['connection_string']
        table_stats = get_table_statistics(connection_string)
        has_pg_stat_statements = config['postgres'].get('has_pg_stat_statements', False)
        
        # Применяем сортировку на стороне Python
        if table_stats and table_stats.get('success'):
            tables = table_stats['tables']
            
            # Определяем направление сортировки
            reverse = sort_order.lower() == 'desc'
            
            # Сортируем таблицы
            if sort_by in ['schemaname', 'table_name']:
                tables.sort(key=lambda x: x.get(sort_by, ''), reverse=reverse)
            else:
                tables.sort(key=lambda x: x.get(sort_by, 0), reverse=reverse)
            
            table_stats['tables'] = tables
            table_stats['sort_by'] = sort_by
            table_stats['sort_order'] = sort_order
            table_stats['group_by_schema'] = group_by_schema
    
    from datetime import datetime
    now = datetime.now()
    
    return render_template('general_statistics_for_tables.html', 
                         table_stats=table_stats,
                         connected='postgres' in config,
                         now=now,
                         has_pg_stat_statements=has_pg_stat_statements)

@app.route('/full_detailed_query_with_all_metrics')
def full_detailed_query_with_all_metrics():
    config = load_config()
    detailed_metrics = None
    has_pg_stat_statements = False
    
    if 'postgres' in config and 'connection_string' in config['postgres']:
        connection_string = config['postgres']['connection_string']
        detailed_metrics = get_full_detailed_metrics(connection_string)
        has_pg_stat_statements = config['postgres'].get('has_pg_stat_statements', False)
    
    from datetime import datetime
    now = datetime.now()
    
    return render_template('full_detailed_query_with_all_metrics.html', 
                         detailed_metrics=detailed_metrics,
                         connected='postgres' in config,
                         now=now,
                         has_pg_stat_statements=has_pg_stat_statements)

@app.route('/find_problematic_queries')
def find_problematic_queries():
    config = load_config()
    queries_data = None
    has_pg_stat_statements = False
    
    if 'postgres' in config and 'connection_string' in config['postgres']:
        connection_string = config['postgres']['connection_string']
        queries_data = get_problematic_queries(connection_string)
        has_pg_stat_statements = config['postgres'].get('has_pg_stat_statements', False)
    
    from datetime import datetime
    now = datetime.now()
    
    return render_template('find_problematic_queries.html', 
                         queries_data=queries_data,
                         connected='postgres' in config,
                         now=now,
                         has_pg_stat_statements=has_pg_stat_statements)

@app.route('/performance_monitoring')
def performance_monitoring():
    config = load_config()
    performance_data = None
    has_pg_stat_statements = False
    
    if 'postgres' in config and 'connection_string' in config['postgres']:
        connection_string = config['postgres']['connection_string']
        performance_data = get_performance_metrics(connection_string)
        has_pg_stat_statements = config['postgres'].get('has_pg_stat_statements', False)
    
    from datetime import datetime
    now = datetime.now()
    
    return render_template('performance_monitoring.html', 
                         performance_data=performance_data,
                         connected='postgres' in config,
                         now=now,
                         has_pg_stat_statements=has_pg_stat_statements)

@app.route('/debug_database')
def debug_database():
    """Временный маршрут для отладки"""
    config = load_config()
    
    if 'postgres' not in config:
        return "Подключение не настроено"
    
    connection_string = config['postgres']['connection_string']
    
    try:
        conn = psycopg2.connect(connection_string)
        cursor = conn.cursor()
        
        cursor.execute("SELECT current_database()")
        current_db = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT schemaname, relname 
            FROM pg_stat_all_tables 
            WHERE schemaname NOT LIKE 'pg_%' 
            LIMIT 10
        """)
        tables = cursor.fetchall()
        
        cursor.execute("""
            SELECT schemaname, relname, seq_scan, n_live_tup, n_dead_tup
            FROM pg_stat_all_tables 
            WHERE schemaname NOT LIKE 'pg_%' 
            ORDER BY n_dead_tup DESC
            LIMIT 3
        """)
        stats = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return f"""
        <h2>Отладочная информация</h2>
        <p><strong>Текущая база данных:</strong> {current_db}</p>
        
        <h3>Таблицы в базе:</h3>
        <ul>
            {"".join(f"<li>{table[0]}.{table[1]}</li>" for table in tables)}
        </ul>
        
        <h3>Статистика (первые 3 таблицы):</h3>
        <pre>
            {"".join(str(stat) + "\\n" for stat in stats)}
        </pre>
        """
        
    except Exception as e:
        return f"Ошибка: {e}"

@app.route('/settings')
def settings():
    config = load_config()
    has_pg_stat_statements = config.get('postgres', {}).get('has_pg_stat_statements', False)
    return render_template('settings.html', has_pg_stat_statements=has_pg_stat_statements)

@app.route('/reports')
def reports():
    config = load_config()
    has_pg_stat_statements = config.get('postgres', {}).get('has_pg_stat_statements', False)
    return render_template('reports.html', has_pg_stat_statements=has_pg_stat_statements)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f, indent=4)
        print(f"Создан новый файл конфигурации: {CONFIG_FILE}")
    
    app.run(debug=True)