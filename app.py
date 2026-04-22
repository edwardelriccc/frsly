from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from functools import wraps
import sqlite3, hashlib, os, json
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'frsly-secret-2025')
DB = 'frsly.db'

MODULES = {
    'ifrs16':      {'name': 'IFRS 16 Kira', 'price': 299, 'desc': 'Kira sozlesmesi hesaplama, kullanim hakki varligi ve kira yukumlulugu'},
    'reeskont':    {'name': 'Reeskont', 'price': 199, 'desc': 'Alacak ve borc senetleri reeskont hesaplama'},
    'amortisman':  {'name': 'Amortisman', 'price': 199, 'desc': 'IAS 16 dogrusal amortisman ve net defter degeri'},
    'izin':        {'name': 'Izin Karsılıgı', 'price': 199, 'desc': 'IAS 19 calisan izin yukumlulugu hesaplama'},
    'etb':         {'name': 'ETB / IFRS Raporlama', 'price': 499, 'desc': 'Mizan yukle, IFRS finansal tablolar ve dipnotlar'},
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
                plan TEXT DEFAULT 'free',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                module TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, module),
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
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                cost REAL NOT NULL,
                useful_life INTEGER NOT NULL,
                activation_date TEXT NOT NULL,
                residual_value REAL DEFAULT 0,
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
                employee_name TEXT NOT NULL,
                daily_gross_salary REAL NOT NULL,
                unused_leave_days REAL NOT NULL,
                social_security_rate REAL DEFAULT 20.5,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        ''')

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def get_user_modules(user_id):
    with get_db() as db:
        rows = db.execute('SELECT module FROM subscriptions WHERE user_id=? AND active=1', (user_id,)).fetchall()
    return [r['module'] for r in rows]

# ─── AUTH ─────────────────────────────────────────────────
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('landing.html', modules=MODULES)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        data = request.get_json()
        with get_db() as db:
            user = db.execute('SELECT * FROM users WHERE email=?', (data['email'],)).fetchone()
        if user and user['password'] == hash_pw(data['password']):
            session['user_id'] = user['id']
            session['email'] = user['email']
            session['company'] = user['company'] or ''
            return jsonify({'ok': True})
        return jsonify({'ok': False, 'msg': 'E-posta veya sifre hatali.'})
    return render_template('login.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        data = request.get_json()
        try:
            with get_db() as db:
                db.execute('INSERT INTO users (email,password,company) VALUES (?,?,?)',
                    (data['email'], hash_pw(data['password']), data.get('company','')))
                user = db.execute('SELECT id FROM users WHERE email=?', (data['email'],)).fetchone()
                # Free trial: amortisman modulu ucretsiz
                db.execute('INSERT OR IGNORE INTO subscriptions (user_id, module) VALUES (?,?)', (user['id'], 'amortisman'))
            return jsonify({'ok': True})
        except sqlite3.IntegrityError:
            return jsonify({'ok': False, 'msg': 'Bu e-posta zaten kayitli.'})
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─── DASHBOARD ────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    uid = session['user_id']
    user_modules = get_user_modules(uid)
    return render_template('dashboard.html',
        email=session['email'],
        company=session.get('company',''),
        user_modules=user_modules,
        all_modules=MODULES)

# ─── MODULE: AMORTISMAN ───────────────────────────────────
@app.route('/amortisman')
@login_required
def mod_amortisman():
    uid = session['user_id']
    if 'amortisman' not in get_user_modules(uid):
        return redirect(url_for('pricing'))
    with get_db() as db:
        assets = db.execute('SELECT * FROM assets WHERE user_id=? ORDER BY id', (uid,)).fetchall()
    return render_template('mod_amortisman.html',
        email=session['email'], company=session.get('company',''),
        user_modules=get_user_modules(uid), all_modules=MODULES,
        assets=[dict(a) for a in assets])

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
        cur = db.execute('INSERT INTO assets (user_id,name,cost,useful_life,activation_date,residual_value) VALUES (?,?,?,?,?,?)',
            (uid, d['name'], d['cost'], d['useful_life'], d['activation_date'], d.get('residual_value',0)))
        row = db.execute('SELECT * FROM assets WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(dict(row))

@app.route('/api/assets/<int:aid>', methods=['PUT'])
@login_required
def api_assets_update(aid):
    uid = session['user_id']
    d = request.get_json()
    with get_db() as db:
        db.execute('UPDATE assets SET name=?,cost=?,useful_life=?,activation_date=?,residual_value=? WHERE id=? AND user_id=?',
            (d['name'], d['cost'], d['useful_life'], d['activation_date'], d.get('residual_value',0), aid, uid))
        row = db.execute('SELECT * FROM assets WHERE id=? AND user_id=?', (aid, uid)).fetchone()
    return jsonify(dict(row) if row else {})

@app.route('/api/assets/<int:aid>', methods=['DELETE'])
@login_required
def api_assets_delete(aid):
    uid = session['user_id']
    with get_db() as db:
        db.execute('DELETE FROM assets WHERE id=? AND user_id=?', (aid, uid))
    return jsonify({'ok': True})

# ─── MODULE: IFRS 16 ──────────────────────────────────────
@app.route('/ifrs16')
@login_required
def mod_ifrs16():
    uid = session['user_id']
    if 'ifrs16' not in get_user_modules(uid):
        return redirect(url_for('pricing'))
    with get_db() as db:
        leases = db.execute('SELECT * FROM leases WHERE user_id=? ORDER BY id', (uid,)).fetchall()
    return render_template('mod_ifrs16.html',
        email=session['email'], company=session.get('company',''),
        user_modules=get_user_modules(uid), all_modules=MODULES,
        leases=[dict(l) for l in leases])

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
        cur = db.execute(
            'INSERT INTO leases (user_id,name,commencement_date,lease_term,payment_amount,payment_frequency,discount_rate,currency) VALUES (?,?,?,?,?,?,?,?)',
            (uid, d['name'], d['commencement_date'], d['lease_term'], d['payment_amount'],
             d.get('payment_frequency','monthly'), d['discount_rate'], d.get('currency','TRY')))
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
    if not row:
        return jsonify({'error': 'Bulunamadi'}), 404
    schedule = calc_ifrs16(dict(row))
    return jsonify(schedule)

def calc_ifrs16(lease):
    from dateutil.relativedelta import relativedelta
    r = lease['discount_rate'] / 100
    n = lease['lease_term']
    pmt = lease['payment_amount']
    freq = lease['payment_frequency']

    periods_per_year = {'monthly': 12, 'quarterly': 4, 'annually': 1}.get(freq, 12)
    period_rate = r / periods_per_year
    total_periods = n if freq == 'annually' else n * (periods_per_year // 12 if freq == 'monthly' else 1)

    # Doğru hesap: aylık ödemeler için n=lease_term ay sayısı
    if freq == 'monthly':
        total_periods = n
        period_rate = r / 12
    elif freq == 'quarterly':
        total_periods = n * 3
        period_rate = r / 4
    else:
        total_periods = n
        period_rate = r

    # PV hesapla
    if period_rate == 0:
        pv = pmt * total_periods
    else:
        pv = pmt * (1 - (1 + period_rate) ** -total_periods) / period_rate

    # Amortisman tablosu
    start = datetime.strptime(lease['commencement_date'], '%Y-%m-%d').date()
    schedule = []
    balance = round(pv, 2)
    rou_annual = round(pv / (n if freq == 'annually' else n), 2)

    for i in range(1, total_periods + 1):
        interest = round(balance * period_rate, 2)
        principal = round(pmt - interest, 2)
        balance = round(balance - principal, 2)
        if balance < 0:
            balance = 0
        if freq == 'monthly':
            period_date = (start + relativedelta(months=i)).strftime('%Y-%m-%d')
        elif freq == 'quarterly':
            period_date = (start + relativedelta(months=i*3)).strftime('%Y-%m-%d')
        else:
            period_date = (start + relativedelta(years=i)).strftime('%Y-%m-%d')

        schedule.append({
            'period': i,
            'date': period_date,
            'payment': round(pmt, 2),
            'interest': interest,
            'principal': principal,
            'balance': balance,
        })

    return {
        'pv': round(pv, 2),
        'total_payment': round(pmt * total_periods, 2),
        'total_interest': round(pmt * total_periods - pv, 2),
        'rou_asset': round(pv, 2),
        'schedule': schedule,
        'currency': lease['currency'],
    }

# ─── MODULE: REESKONT ─────────────────────────────────────
@app.route('/reeskont')
@login_required
def mod_reeskont():
    uid = session['user_id']
    if 'reeskont' not in get_user_modules(uid):
        return redirect(url_for('pricing'))
    with get_db() as db:
        items = db.execute('SELECT * FROM reeskont_items WHERE user_id=? ORDER BY id', (uid,)).fetchall()
    return render_template('mod_reeskont.html',
        email=session['email'], company=session.get('company',''),
        user_modules=get_user_modules(uid), all_modules=MODULES,
        items=[dict(i) for i in items])

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
        cur = db.execute(
            'INSERT INTO reeskont_items (user_id,name,item_type,face_value,issue_date,maturity_date,discount_rate,currency) VALUES (?,?,?,?,?,?,?,?)',
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
    if days <= 0:
        return {'pv': item['face_value'], 'discount': 0, 'days': 0}
    r = item['discount_rate'] / 100
    # Dis iskonto (reeskont) formulu: PV = FV / (1 + r * t)
    t = days / 365
    pv = item['face_value'] / (1 + r * t)
    discount = item['face_value'] - pv
    return {
        'pv': round(pv, 2),
        'discount': round(discount, 2),
        'days': days,
        'face_value': item['face_value'],
    }

# ─── MODULE: IZIN ─────────────────────────────────────────
@app.route('/izin')
@login_required
def mod_izin():
    uid = session['user_id']
    if 'izin' not in get_user_modules(uid):
        return redirect(url_for('pricing'))
    with get_db() as db:
        items = db.execute('SELECT * FROM izin_items WHERE user_id=? ORDER BY id', (uid,)).fetchall()
    return render_template('mod_izin.html',
        email=session['email'], company=session.get('company',''),
        user_modules=get_user_modules(uid), all_modules=MODULES,
        items=[dict(i) for i in items])

@app.route('/api/izin', methods=['GET'])
@login_required
def api_izin_get():
    uid = session['user_id']
    with get_db() as db:
        rows = db.execute('SELECT * FROM izin_items WHERE user_id=? ORDER BY id', (uid,)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/izin', methods=['POST'])
@login_required
def api_izin_add():
    uid = session['user_id']
    d = request.get_json()
    with get_db() as db:
        cur = db.execute(
            'INSERT INTO izin_items (user_id,employee_name,daily_gross_salary,unused_leave_days,social_security_rate) VALUES (?,?,?,?,?)',
            (uid, d['employee_name'], d['daily_gross_salary'], d['unused_leave_days'], d.get('social_security_rate', 20.5)))
        row = db.execute('SELECT * FROM izin_items WHERE id=?', (cur.lastrowid,)).fetchone()
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

def calc_izin(item):
    daily = item['daily_gross_salary']
    days = item['unused_leave_days']
    ss_rate = item['social_security_rate'] / 100
    gross = daily * days
    ss = gross * ss_rate
    total = gross + ss
    return {
        'gross_provision': round(gross, 2),
        'social_security': round(ss, 2),
        'total_provision': round(total, 2),
        'daily_salary': round(daily, 2),
        'days': days,
    }

# ─── PRICING ──────────────────────────────────────────────
@app.route('/pricing')
@login_required
def pricing():
    uid = session['user_id']
    user_modules = get_user_modules(uid)
    return render_template('pricing.html',
        email=session['email'], company=session.get('company',''),
        user_modules=user_modules, all_modules=MODULES)

@app.route('/api/subscribe', methods=['POST'])
@login_required
def api_subscribe():
    uid = session['user_id']
    d = request.get_json()
    module = d.get('module')
    if module not in MODULES:
        return jsonify({'ok': False, 'msg': 'Gecersiz modul.'})
    with get_db() as db:
        db.execute('INSERT OR REPLACE INTO subscriptions (user_id, module, active) VALUES (?,?,1)', (uid, module))
    return jsonify({'ok': True, 'msg': MODULES[module]['name'] + ' aktif edildi.'})

init_db()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
