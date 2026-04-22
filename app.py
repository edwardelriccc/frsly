from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from functools import wraps
import sqlite3, hashlib, os, json, calendar
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'frsly-secret-2025')
DB = 'frsly.db'
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@frsly.com')

MODULES = {
    'ifrs16':     {'name': 'IFRS 16 Kira',        'price': 299, 'desc': 'Kira sozlesmesi, kullanim hakki varligi ve kira yukumlulugu'},
    'reeskont':   {'name': 'Reeskont',             'price': 199, 'desc': 'Alacak ve borc senetleri reeskont hesaplama'},
    'amortisman': {'name': 'Amortisman',           'price': 199, 'desc': 'IAS 16 dogrusal amortisman, hareket tablosu ve elden cikaris'},
    'izin':       {'name': 'Izin Karsiligi',       'price': 199, 'desc': 'IAS 19 calisan izin yukumlulugu ve hareket tablosu'},
    'etb':        {'name': 'ETB / IFRS Raporlama', 'price': 499, 'desc': 'Mizan yukle, IFRS finansal tablolar ve dipnotlar'},
}

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                company TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                module TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                UNIQUE(user_id, module),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                gl_code TEXT DEFAULT '',
                cost REAL NOT NULL,
                useful_life INTEGER NOT NULL,
                activation_date TEXT NOT NULL,
                residual_value REAL DEFAULT 0,
                disposed INTEGER DEFAULT 0,
                disposal_date TEXT,
                disposal_price REAL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS leases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                commencement_date TEXT NOT NULL,
                lease_term INTEGER NOT NULL,
                payment_amount REAL NOT NULL,
                payment_frequency TEXT DEFAULT 'monthly',
                discount_rate REAL NOT NULL,
                currency TEXT DEFAULT 'TRY',
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS reeskont_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                item_type TEXT NOT NULL,
                face_value REAL NOT NULL,
                issue_date TEXT NOT NULL,
                maturity_date TEXT NOT NULL,
                discount_rate REAL NOT NULL,
                currency TEXT DEFAULT 'TRY',
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS izin_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                employee_no INTEGER NOT NULL,
                daily_gross_salary REAL NOT NULL,
                unused_leave_days REAL NOT NULL,
                social_security_rate REAL DEFAULT 20.5,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        ''')
        for col in ['gl_code TEXT DEFAULT ""', 'disposed INTEGER DEFAULT 0', 'disposal_date TEXT', 'disposal_price REAL']:
            try:
                db.execute(f'ALTER TABLE assets ADD COLUMN {col}')
            except: pass

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def get_user_modules(user_id):
    with get_db() as db:
        user = db.execute('SELECT email FROM users WHERE id=?', (user_id,)).fetchone()
        if user and user['email'] == ADMIN_EMAIL:
            return list(MODULES.keys())
        rows = db.execute('SELECT module FROM subscriptions WHERE user_id=? AND active=1', (user_id,)).fetchall()
    return [r['module'] for r in rows]

def period_end(period_str):
    y, m = map(int, period_str.split('-'))
    return date(y, m, calendar.monthrange(y, m)[1])

def period_prev_year_end(period_str):
    y = int(period_str.split('-')[0])
    return date(y - 1, 12, 31)

def period_year_start(period_str):
    y = int(period_str.split('-')[0])
    return date(y, 1, 1)

# ── AUTH ──────────────────────────────────────────────────
@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        d = request.get_json()
        with get_db() as db:
            user = db.execute('SELECT * FROM users WHERE email=?', (d['email'],)).fetchone()
        if user and user['password'] == hash_pw(d['password']):
            session.update({'user_id': user['id'], 'email': user['email'], 'company': user['company'] or ''})
            return jsonify({'ok': True})
        return jsonify({'ok': False, 'msg': 'E-posta veya sifre hatali.'})
    return render_template('login.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        d = request.get_json()
        try:
            with get_db() as db:
                db.execute('INSERT INTO users (email,password,company) VALUES (?,?,?)',
                    (d['email'], hash_pw(d['password']), d.get('company','')))
                user = db.execute('SELECT id FROM users WHERE email=?', (d['email'],)).fetchone()
                db.execute('INSERT OR IGNORE INTO subscriptions (user_id,module) VALUES (?,?)', (user['id'],'amortisman'))
            return jsonify({'ok': True})
        except sqlite3.IntegrityError:
            return jsonify({'ok': False, 'msg': 'Bu e-posta zaten kayitli.'})
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    uid = session['user_id']
    return render_template('dashboard.html', email=session['email'], company=session.get('company',''),
        user_modules=get_user_modules(uid), all_modules=MODULES)

# ── AMORTISMAN ────────────────────────────────────────────
@app.route('/amortisman')
@login_required
def mod_amortisman():
    uid = session['user_id']
    if 'amortisman' not in get_user_modules(uid): return redirect(url_for('pricing'))
    with get_db() as db:
        assets = db.execute('SELECT * FROM assets WHERE user_id=? ORDER BY id', (uid,)).fetchall()
    return render_template('mod_amortisman.html', email=session['email'], company=session.get('company',''),
        user_modules=get_user_modules(uid), all_modules=MODULES, assets=[dict(a) for a in assets])

@app.route('/api/assets', methods=['GET'])
@login_required
def api_assets_get():
    uid = session['user_id']
    with get_db() as db:
        rows = db.execute('SELECT * FROM assets WHERE user_id=? ORDER BY id', (uid,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/assets', methods=['POST'])
@login_required
def api_assets_add():
    uid = session['user_id']
    d = request.get_json()
    with get_db() as db:
        cur = db.execute(
            'INSERT INTO assets (user_id,name,gl_code,cost,useful_life,activation_date,residual_value) VALUES (?,?,?,?,?,?,?)',
            (uid, d['name'], d.get('gl_code',''), d['cost'], d['useful_life'], d['activation_date'], d.get('residual_value',0)))
        row = db.execute('SELECT * FROM assets WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(dict(row))

@app.route('/api/assets/<int:aid>', methods=['PUT'])
@login_required
def api_assets_update(aid):
    uid = session['user_id']
    d = request.get_json()
    with get_db() as db:
        db.execute('UPDATE assets SET name=?,gl_code=?,cost=?,useful_life=?,activation_date=?,residual_value=? WHERE id=? AND user_id=?',
            (d['name'], d.get('gl_code',''), d['cost'], d['useful_life'], d['activation_date'], d.get('residual_value',0), aid, uid))
        row = db.execute('SELECT * FROM assets WHERE id=? AND user_id=?', (aid, uid)).fetchone()
    return jsonify(dict(row) if row else {})

@app.route('/api/assets/<int:aid>', methods=['DELETE'])
@login_required
def api_assets_delete(aid):
    uid = session['user_id']
    with get_db() as db:
        db.execute('DELETE FROM assets WHERE id=? AND user_id=?', (aid, uid))
    return jsonify({'ok': True})

@app.route('/api/assets/<int:aid>/dispose', methods=['POST'])
@login_required
def api_assets_dispose(aid):
    uid = session['user_id']
    d = request.get_json()
    with get_db() as db:
        db.execute('UPDATE assets SET disposed=1,disposal_date=?,disposal_price=? WHERE id=? AND user_id=?',
            (d['disposal_date'], float(d['disposal_price']), aid, uid))
        row = db.execute('SELECT * FROM assets WHERE id=? AND user_id=?', (aid, uid)).fetchone()
    if not row: return jsonify({'error': 'Bulunamadi'}), 404
    asset = dict(row)
    return jsonify({'ok': True, 'asset': asset, 'disposal': calc_disposal(asset, d['disposal_date'], float(d['disposal_price']))})

@app.route('/api/assets/<int:aid>/undispose', methods=['POST'])
@login_required
def api_assets_undispose(aid):
    uid = session['user_id']
    with get_db() as db:
        db.execute('UPDATE assets SET disposed=0,disposal_date=NULL,disposal_price=NULL WHERE id=? AND user_id=?', (aid, uid))
    return jsonify({'ok': True})

@app.route('/api/assets/movement')
@login_required
def api_assets_movement():
    uid = session['user_id']
    period = request.args.get('period', datetime.today().strftime('%Y-%m'))
    rd = period_end(period)
    od = period_prev_year_end(period)
    ys = period_year_start(period)
    with get_db() as db:
        assets = db.execute('SELECT * FROM assets WHERE user_id=? ORDER BY id', (uid,)).fetchall()
    rows = []
    for a in assets:
        asset = dict(a)
        act = datetime.strptime(asset['activation_date'], '%Y-%m-%d').date()
        is_disp = bool(asset['disposed'])
        disp_date = datetime.strptime(asset['disposal_date'], '%Y-%m-%d').date() if is_disp and asset['disposal_date'] else None
        cost = asset['cost']
        cost_open = cost if act <= od else 0
        addition = cost if ys <= act <= rd else 0
        disp_cost = cost if is_disp and disp_date and ys <= disp_date <= rd else 0
        cost_close = cost_open + addition - disp_cost
        dep_open = acc_dep(asset, od)
        dep_period = acc_dep(asset, rd) - acc_dep(asset, od)
        dep_disp = acc_dep(asset, disp_date) if is_disp and disp_date and ys <= disp_date <= rd else 0
        dep_close = dep_open + dep_period - dep_disp
        rows.append({
            'id': asset['id'], 'name': asset['name'], 'gl_code': asset['gl_code'] or '',
            'cost_open': round(cost_open,2), 'additions': round(addition,2),
            'disposals_cost': round(disp_cost,2), 'cost_close': round(cost_close,2),
            'dep_open': round(dep_open,2), 'dep_period': round(dep_period,2),
            'dep_disposals': round(dep_disp,2), 'dep_close': round(dep_close,2),
            'nbv_open': round(cost_open - dep_open,2), 'nbv_close': round(cost_close - dep_close,2),
            'disposed': is_disp, 'disposal_date': asset['disposal_date'],
        })
    keys = ['cost_open','additions','disposals_cost','cost_close','dep_open','dep_period','dep_disposals','dep_close','nbv_open','nbv_close']
    totals = {k: round(sum(r[k] for r in rows), 2) for k in keys}
    return jsonify({'rows': rows, 'totals': totals, 'period': period,
        'rd': rd.strftime('%d.%m.%Y'), 'od': od.strftime('%d.%m.%Y'), 'ys': ys.strftime('%d.%m.%Y')})

def acc_dep(asset, as_of):
    if not as_of: return 0
    if isinstance(as_of, str): as_of = datetime.strptime(as_of, '%Y-%m-%d').date()
    act = datetime.strptime(asset['activation_date'], '%Y-%m-%d').date()
    if act > as_of: return 0
    dep_amt = asset['cost'] - (asset['residual_value'] or 0)
    total_months = asset['useful_life'] * 12
    monthly = dep_amt / total_months
    months = min((as_of.year - act.year) * 12 + (as_of.month - act.month), total_months)
    return max(0, monthly * months)

def calc_disposal(asset, disposal_date, disposal_price):
    if isinstance(disposal_date, str): disposal_date = datetime.strptime(disposal_date, '%Y-%m-%d').date()
    ad = acc_dep(asset, disposal_date)
    carrying = asset['cost'] - ad
    gain_loss = disposal_price - carrying
    return {'cost': round(asset['cost'],2), 'acc_dep': round(ad,2),
            'carrying': round(carrying,2), 'sale_price': round(disposal_price,2),
            'gain_loss': round(gain_loss,2), 'is_gain': gain_loss >= 0}

# ── IFRS 16 ───────────────────────────────────────────────
@app.route('/ifrs16')
@login_required
def mod_ifrs16():
    uid = session['user_id']
    if 'ifrs16' not in get_user_modules(uid): return redirect(url_for('pricing'))
    with get_db() as db:
        leases = db.execute('SELECT * FROM leases WHERE user_id=? ORDER BY id', (uid,)).fetchall()
    return render_template('mod_ifrs16.html', email=session['email'], company=session.get('company',''),
        user_modules=get_user_modules(uid), all_modules=MODULES, leases=[dict(l) for l in leases])

@app.route('/api/leases', methods=['GET'])
@login_required
def api_leases_get():
    uid = session['user_id']
    with get_db() as db:
        rows = db.execute('SELECT * FROM leases WHERE user_id=? ORDER BY id', (uid,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/leases', methods=['POST'])
@login_required
def api_leases_add():
    uid = session['user_id']
    d = request.get_json()
    with get_db() as db:
        cur = db.execute('INSERT INTO leases (user_id,name,commencement_date,lease_term,payment_amount,payment_frequency,discount_rate,currency) VALUES (?,?,?,?,?,?,?,?)',
            (uid, d['name'], d['commencement_date'], d['lease_term'], d['payment_amount'], d.get('payment_frequency','monthly'), d['discount_rate'], d.get('currency','TRY')))
        row = db.execute('SELECT * FROM leases WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(dict(row))

@app.route('/api/leases/<int:lid>', methods=['DELETE'])
@login_required
def api_leases_delete(lid):
    uid = session['user_id']
    with get_db() as db:
        db.execute('DELETE FROM leases WHERE id=? AND user_id=?', (lid, uid))
    return jsonify({'ok': True})

@app.route('/api/leases/<int:lid>/schedule')
@login_required
def api_lease_schedule(lid):
    uid = session['user_id']
    with get_db() as db:
        row = db.execute('SELECT * FROM leases WHERE id=? AND user_id=?', (lid, uid)).fetchone()
    if not row: return jsonify({'error': 'Bulunamadi'}), 404
    return jsonify(calc_ifrs16(dict(row)))

def calc_ifrs16(lease):
    r = lease['discount_rate'] / 100
    n, pmt, freq = lease['lease_term'], lease['payment_amount'], lease['payment_frequency']
    if freq == 'monthly': tp, pr = n, r/12
    elif freq == 'quarterly': tp, pr = n*3, r/4
    else: tp, pr = n, r
    pv = pmt * (1 - (1+pr)**-tp) / pr if pr else pmt * tp
    start = datetime.strptime(lease['commencement_date'], '%Y-%m-%d').date()
    schedule, balance = [], round(pv, 2)
    for i in range(1, tp+1):
        interest = round(balance * pr, 2)
        principal = round(pmt - interest, 2)
        balance = round(max(0, balance - principal), 2)
        if freq == 'monthly': pd = (start + relativedelta(months=i)).strftime('%Y-%m-%d')
        elif freq == 'quarterly': pd = (start + relativedelta(months=i*3)).strftime('%Y-%m-%d')
        else: pd = (start + relativedelta(years=i)).strftime('%Y-%m-%d')
        schedule.append({'period': i, 'date': pd, 'payment': round(pmt,2), 'interest': interest, 'principal': principal, 'balance': balance})
    return {'pv': round(pv,2), 'total_payment': round(pmt*tp,2), 'total_interest': round(pmt*tp-pv,2),
            'rou_asset': round(pv,2), 'schedule': schedule, 'currency': lease['currency']}

# ── REESKONT ──────────────────────────────────────────────
@app.route('/reeskont')
@login_required
def mod_reeskont():
    uid = session['user_id']
    if 'reeskont' not in get_user_modules(uid): return redirect(url_for('pricing'))
    with get_db() as db:
        items = db.execute('SELECT * FROM reeskont_items WHERE user_id=? ORDER BY id', (uid,)).fetchall()
    return render_template('mod_reeskont.html', email=session['email'], company=session.get('company',''),
        user_modules=get_user_modules(uid), all_modules=MODULES, items=[dict(i) for i in items])

@app.route('/api/reeskont', methods=['GET'])
@login_required
def api_reeskont_get():
    uid = session['user_id']
    with get_db() as db:
        rows = db.execute('SELECT * FROM reeskont_items WHERE user_id=? ORDER BY id', (uid,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/reeskont', methods=['POST'])
@login_required
def api_reeskont_add():
    uid = session['user_id']
    d = request.get_json()
    with get_db() as db:
        cur = db.execute('INSERT INTO reeskont_items (user_id,name,item_type,face_value,issue_date,maturity_date,discount_rate,currency) VALUES (?,?,?,?,?,?,?,?)',
            (uid, d['name'], d['item_type'], d['face_value'], d['issue_date'], d['maturity_date'], d['discount_rate'], d.get('currency','TRY')))
        row = db.execute('SELECT * FROM reeskont_items WHERE id=?', (cur.lastrowid,)).fetchone()
    result = dict(row)
    result['calc'] = calc_reeskont(result)
    return jsonify(result)

@app.route('/api/reeskont/<int:rid>', methods=['DELETE'])
@login_required
def api_reeskont_delete(rid):
    uid = session['user_id']
    with get_db() as db:
        db.execute('DELETE FROM reeskont_items WHERE id=? AND user_id=?', (rid, uid))
    return jsonify({'ok': True})

def calc_reeskont(item):
    issue = datetime.strptime(item['issue_date'], '%Y-%m-%d').date()
    maturity = datetime.strptime(item['maturity_date'], '%Y-%m-%d').date()
    days = (maturity - issue).days
    if days <= 0: return {'pv': item['face_value'], 'discount': 0, 'days': 0}
    pv = item['face_value'] / (1 + (item['discount_rate']/100) * (days/365))
    return {'pv': round(pv,2), 'discount': round(item['face_value']-pv,2), 'days': days}

# ── IZIN ──────────────────────────────────────────────────
@app.route('/izin')
@login_required
def mod_izin():
    uid = session['user_id']
    if 'izin' not in get_user_modules(uid): return redirect(url_for('pricing'))
    with get_db() as db:
        items = db.execute('SELECT * FROM izin_items WHERE user_id=? ORDER BY employee_no', (uid,)).fetchall()
    return render_template('mod_izin.html', email=session['email'], company=session.get('company',''),
        user_modules=get_user_modules(uid), all_modules=MODULES, items=[dict(i) for i in items])

@app.route('/api/izin', methods=['GET'])
@login_required
def api_izin_get():
    uid = session['user_id']
    with get_db() as db:
        rows = db.execute('SELECT * FROM izin_items WHERE user_id=? ORDER BY employee_no', (uid,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/izin', methods=['POST'])
@login_required
def api_izin_add():
    uid = session['user_id']
    d = request.get_json()
    with get_db() as db:
        last = db.execute('SELECT MAX(employee_no) as m FROM izin_items WHERE user_id=?', (uid,)).fetchone()
        next_no = (last['m'] or 0) + 1
        cur = db.execute('INSERT INTO izin_items (user_id,employee_no,daily_gross_salary,unused_leave_days,social_security_rate) VALUES (?,?,?,?,?)',
            (uid, next_no, d['daily_gross_salary'], d['unused_leave_days'], d.get('social_security_rate',20.5)))
        row = db.execute('SELECT * FROM izin_items WHERE id=?', (cur.lastrowid,)).fetchone()
    result = dict(row)
    result['calc'] = calc_izin(result)
    return jsonify(result)

@app.route('/api/izin/<int:iid>', methods=['PUT'])
@login_required
def api_izin_update(iid):
    uid = session['user_id']
    d = request.get_json()
    with get_db() as db:
        db.execute('UPDATE izin_items SET daily_gross_salary=?,unused_leave_days=?,social_security_rate=? WHERE id=? AND user_id=?',
            (d['daily_gross_salary'], d['unused_leave_days'], d.get('social_security_rate',20.5), iid, uid))
        row = db.execute('SELECT * FROM izin_items WHERE id=? AND user_id=?', (iid, uid)).fetchone()
    result = dict(row)
    result['calc'] = calc_izin(result)
    return jsonify(result)

@app.route('/api/izin/<int:iid>', methods=['DELETE'])
@login_required
def api_izin_delete(iid):
    uid = session['user_id']
    with get_db() as db:
        db.execute('DELETE FROM izin_items WHERE id=? AND user_id=?', (iid, uid))
    return jsonify({'ok': True})

@app.route('/api/izin/movement')
@login_required
def api_izin_movement():
    uid = session['user_id']
    period = request.args.get('period', datetime.today().strftime('%Y-%m'))
    rd = period_end(period)
    od = period_prev_year_end(period)
    with get_db() as db:
        items = db.execute('SELECT * FROM izin_items WHERE user_id=? ORDER BY employee_no', (uid,)).fetchall()
    rows = []
    for item in items:
        item = dict(item)
        c = calc_izin(item)
        open_ratio = od.month / rd.month if rd.month else 0
        open_gross = c['gross_provision'] * open_ratio
        open_ss = open_gross * (item['social_security_rate'] / 100)
        open_total = round(open_gross + open_ss, 2)
        rows.append({
            'employee_no': item['employee_no'],
            'name': f"Calisan {item['employee_no']}",
            'daily_salary': item['daily_gross_salary'],
            'days': item['unused_leave_days'],
            'open_total': open_total,
            'period_charge': round(c['total_provision'] - open_total, 2),
            'close_total': round(c['total_provision'], 2),
            'close_gross': round(c['gross_provision'], 2),
            'close_ss': round(c['social_security'], 2),
        })
    totals = {k: round(sum(r[k] for r in rows), 2) for k in ['open_total','period_charge','close_total','close_gross','close_ss']}
    return jsonify({'rows': rows, 'totals': totals, 'period': period,
        'rd': rd.strftime('%d.%m.%Y'), 'od': od.strftime('%d.%m.%Y')})

def calc_izin(item):
    gross = item['daily_gross_salary'] * item['unused_leave_days']
    ss = gross * (item['social_security_rate'] / 100)
    return {'gross_provision': round(gross,2), 'social_security': round(ss,2), 'total_provision': round(gross+ss,2)}

# ── PRICING ───────────────────────────────────────────────
@app.route('/pricing')
@login_required
def pricing():
    uid = session['user_id']
    return render_template('pricing.html', email=session['email'], company=session.get('company',''),
        user_modules=get_user_modules(uid), all_modules=MODULES)

@app.route('/api/subscribe', methods=['POST'])
@login_required
def api_subscribe():
    uid = session['user_id']
    d = request.get_json()
    module = d.get('module')
    if module not in MODULES: return jsonify({'ok': False, 'msg': 'Gecersiz modul.'})
    with get_db() as db:
        db.execute('INSERT OR REPLACE INTO subscriptions (user_id,module,active) VALUES (?,?,1)', (uid, module))
    return jsonify({'ok': True})

init_db()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
