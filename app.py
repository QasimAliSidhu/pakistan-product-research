import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import csv
from datetime import date
import io
import json
from pathlib import Path
import secrets
import sqlite3
import threading
from flask import Flask, jsonify, request, send_from_directory, Response
from analytics import DEFAULTS, economics, validate_inputs, forecast
import research
from sources import SOURCES

ROOT=Path(__file__).resolve().parent
POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='product-research')
LOCK=threading.Lock()

def create_app(state=None):
    app=Flask(__name__,static_folder='static')
    app.config['MAX_CONTENT_LENGTH']=5*1024*1024
    state=Path(state or ROOT/'data'); state.mkdir(parents=True,exist_ok=True)
    token=secrets.token_urlsafe(32)
    @contextmanager
    def db():
        connection=sqlite3.connect(state/'research.sqlite3',timeout=20)
        connection.row_factory=sqlite3.Row
        try:
            with connection: yield connection
        finally: connection.close()
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,name TEXT UNIQUE,inputs TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,product_id INTEGER,created TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS history(product_id INTEGER,date TEXT,metric TEXT,value REAL,geo TEXT,source TEXT,observed_at TEXT,PRIMARY KEY(product_id,date,metric));
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,product_id INTEGER,kind TEXT,status TEXT,message TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS quotes(id INTEGER PRIMARY KEY,product_id INTEGER,supplier TEXT,url TEXT,unit_cost REAL,inbound REAL,duty REAL,moq INTEGER,lead_days INTEGER,quote_date TEXT,confirmed INTEGER);
        ''')
        c.execute("UPDATE jobs SET status='interrupted',message='App restarted; run the research again.' WHERE status IN ('queued','running')")
    def product(pid):
        with db() as c: row=c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
        if not row: raise ValueError('Product not found')
        return dict(row)
    def detail(pid):
        p=product(pid); p['search_keyword']=research.normalize_keyword(p['name']); p['inputs']=json.loads(p['inputs']); p['economics']=economics(p['inputs'])
        with db() as c:
            obs=c.execute('SELECT * FROM observations WHERE product_id=? ORDER BY id DESC LIMIT 20',(pid,)).fetchall()
            p['research']=research.merge_evidence([json.loads(o['payload']) for o in obs],p['name'])
            p['history']=[dict(r) for r in c.execute('SELECT * FROM history WHERE product_id=? ORDER BY metric,date',(pid,))]
            p['jobs']=[dict(r) for r in c.execute('SELECT * FROM jobs WHERE product_id=? ORDER BY created DESC LIMIT 8',(pid,))]
            p['quotes']=[dict(r) for r in c.execute('SELECT *,unit_cost+inbound+duty AS landed_cost FROM quotes WHERE product_id=? ORDER BY confirmed DESC,unit_cost+inbound+duty',(pid,))]
        p['forecasts']={metric:forecast([r for r in p['history'] if r['metric']==metric]) for metric in sorted({r['metric'] for r in p['history']})}
        return p
    @app.before_request
    def protect():
        # Loopback app; prevent browser-origin cross-site writes / DNS rebinding.
        if request.host.split(':')[0] not in ('localhost','127.0.0.1','[::1]'):
            return jsonify(error='Use localhost or 127.0.0.1'),403
        if request.method in ('POST','PUT','DELETE') and request.headers.get('X-Research-Token')!=token:
            return jsonify(error='Refresh the app before changing data'),403
    @app.errorhandler(ValueError)
    def invalid(exc): return jsonify(error=str(exc)),400
    @app.errorhandler(sqlite3.IntegrityError)
    def duplicate(exc): return jsonify(error='That product already exists'),409
    @app.errorhandler(413)
    def too_large(exc): return jsonify(error='Upload must be under 5 MB'),413
    @app.get('/')
    def index(): return send_from_directory(ROOT/'static','index.html')
    @app.get('/api/bootstrap')
    def bootstrap(): return jsonify(token=token,defaults=DEFAULTS,sites=research.SITES)
    @app.get('/api/sources')
    def sources(): return jsonify(SOURCES)
    @app.get('/api/products')
    def products():
        with db() as c: ids=[r['id'] for r in c.execute('SELECT id FROM products ORDER BY id DESC')]
        rows=[detail(pid) for pid in ids]
        # Rank only confirmed quotes and fee profiles; cash profit per placed order.
        rows.sort(key=lambda p:(p['economics']['ready'] and p['economics']['budget_remaining']>=0,p['economics']['expected_order_profit'] if p['economics']['expected_order_profit'] is not None else -1e20),reverse=True)
        return jsonify(rows)
    @app.post('/api/products')
    def add():
        name=str((request.get_json() or {}).get('name','')).strip()
        if not 2<=len(name)<=120: raise ValueError('Enter a product keyword between 2 and 120 characters')
        with db() as c:
            cursor=c.execute('INSERT INTO products(name,inputs,created) VALUES (?,?,?)',(name,json.dumps(DEFAULTS),research.now()))
            pid=cursor.lastrowid
        return jsonify(detail(pid)),201
    @app.get('/api/products/<int:pid>')
    def get_product(pid): return jsonify(detail(pid))
    @app.put('/api/products/<int:pid>/inputs')
    def save_inputs(pid):
        product(pid); inputs=validate_inputs(request.get_json() or {})
        with db() as c: c.execute('UPDATE products SET inputs=? WHERE id=?',(json.dumps(inputs),pid))
        return jsonify(detail(pid))
    @app.post('/api/products/<int:pid>/quotes')
    def add_quote(pid):
        product(pid); body=request.get_json() or {}
        supplier=str(body.get('supplier','')).strip()[:200]
        if not supplier: raise ValueError('Enter the supplier name')
        url=str(body.get('url','')).strip()[:1000]
        if url and not url.startswith(('https://','http://')): raise ValueError('Supplier link must be an HTTP(S) URL')
        values=validate_inputs(dict(purchase_cost=body.get('unit_cost',0),inbound=body.get('inbound',0),duty=body.get('duty',0),
            moq=body.get('moq',1),order_qty=max(float(body.get('moq',1)),1),supplier_lead_days=body.get('lead_days',14),
            supplier_name=supplier,quote_date=body.get('quote_date',''),verified_quote=body.get('confirmed',False)))
        if values['purchase_cost']<=0: raise ValueError('Enter a positive purchase price in PKR')
        with db() as c:
            c.execute('INSERT INTO quotes(product_id,supplier,url,unit_cost,inbound,duty,moq,lead_days,quote_date,confirmed) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (pid,supplier,url,values['purchase_cost'],values['inbound'],values['duty'],int(values['moq']),values['supplier_lead_days'],values['quote_date'],int(values['verified_quote'])))
        return jsonify(detail(pid)),201
    @app.post('/api/products/<int:pid>/quotes/<int:qid>/select')
    def select_quote(pid,qid):
        p=product(pid)
        with db() as c: q=c.execute('SELECT * FROM quotes WHERE product_id=? AND id=?',(pid,qid)).fetchone()
        if not q: raise ValueError('Quote not found')
        inputs=json.loads(p['inputs']); inputs.update(purchase_cost=q['unit_cost'],inbound=q['inbound'],duty=q['duty'],
            moq=q['moq'],order_qty=max(inputs['order_qty'],q['moq']),supplier_lead_days=q['lead_days'],supplier_name=q['supplier'],
            supplier_url=q['url'],quote_date=q['quote_date'],verified_quote=bool(q['confirmed']))
        with db() as c: c.execute('UPDATE products SET inputs=? WHERE id=?',(json.dumps(inputs),pid))
        return jsonify(detail(pid))
    def progress(jid,message,status='running'):
        with db() as c: c.execute('UPDATE jobs SET status=?,message=? WHERE id=?',(status,message,jid))
    def worker(jid,pid,kind,sites,url=''):
        try:
            name=product(pid)['name']; progress(jid,'Starting '+kind)
            if kind=='research':
                payload=research.collect(name,lambda m:progress(jid,m),sites)
                with db() as c: c.execute('INSERT INTO observations(product_id,created,payload) VALUES (?,?,?)',(pid,research.now(),json.dumps(payload)))
                if not payload['evidence']:
                    progress(jid,f"No relevant sources found for '{payload['searched_keyword']}'. Review search errors or try a narrower product phrase.",'failed'); return
                offers=sum(r.get('status')=='observed metadata' for r in payload['evidence'])
                sources=len(payload['evidence'])
                progress(jid,f'{offers} listing observations; {sources-offers} discovery links or collection failures. Sales history and supplier costs are not confirmed.','completed' if offers else 'incomplete')
                return
            elif kind=='page':
                payload=detail(pid)['research'] or dict(keyword=name,evidence=[],errors=[])
                payload['observed_at']=research.now()
                collected=research.inspect(dict(title=name,url=url,snippet='',engine='user-supplied public page'),'marketplace')
                replacements={r['url'] for r in collected}
                payload['evidence']=[r for r in payload['evidence'] if r['url'] not in replacements]+collected
                with db() as c: c.execute('INSERT INTO observations(product_id,created,payload) VALUES (?,?,?)',(pid,research.now(),json.dumps(payload)))
            else:
                rows=research.trends(name)
                if not rows: raise ValueError('Google returned no complete historical points for Pakistan')
                with db() as c:
                    # Replace one series to avoid mixing independently normalized fetches.
                    c.execute("DELETE FROM history WHERE product_id=? AND metric='interest'",(pid,))
                    for r in rows:
                        c.execute('INSERT INTO history VALUES (?,?,?,?,?,?,?)',(pid,r['date'],r['metric'],r['value'],r['geo'],r['source'],r['observed_at']))
            progress(jid,'Finished. Review source evidence and assumptions.','completed')
        except Exception as exc: progress(jid,str(exc)[:700],'failed')
    @app.post('/api/products/<int:pid>/jobs')
    def start_job(pid):
        product(pid); body=request.get_json() or {}; kind=body.get('kind','research')
        if kind not in ('research','trends','page'): raise ValueError('Unknown research operation')
        url=research.public_url(str(body.get('url',''))) if kind=='page' else ''
        sites=body.get('sites',research.SITES)
        if not isinstance(sites,list) or not sites or any(s not in research.SITES for s in sites): raise ValueError('Choose supported marketplaces')
        with LOCK:
            with db() as c:
                active=c.execute("SELECT id FROM jobs WHERE status IN ('running','queued')").fetchone()
                if active: return jsonify(error='A research run is already active. Let it finish before starting another.'),409
                jid=secrets.token_hex(12)
                c.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',(jid,pid,kind,'queued','Waiting for researcher',research.now()))
            POOL.submit(worker,jid,pid,kind,sites,url)
        return jsonify(id=jid),202
    @app.post('/api/products/<int:pid>/history')
    def import_history(pid):
        product(pid)
        if 'file' not in request.files: raise ValueError('Choose a CSV file')
        raw=request.files['file'].read().decode('utf-8-sig')
        lines=raw.splitlines(); parsed=[]
        # Accept native Google Trends exports (metadata line + Month/Week header).
        header=next((i for i,line in enumerate(lines) if line.startswith(('Month,','Week,','Date,','date,'))),None)
        if header is None: raise ValueError('CSV requires date,metric,value,geo,source columns or a Google Trends Month/Week export')
        reader=csv.DictReader(lines[header:]); headers=reader.fieldnames or []
        native=headers[0] in ('Month','Week','Date')
        if native and (len(headers)!=2 or '(Pakistan)' not in headers[1]):
            raise ValueError('Import a single-keyword Google Trends export for Pakistan')
        if not native and not {'date','metric','value','geo','source'}.issubset(headers):
            raise ValueError('CSV requires date,metric,value,geo,source columns')
        buckets={}; seen=set(); current=date.today().strftime('%Y-%m')
        for row in reader:
            if not any(row.values()): continue
            month=(row[headers[0]] if native else row['date'])[:7]
            date.fromisoformat(month+'-01')
            if month>=current: raise ValueError('History must contain completed months, not current or future months')
            metric='interest' if native else row['metric'].strip()
            if metric not in ('interest','units','revenue'): raise ValueError('Metric must be interest, units, or revenue')
            raw_value=row[headers[1]] if native else row['value']
            if str(raw_value).startswith('<'): raise ValueError('Censored values such as <1 need an explicit treatment before import')
            value=research.number(raw_value)
            if value is None or metric=='interest' and value>100: raise ValueError('Invalid history value')
            geo='PK' if native else row['geo'].strip().upper()
            source='Google Trends Pakistan CSV upload' if native else row['source'].strip()
            if geo!='PK' or not source: raise ValueError('History requires geo=PK and a source description')
            key=(month,metric)
            if not native and key in seen: raise ValueError('Duplicate month/metric in CSV')
            seen.add(key); buckets.setdefault(key,[]).append((value,source))
        if not buckets: raise ValueError('CSV has no observations')
        for (month,metric),values in buckets.items():
            parsed.append((pid,month,metric,sum(v[0] for v in values)/len(values),'PK',values[0][1],research.now()))
        with db() as c:
            for metric in {r[2] for r in parsed}: c.execute('DELETE FROM history WHERE product_id=? AND metric=?',(pid,metric))
            c.executemany('INSERT INTO history VALUES (?,?,?,?,?,?,?)',parsed)
        return jsonify(detail(pid))
    @app.get('/api/export')
    def export():
        with db() as c: ids=[r['id'] for r in c.execute('SELECT id FROM products')]
        output=io.StringIO(); fields=['product','ready','supplier','supplier_url','fee_source','fee_date','sale_price','landed_cost','expected_order_profit','margin_pct','monthly_profit','investment','budget_remaining']
        writer=csv.DictWriter(output,fieldnames=fields); writer.writeheader()
        def safe(value):
            return "'"+value if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')) else value
        for pid in ids:
            p=detail(pid); values=dict(p['economics'],product=p['name'],supplier=p['inputs']['supplier_name'],supplier_url=p['inputs']['supplier_url'],fee_source=p['inputs']['fee_source'],fee_date=p['inputs']['fee_date'],sale_price=p['inputs']['sale_price'])
            writer.writerow({k:safe(values[k]) for k in fields})
        return Response(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=pakistan-product-comparison.csv'})
    @app.get('/api/products/<int:pid>/evidence')
    def export_evidence(pid):
        return Response(json.dumps(detail(pid),indent=2),mimetype='application/json',headers={'Content-Disposition':f'attachment; filename=product-{pid}-evidence.json'})
    @app.get('/api/health')
    def health(): return jsonify(status='ok',app='pakistan-product-research')
    return app

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8790); parser.add_argument('--no-search',action='store_true'); args=parser.parse_args()
    search_process=None if args.no_search else research.start_search()
    try:
        from waitress import serve
        print(f'Pakistan Product Research: http://127.0.0.1:{args.port}',flush=True)
        serve(create_app(),host='127.0.0.1',port=args.port,threads=6)
    finally:
        if search_process: search_process.terminate()
