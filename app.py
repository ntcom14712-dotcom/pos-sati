from from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime
import qrcode
import os
import uuid
from io import BytesIO
import base64
from werkzeug.utils import secure_filename

app = Flask(__name__)

# ใช้ DATABASE_URL ถ้ามี (สำหรับ Render Postgres) ไม่งั้นใช้ SQLite
database_url = os.environ.get('DATABASE_URL', '')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///pos.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'pos-qr-secret-key-change-in-production')

db = SQLAlchemy(app)

# ==================== MODELS ====================

class Table(db.Model):
    __tablename__ = 'tables'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)          # เช่น "โต๊ะ 1"
    code = db.Column(db.String(20), unique=True, nullable=False)  # ใช้ใน URL
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    orders = db.relationship('Order', backref='table', lazy=True)


class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    image_url = db.Column(db.String(300), default='')

    items = db.relationship('MenuItem', backref='category', lazy=True)


class MenuItem(db.Model):
    __tablename__ = 'menu_items'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(300), default='')
    price = db.Column(db.Float, nullable=False)
    cost_price = db.Column(db.Float, default=0)  # ต้นทุน (admin only)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    is_available = db.Column(db.Boolean, default=True)
    image_url = db.Column(db.String(300), default='')
    sort_order = db.Column(db.Integer, default=0)
    stock_qty = db.Column(db.Integer, default=None)  # None = ไม่ติดตามสต็อก


class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    table_id = db.Column(db.Integer, db.ForeignKey('tables.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, preparing, ready, completed, cancelled
    total = db.Column(db.Float, default=0)
    note = db.Column(db.String(300), default='')
    is_paid = db.Column(db.Boolean, default=False)
    paid_at = db.Column(db.DateTime, nullable=True)
    payment_method = db.Column(db.String(20), default='')  # cash, transfer, card, other
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')


class OrderItem(db.Model):
    __tablename__ = 'order_items'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    menu_item_id = db.Column(db.Integer, db.ForeignKey('menu_items.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    unit_price = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(200), default='')

    menu_item = db.relationship('MenuItem')


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), default='staff')  # admin, cashier, kitchen, staff
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Shift(db.Model):
    __tablename__ = 'shifts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    opened_at = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)
    opening_cash = db.Column(db.Float, default=0)
    closing_cash = db.Column(db.Float, nullable=True)
    expected_cash = db.Column(db.Float, nullable=True)
    note = db.Column(db.String(300), default='')
    is_open = db.Column(db.Boolean, default=True)

    user = db.relationship('User')


class PrintStation(db.Model):
    """จุดปริ้น เช่น ครัว, บาร์น้ำ, แคชเชียร์"""
    __tablename__ = 'print_stations'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    station_type = db.Column(db.String(30), default='kitchen')  # kitchen, bar, cashier
    category_ids = db.Column(db.String(200), default='')  # "1,2,3"
    is_active = db.Column(db.Boolean, default=True)

    def get_category_ids(self):
        if not self.category_ids:
            return []
        return [int(x) for x in self.category_ids.split(',') if x.strip().isdigit()]

    def set_category_ids(self, ids):
        self.category_ids = ','.join(str(i) for i in ids)


class Setting(db.Model):
    """ตั้งค่าร้าน / หัวบิล"""
    __tablename__ = 'settings'
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text, default='')


def get_setting(key, default=''):
    s = Setting.query.get(key)
    return s.value if s else default


def set_setting(key, value):
    s = Setting.query.get(key)
    if s:
        s.value = value or ''
    else:
        s = Setting(key=key, value=value or '')
        db.session.add(s)
    db.session.commit()


# ==================== AUTH ====================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login', next=request.path))
            user = User.query.get(session['user_id'])
            if not user or (user.role not in roles and user.role != 'admin'):
                flash('คุณไม่มีสิทธิ์เข้าถึงหน้านี้', 'error')
                return redirect(url_for('admin_dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator


def get_current_user():
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None


def is_admin_user(user=None):
    u = user or get_current_user()
    return bool(u and u.role == 'admin')


def can_edit_bills(user=None):
    """admin เท่านั้นที่แก้ไข/ลบบิลได้"""
    return is_admin_user(user)


def require_admin_api():
    """ใช้ใน API: ถ้าไม่ใช่ admin คืน 403"""
    if not is_admin_user():
        return jsonify({'success': False, 'message': 'ไม่มีสิทธิ์ (เฉพาะ Admin)'}), 403
    return None


# สิทธิ์ตามบทบาท
# admin  = ทุกเมนู
# staff  = แดชบอร์ด, สั่งออเดอร์, จัดการโต๊ะ, เปิดปิดกะ, ดูออเดอร์/ชำระ/พิมพ์ (ห้ามแก้บิล)
# kitchen = หน้าครัว
STAFF_ALLOWED_PATHS = {
    '/admin', '/admin/pos', '/admin/tables', '/admin/shift', '/admin/orders',
    '/logout', '/kitchen',
}


# หมวดที่ติดตามสต็อก (อาหารทานเล่น มิกซ์เซอร์ เบียร์ เหล้า)
STOCK_CATEGORIES = {'อาหารทานเล่น', 'มิกซ์เซอร์', 'เบียร์', 'เหล้า'}


def is_stock_tracked(item):
    if not item or not item.category:
        return False
    return item.category.name in STOCK_CATEGORIES


def consolidate_order_items(order):
    """รวมรายการที่เมนูและหมายเหตุเดียวกันในบิล"""
    grouped = {}
    for oi in list(order.items):
        key = (oi.menu_item_id, (oi.note or '').strip())
        if key in grouped:
            grouped[key].quantity += oi.quantity
            db.session.delete(oi)
        else:
            grouped[key] = oi
    db.session.flush()
    order.total = sum(i.quantity * i.unit_price for i in order.items)


def add_or_merge_item(order, menu_item, qty, note=''):
    """เพิ่มรายการ หรือรวมจำนวนถ้าเมนูเดียวกันและหมายเหตุเดียวกัน"""
    note = (note or '').strip()
    existing = OrderItem.query.filter_by(
        order_id=order.id,
        menu_item_id=menu_item.id,
        note=note
    ).first()
    if existing:
        existing.quantity += qty
        return existing
    oi = OrderItem(
        order_id=order.id,
        menu_item_id=menu_item.id,
        quantity=qty,
        unit_price=menu_item.price,
        note=note
    )
    db.session.add(oi)
    return oi


def deduct_stock(order):
    """หักสต็อกตามรายการที่ขาย (เฉพาะหมวดที่ติดตาม)"""
    warnings = []
    for oi in order.items:
        item = oi.menu_item
        if item and is_stock_tracked(item) and item.stock_qty is not None:
            item.stock_qty = max(0, (item.stock_qty or 0) - oi.quantity)
            if item.stock_qty < 2:
                warnings.append({
                    'name': item.name,
                    'stock': item.stock_qty,
                    'category': item.category.name if item.category else ''
                })
    return warnings

def generate_qr_code(table_code: str) -> str:
    """สร้าง QR Code เป็น base64 data URL"""
    base = os.environ.get('BASE_URL', '').rstrip('/')
    if not base:
        try:
            base = request.host_url.rstrip('/')
        except RuntimeError:
            base = 'http://127.0.0.1:5000'
    url = f"{base}/order/{table_code}"
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return f"data:image/png;base64,{img_str}"


def calculate_order_total(order: Order) -> float:
    return sum(item.quantity * item.unit_price for item in order.items)


# ==================== AUTH ROUTES ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('admin_dashboard'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username, is_active=True).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['user_name'] = user.name
            session['user_role'] = user.role
            next_url = request.args.get('next') or url_for('admin_dashboard')
            return redirect(next_url)
        error = 'ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง'

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ==================== CUSTOMER ROUTES ====================

@app.route('/order/<table_code>')
def customer_order(table_code):
    table = Table.query.filter_by(code=table_code, is_active=True).first_or_404()
    categories = Category.query.order_by(Category.sort_order).all()
    items = MenuItem.query.filter_by(is_available=True).order_by(MenuItem.sort_order).all()
    return render_template('customer/order.html', table=table, categories=categories, items=items)


@app.route('/api/order', methods=['POST'])
def create_order():
    data = request.json
    table_code = data.get('table_code')
    items = data.get('items', [])
    note = data.get('note', '')

    table = Table.query.filter_by(code=table_code, is_active=True).first()
    if not table:
        return jsonify({'success': False, 'message': 'ไม่พบโต๊ะ'}), 404

    if not items:
        return jsonify({'success': False, 'message': 'กรุณาเลือกเมนู'}), 400

    # รวมเข้าบิลที่ยังไม่ชำระของโต๊ะนี้ (ถ้ามี)
    order = Order.query.filter(
        Order.table_id == table.id,
        Order.is_paid == False,
        Order.status != 'cancelled'
    ).order_by(Order.created_at.desc()).first()

    if order is None:
        order = Order(table_id=table.id, note=note, status='pending')
        db.session.add(order)
        db.session.flush()
    else:
        if note:
            order.note = (order.note + ' | ' + note) if order.note else note
        # ถ้าเสิร์ฟไปแล้วแต่ยังไม่ชำระ และมีรายการใหม่ ให้กลับไป pending เพื่อครัวเห็น
        if order.status in ('completed', 'ready'):
            order.status = 'pending'

    total_added = 0
    for item_data in items:
        menu_item = MenuItem.query.get(item_data['id'])
        if not menu_item or not menu_item.is_available:
            continue
        qty = int(item_data.get('quantity', 1))
        add_or_merge_item(order, menu_item, qty, item_data.get('note', ''))
        total_added += qty * menu_item.price

    db.session.flush()
    order.total = sum(i.quantity * i.unit_price for i in order.items)
    db.session.commit()

    return jsonify({
        'success': True,
        'order_id': order.id,
        'total': order.total,
        'merged': total_added > 0
    })


# ==================== ADMIN / KITCHEN / CASHIER ====================

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('login'))


@app.route('/admin')
@login_required
def admin_dashboard():
    pending = Order.query.filter(Order.status.in_(['pending', 'preparing', 'ready'])).count()
    today_orders = Order.query.filter(
        db.func.date(Order.created_at) == datetime.utcnow().date()
    ).count()
    today_revenue = db.session.query(db.func.sum(Order.total)).filter(
        db.func.date(Order.created_at) == datetime.utcnow().date(),
        Order.is_paid == True
    ).scalar() or 0
    tables_count = Table.query.filter_by(is_active=True).count()
    return render_template('admin/dashboard.html',
                           pending=pending,
                           today_orders=today_orders,
                           today_revenue=today_revenue,
                           tables_count=tables_count,
                           current_user=get_current_user())


@app.route('/admin/menu')
@login_required
@role_required('admin')
def admin_menu():
    categories = Category.query.order_by(Category.sort_order).all()
    items = MenuItem.query.order_by(MenuItem.sort_order).all()
    return render_template('admin/menu.html', categories=categories, items=items,
                           current_user=get_current_user())


@app.route('/admin/tables')
@login_required
def admin_tables():
    tables = Table.query.order_by(Table.name).all()
    qr_codes = {t.code: generate_qr_code(t.code) for t in tables}
    return render_template('admin/tables.html', tables=tables, qr_codes=qr_codes,
                           current_user=get_current_user())


@app.route('/admin/orders')
@login_required
def admin_orders():
    return render_template('admin/orders.html', current_user=get_current_user())


@app.route('/admin/pos')
@login_required
def admin_pos():
    """หน้าสั่งออเดอร์โดยพนักงาน"""
    tables = Table.query.filter_by(is_active=True).order_by(Table.name).all()
    categories = Category.query.order_by(Category.sort_order).all()
    items = MenuItem.query.filter_by(is_available=True).order_by(MenuItem.sort_order).all()
    return render_template('admin/pos.html',
                           tables=tables,
                           categories=categories,
                           items=items,
                           current_user=get_current_user())


@app.route('/api/staff-order', methods=['POST'])
@login_required
def staff_order():
    data = request.json
    table_id = data.get('table_id')
    items = data.get('items', [])
    note = data.get('note', '')
    table = Table.query.filter_by(id=table_id, is_active=True).first()
    if not table:
        return jsonify({'success': False, 'message': 'ไม่พบโต๊ะ'}), 404
    if not items:
        return jsonify({'success': False, 'message': 'กรุณาเลือกเมนู'}), 400

    # รวมเข้าบิลที่ยังไม่ชำระของโต๊ะนี้ (ถ้ามี)
    order = Order.query.filter(
        Order.table_id == table.id,
        Order.is_paid == False,
        Order.status != 'cancelled'
    ).order_by(Order.created_at.desc()).first()

    if order is None:
        order = Order(table_id=table.id, note=note, status='pending')
        db.session.add(order)
        db.session.flush()
    else:
        if note:
            order.note = (order.note + ' | ' + note) if order.note else note
        if order.status in ('completed', 'ready'):
            order.status = 'pending'

    total_added = 0
    for item_data in items:
        menu_item = MenuItem.query.get(item_data['id'])
        if not menu_item or not menu_item.is_available:
            continue
        qty = int(item_data.get('quantity', 1))
        add_or_merge_item(order, menu_item, qty, item_data.get('note', ''))
        total_added += qty * menu_item.price

    db.session.flush()
    order.total = sum(i.quantity * i.unit_price for i in order.items)
    db.session.commit()
    return jsonify({'success': True, 'order_id': order.id, 'total': order.total})


@app.route('/kitchen')
@login_required
def kitchen():
    return render_template('kitchen.html', current_user=get_current_user())


# ==================== SHIFT ====================

@app.route('/admin/shift')
@login_required
def admin_shift():
    current_shift = Shift.query.filter_by(is_open=True).first()
    recent_shifts = Shift.query.order_by(Shift.opened_at.desc()).limit(20).all()
    return render_template('admin/shift.html',
                           current_shift=current_shift,
                           recent_shifts=recent_shifts,
                           current_user=get_current_user())


@app.route('/api/shift/open', methods=['POST'])
@login_required
def open_shift():
    existing = Shift.query.filter_by(is_open=True).first()
    if existing:
        return jsonify({'success': False, 'message': 'มีกะที่เปิดอยู่แล้ว'}), 400
    data = request.json or {}
    shift = Shift(
        user_id=session['user_id'],
        opening_cash=float(data.get('opening_cash', 0)),
        note=data.get('note', '')
    )
    db.session.add(shift)
    db.session.commit()
    return jsonify({'success': True, 'id': shift.id})


@app.route('/api/shift/close', methods=['POST'])
@login_required
def close_shift():
    shift = Shift.query.filter_by(is_open=True).first()
    if not shift:
        return jsonify({'success': False, 'message': 'ไม่มีกะที่เปิดอยู่'}), 400
    data = request.json or {}
    # คำนวณยอดขายระหว่างกะ
    revenue = db.session.query(db.func.sum(Order.total)).filter(
        Order.is_paid == True,
        Order.paid_at >= shift.opened_at
    ).scalar() or 0
    shift.closed_at = datetime.utcnow()
    shift.closing_cash = float(data.get('closing_cash', 0))
    shift.expected_cash = shift.opening_cash + revenue
    shift.note = (shift.note or '') + (' | ' + data.get('note', '') if data.get('note') else '')
    shift.is_open = False
    db.session.commit()
    return jsonify({
        'success': True,
        'revenue': revenue,
        'expected_cash': shift.expected_cash,
        'closing_cash': shift.closing_cash
    })


# ==================== REPORTS ====================

@app.route('/admin/reports')
@login_required
@role_required('admin')
def admin_reports():
    return render_template('admin/reports.html', current_user=get_current_user())


@app.route('/admin/settings')
@login_required
@role_required('admin')
def admin_settings():
    return render_template('admin/settings.html', current_user=get_current_user())


@app.route('/api/reports')
@login_required
def api_reports():
    period = request.args.get('period', 'today')  # today, week, month, custom
    from datetime import timedelta, date

    today = datetime.utcnow().date()
    if period == 'today':
        start = datetime.combine(today, datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
        label = 'วันนี้'
    elif period == 'week':
        start = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
        label = 'สัปดาห์นี้'
    elif period == 'month':
        start = datetime.combine(today.replace(day=1), datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
        label = 'เดือนนี้'
    elif period == 'last7':
        start = datetime.combine(today - timedelta(days=6), datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
        label = '7 วันล่าสุด'
    elif period == 'last30':
        start = datetime.combine(today - timedelta(days=29), datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
        label = '30 วันล่าสุด'
    else:
        start = datetime.combine(today, datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
        label = 'วันนี้'

    # ออเดอร์ที่ชำระแล้วในช่วงเวลา
    orders = Order.query.filter(
        Order.is_paid == True,
        Order.paid_at >= start,
        Order.paid_at <= end
    ).all()

    total_revenue = sum(o.total for o in orders)
    total_orders = len(orders)
    avg_order = total_revenue / total_orders if total_orders else 0

    # สินค้าขายดี + ต้นทุน + แยกตามหมวด
    item_stats = {}
    cat_stats = {}
    total_cost = 0
    for o in orders:
        for oi in o.items:
            name = oi.menu_item.name if oi.menu_item else 'ไม่ทราบ'
            cost = (oi.menu_item.cost_price if oi.menu_item and oi.menu_item.cost_price else 0) or 0
            cat_name = 'ไม่ระบุ'
            if oi.menu_item and oi.menu_item.category:
                cat_name = oi.menu_item.category.name
            if name not in item_stats:
                item_stats[name] = {'qty': 0, 'revenue': 0, 'cost': 0, 'category': cat_name}
            item_stats[name]['qty'] += oi.quantity
            item_stats[name]['revenue'] += oi.quantity * oi.unit_price
            item_stats[name]['cost'] += oi.quantity * cost
            total_cost += oi.quantity * cost
            if cat_name not in cat_stats:
                cat_stats[cat_name] = {'qty': 0, 'revenue': 0, 'cost': 0, 'items': {}}
            cat_stats[cat_name]['qty'] += oi.quantity
            cat_stats[cat_name]['revenue'] += oi.quantity * oi.unit_price
            cat_stats[cat_name]['cost'] += oi.quantity * cost
            if name not in cat_stats[cat_name]['items']:
                cat_stats[cat_name]['items'][name] = {'qty': 0, 'revenue': 0}
            cat_stats[cat_name]['items'][name]['qty'] += oi.quantity
            cat_stats[cat_name]['items'][name]['revenue'] += oi.quantity * oi.unit_price

    total_profit = total_revenue - total_cost
    profit_margin = (total_profit / total_revenue * 100) if total_revenue else 0

    top_items = sorted(
        [{'name': k, 'qty': v['qty'], 'revenue': v['revenue'],
          'cost': v['cost'], 'profit': v['revenue'] - v['cost'],
          'category': v.get('category', '')}
         for k, v in item_stats.items()],
        key=lambda x: x['qty'], reverse=True
    )[:30]

    by_category = []
    for cat, v in sorted(cat_stats.items(), key=lambda x: -x[1]['revenue']):
        items_ranked = sorted(
            [{'name': n, 'qty': d['qty'], 'revenue': d['revenue']} for n, d in v['items'].items()],
            key=lambda x: -x['qty']
        )
        by_category.append({
            'category': cat,
            'qty': v['qty'],
            'revenue': v['revenue'],
            'cost': v['cost'],
            'profit': v['revenue'] - v['cost'],
            'items': items_ranked,
        })

    # รายได้รายวัน (สำหรับกราฟ)
    daily = {}
    for o in orders:
        d = o.paid_at.strftime('%Y-%m-%d') if o.paid_at else o.created_at.strftime('%Y-%m-%d')
        daily[d] = daily.get(d, 0) + o.total
    daily_list = [{'date': k, 'revenue': v} for k, v in sorted(daily.items())]

    # แยกตามประเภทการชำระเงิน
    payment_stats = {}
    method_labels = {'cash': 'เงินสด', 'transfer': 'โอนเงิน', 'card': 'บัตร', 'other': 'อื่นๆ', '': 'ไม่ระบุ'}
    for o in orders:
        m = o.payment_method or ''
        label = method_labels.get(m, m or 'ไม่ระบุ')
        if label not in payment_stats:
            payment_stats[label] = {'count': 0, 'revenue': 0, 'method': m}
        payment_stats[label]['count'] += 1
        payment_stats[label]['revenue'] += o.total
    by_payment = sorted(
        [{'method': k, 'count': v['count'], 'revenue': v['revenue']} for k, v in payment_stats.items()],
        key=lambda x: -x['revenue']
    )

    return jsonify({
        'label': label,
        'total_revenue': total_revenue,
        'total_cost': total_cost,
        'total_profit': total_profit,
        'profit_margin': profit_margin,
        'total_orders': total_orders,
        'avg_order': avg_order,
        'top_items': top_items,
        'by_category': by_category,
        'daily': daily_list,
        'by_payment': by_payment
    })




# ==================== ORDER EDIT ====================

@app.route('/api/orders/<int:order_id>')
@login_required
def get_order(order_id):
    o = Order.query.get_or_404(order_id)
    consolidate_order_items(o)
    db.session.commit()
    return jsonify({
        'id': o.id,
        'table_name': o.table.name,
        'table_id': o.table_id,
        'status': o.status,
        'total': o.total,
        'note': o.note,
        'is_paid': o.is_paid,
        'items': [{
            'id': i.id,
            'menu_item_id': i.menu_item_id,
            'name': i.menu_item.name if i.menu_item else '?',
            'quantity': i.quantity,
            'unit_price': i.unit_price,
            'note': i.note
        } for i in o.items]
    })


@app.route('/api/orders/<int:order_id>/items', methods=['POST'])
@login_required
def add_order_item(order_id):
    denied = require_admin_api()
    if denied:
        return denied
    order = Order.query.get_or_404(order_id)
    data = request.json
    menu_item = MenuItem.query.get(data.get('menu_item_id'))
    if not menu_item:
        return jsonify({'success': False, 'message': 'ไม่พบเมนู'}), 404
    qty = int(data.get('quantity', 1))
    oi = add_or_merge_item(order, menu_item, qty, data.get('note', ''))
    db.session.flush()
    order.total = sum(i.quantity * i.unit_price for i in order.items)
    db.session.commit()
    return jsonify({'success': True, 'item_id': oi.id, 'total': order.total})


@app.route('/api/orders/<int:order_id>/items/<int:item_id>', methods=['PUT'])
@login_required
def update_order_item(order_id, item_id):
    denied = require_admin_api()
    if denied:
        return denied
    order = Order.query.get_or_404(order_id)
    oi = OrderItem.query.filter_by(id=item_id, order_id=order_id).first_or_404()
    data = request.json
    if 'quantity' in data:
        qty = int(data['quantity'])
        if qty <= 0:
            db.session.delete(oi)
        else:
            oi.quantity = qty
    if 'note' in data:
        oi.note = data['note']
    db.session.flush()
    order.total = sum(i.quantity * i.unit_price for i in order.items)
    db.session.commit()
    return jsonify({'success': True, 'total': order.total})


@app.route('/api/orders/<int:order_id>/items/<int:item_id>', methods=['DELETE'])
@login_required
def delete_order_item(order_id, item_id):
    denied = require_admin_api()
    if denied:
        return denied
    order = Order.query.get_or_404(order_id)
    oi = OrderItem.query.filter_by(id=item_id, order_id=order_id).first_or_404()
    db.session.delete(oi)
    db.session.flush()
    order.total = sum(i.quantity * i.unit_price for i in order.items)
    db.session.commit()
    return jsonify({'success': True, 'total': order.total})


@app.route('/api/orders/<int:order_id>/note', methods=['PUT'])
@login_required
def update_order_note(order_id):
    denied = require_admin_api()
    if denied:
        return denied
    order = Order.query.get_or_404(order_id)
    data = request.json
    order.note = data.get('note', '')
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/menu/list')
@login_required
def menu_list():
    items = MenuItem.query.filter_by(is_available=True).order_by(MenuItem.sort_order).all()
    return jsonify([{
        'id': i.id, 'name': i.name, 'price': i.price,
        'category_id': i.category_id,
        'image_url': i.image_url or '',
        'description': i.description or '',
    } for i in items])


# ==================== PRINT STATIONS ====================

@app.route('/admin/printers')
@login_required
@role_required('admin')
def admin_printers():
    stations = PrintStation.query.order_by(PrintStation.id).all()
    categories = Category.query.order_by(Category.sort_order).all()
    return render_template('admin/printers.html',
                           stations=stations,
                           categories=categories,
                           current_user=get_current_user())


@app.route('/api/printers', methods=['POST'])
@login_required
def add_printer():
    data = request.json
    s = PrintStation(
        name=data['name'],
        station_type=data.get('station_type', 'kitchen'),
        category_ids=','.join(str(i) for i in data.get('category_ids', [])),
        is_active=True
    )
    db.session.add(s)
    db.session.commit()
    return jsonify({'success': True, 'id': s.id})


@app.route('/api/printers/<int:sid>', methods=['PUT'])
@login_required
def update_printer(sid):
    s = PrintStation.query.get_or_404(sid)
    data = request.json
    s.name = data.get('name', s.name)
    s.station_type = data.get('station_type', s.station_type)
    if 'category_ids' in data:
        s.category_ids = ','.join(str(i) for i in data['category_ids'])
    if 'is_active' in data:
        s.is_active = data['is_active']
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/printers/<int:sid>', methods=['DELETE'])
@login_required
def delete_printer(sid):
    s = PrintStation.query.get_or_404(sid)
    db.session.delete(s)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/orders/<int:order_id>/tickets')
@login_required
def order_tickets(order_id):
    """แยกใบสั่งตามสถานีปริ้น + ใบเสร็จมียอดรวมเสมอ"""
    order = Order.query.get_or_404(order_id)
    stations = PrintStation.query.filter_by(is_active=True).all()
    shop = {
        'name': get_setting('shop_name', 'สติๆ'),
        'address': get_setting('shop_address', ''),
        'phone': get_setting('shop_phone', ''),
        'tax_id': get_setting('shop_tax_id', ''),
        'footer': get_setting('receipt_footer', 'ขอบคุณที่ใช้บริการ'),
    }
    all_items = [{
        'name': oi.menu_item.name if oi.menu_item else '?',
        'quantity': oi.quantity,
        'note': oi.note,
        'unit_price': oi.unit_price
    } for oi in order.items]

    tickets = []
    has_cashier = False
    for st in stations:
        cat_ids = st.get_category_ids()
        if st.station_type == 'cashier':
            items = all_items
            has_cashier = True
        else:
            items = []
            for oi in order.items:
                item_cat = oi.menu_item.category_id if oi.menu_item else None
                if cat_ids and item_cat in cat_ids:
                    items.append({
                        'name': oi.menu_item.name if oi.menu_item else '?',
                        'quantity': oi.quantity,
                        'note': oi.note,
                        'unit_price': oi.unit_price
                    })
        if not items:
            continue
        is_receipt = st.station_type == 'cashier'
        tickets.append({
            'station_id': st.id,
            'station_name': st.name,
            'station_type': st.station_type,
            'table_name': order.table.name,
            'order_id': order.id,
            'created_at': order.created_at.strftime('%d/%m/%Y %H:%M'),
            'note': order.note,
            'total': float(order.total or 0) if is_receipt else None,
            'is_paid': order.is_paid,
            'items': items,
            'shop': shop,
        })

    # ถ้าไม่มีสถานีแคชเชียร์ สร้างใบเสร็จเต็มมียอดรวม
    if not has_cashier and all_items:
        tickets.append({
            'station_id': 0,
            'station_name': 'ใบเสร็จ',
            'station_type': 'cashier',
            'table_name': order.table.name,
            'order_id': order.id,
            'created_at': order.created_at.strftime('%d/%m/%Y %H:%M'),
            'note': order.note,
            'total': float(order.total or 0),
            'is_paid': order.is_paid,
            'items': all_items,
            'shop': shop,
        })
    return jsonify(tickets)


@app.route('/api/orders/<int:order_id>/receipt')
@login_required
def order_receipt(order_id):
    """ใบเสร็จเต็มมียอดรวมเสมอ (ดู/พิมพ์ย้อนหลัง)"""
    order = Order.query.get_or_404(order_id)
    shop = {
        'name': get_setting('shop_name', 'สติๆ'),
        'address': get_setting('shop_address', ''),
        'phone': get_setting('shop_phone', ''),
        'tax_id': get_setting('shop_tax_id', ''),
        'footer': get_setting('receipt_footer', 'ขอบคุณที่ใช้บริการ'),
    }
    items = [{
        'name': oi.menu_item.name if oi.menu_item else '?',
        'quantity': oi.quantity,
        'note': oi.note,
        'unit_price': oi.unit_price
    } for oi in order.items]
    return jsonify({
        'order_id': order.id,
        'table_name': order.table.name,
        'status': order.status,
        'is_paid': order.is_paid,
        'created_at': order.created_at.strftime('%d/%m/%Y %H:%M'),
        'note': order.note or '',
        'total': float(order.total or 0),
        'items': items,
        'shop': shop,
    })


@app.route('/api/settings', methods=['GET'])
@login_required
def get_settings():
    keys = ['shop_name', 'shop_address', 'shop_phone', 'shop_tax_id', 'receipt_footer']
    return jsonify({k: get_setting(k, '') for k in keys})


@app.route('/api/settings', methods=['POST'])
@login_required
def save_settings():
    denied = require_admin_api()
    if denied:
        return denied
    data = request.json or {}
    for key in ['shop_name', 'shop_address', 'shop_phone', 'shop_tax_id', 'receipt_footer']:
        if key in data:
            set_setting(key, data[key])
    return jsonify({'success': True})


@app.route('/station/<int:station_id>')
@login_required
def station_view(station_id):
    """หน้าจอสถานี (ครัว/บาร์) แสดงเฉพาะออเดอร์ที่เกี่ยวข้อง"""
    station = PrintStation.query.get_or_404(station_id)
    return render_template('station.html', station=station, current_user=get_current_user())


@app.route('/api/station/<int:station_id>/orders')
@login_required
def station_orders(station_id):
    station = PrintStation.query.get_or_404(station_id)
    cat_ids = station.get_category_ids()
    orders = Order.query.filter(
        Order.status.in_(['pending', 'preparing', 'ready'])
    ).order_by(Order.created_at.desc()).limit(50).all()
    result = []
    for o in orders:
        items = []
        for oi in o.items:
            item_cat = oi.menu_item.category_id if oi.menu_item else None
            if not cat_ids or (item_cat in cat_ids):
                items.append({
                    'name': oi.menu_item.name if oi.menu_item else '?',
                    'quantity': oi.quantity,
                    'note': oi.note
                })
        if items:
            result.append({
                'id': o.id,
                'table_name': o.table.name,
                'status': o.status,
                'created_at': o.created_at.strftime('%d/%m/%Y %H:%M'),
                'note': o.note,
                'items': items
            })
    return jsonify(result)


# ==================== API ====================

@app.route('/api/tables/status')
@login_required
def api_tables_status():
    """สถานะโต๊ะตามการชำระเงิน: ว่าง (ขาว) หรือมีบิลค้างชำระ (ส้มแดง)
    ไม่ขึ้นกับสถานะครัว — เสิร์ฟแล้วแต่ยังไม่ชำระ โต๊ะยังค้าง"""
    tables = Table.query.filter_by(is_active=True).order_by(Table.name).all()
    result = []
    for t in tables:
        # บิลที่ยังไม่ชำระ (รวมที่เสิร์ฟแล้วแต่ยังไม่คิดเงิน)
        unpaid = Order.query.filter(
            Order.table_id == t.id,
            Order.is_paid == False,
            Order.status != 'cancelled'
        ).order_by(Order.created_at.desc()).all()
        orders_data = []
        for o in unpaid:
            orders_data.append({
                'id': o.id,
                'status': o.status,
                'total': float(o.total or 0),
                'is_paid': o.is_paid,
                'note': o.note or '',
                'created_at': o.created_at.strftime('%d/%m/%Y %H:%M'),
                'items_count': len(o.items),
                'items': [{
                    'name': i.menu_item.name if i.menu_item else '?',
                    'quantity': i.quantity,
                    'note': i.note or '',
                    'unit_price': i.unit_price,
                } for i in o.items],
            })
        result.append({
            'id': t.id,
            'name': t.name,
            'code': t.code,
            'has_order': len(unpaid) > 0,
            'orders': orders_data,
            'order_count': len(unpaid),
            'total_pending': sum(o.total or 0 for o in unpaid),
        })
    return jsonify(result)


@app.route('/api/orders')
@login_required
def api_orders():
    """ดึงออเดอร์ (active หรือ history ตาม query)"""
    status = request.args.get('status')
    history = request.args.get('history')  # 1 = ดูบิลย้อนหลัง
    query = Order.query.order_by(Order.created_at.desc())
    if status:
        query = query.filter_by(status=status)
    elif history == '1':
        query = query.filter(Order.status.in_(['completed', 'cancelled']))
    else:
        # แสดงเฉพาะที่ยังไม่เสร็จ
        query = query.filter(Order.status.in_(['pending', 'preparing', 'ready']))

    limit = 100 if history == '1' else 50
    orders = query.limit(limit).all()
    result = []
    for o in orders:
        result.append({
            'id': o.id,
            'table_name': o.table.name,
            'status': o.status,
            'total': o.total,
            'note': o.note,
            'is_paid': o.is_paid,
            'created_at': o.created_at.strftime('%d/%m/%Y %H:%M'),
            'items': [{
                'name': i.menu_item.name if i.menu_item else '?',
                'quantity': i.quantity,
                'note': i.note,
                'unit_price': i.unit_price
            } for i in o.items]
        })
    return jsonify(result)


@app.route('/api/orders/<int:order_id>', methods=['DELETE'])
@login_required
def delete_order(order_id):
    """ลบบิล (ออเดอร์) ทั้งใบ — เฉพาะ admin"""
    denied = require_admin_api()
    if denied:
        return denied
    order = Order.query.get_or_404(order_id)
    db.session.delete(order)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/orders/<int:order_id>/status', methods=['POST'])
@login_required
def update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    data = request.json
    new_status = data.get('status')
    if new_status in ['pending', 'preparing', 'ready', 'completed', 'cancelled']:
        order.status = new_status
        order.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'สถานะไม่ถูกต้อง'}), 400


@app.route('/api/orders/<int:order_id>/pay', methods=['POST'])
@login_required
def mark_paid(order_id):
    order = Order.query.get_or_404(order_id)
    consolidate_order_items(order)
    if order.is_paid:
        return jsonify({'success': False, 'message': 'ชำระแล้ว'}), 400
    data = request.json or {}
    method = data.get('payment_method', 'cash')
    if method not in ('cash', 'transfer', 'card', 'other'):
        method = 'cash'
    order.is_paid = True
    order.paid_at = datetime.utcnow()
    order.payment_method = method
    if order.status != 'completed':
        order.status = 'completed'
    warnings = deduct_stock(order)
    db.session.commit()
    return jsonify({'success': True, 'low_stock': warnings})


@app.route('/api/stock/low')
@login_required
def low_stock():
    """รายการสต็อกเหลือน้อยกว่า 2 (เฉพาะหมวดที่ติดตาม)"""
    items = MenuItem.query.filter(MenuItem.stock_qty.isnot(None), MenuItem.stock_qty < 2).all()
    result = []
    for it in items:
        if is_stock_tracked(it):
            result.append({
                'id': it.id,
                'name': it.name,
                'stock': it.stock_qty or 0,
                'category': it.category.name if it.category else '',
            })
    return jsonify(result)


@app.route('/admin/stock')
@login_required
@role_required('admin')
def admin_stock():
    """หน้าจัดการสต็อก - แสดงเฉพาะหมวดที่ติดตาม"""
    items = MenuItem.query.join(Category).filter(
        Category.name.in_(STOCK_CATEGORIES)
    ).order_by(Category.sort_order, MenuItem.name).all()
    # ถ้าไม่มี join ได้ ให้ filter ด้วย is_stock_tracked
    tracked = [it for it in MenuItem.query.all() if is_stock_tracked(it)]
    tracked.sort(key=lambda x: ((x.category.name if x.category else ''), x.name))
    return render_template('admin/stock.html', items=tracked, current_user=get_current_user())


@app.route('/api/stock/<int:item_id>', methods=['PUT'])
@login_required
def update_stock(item_id):
    denied = require_admin_api()
    if denied:
        return denied
    item = MenuItem.query.get_or_404(item_id)
    data = request.json or {}
    if 'stock_qty' in data:
        val = data['stock_qty']
        item.stock_qty = int(val) if val is not None and str(val) != '' else None
    db.session.commit()
    return jsonify({'success': True, 'stock_qty': item.stock_qty})


# ---- Menu Management API ----


@app.route('/api/upload', methods=['POST'])
@login_required
def upload_image():
    """อัปโหลดรูปเมนู"""
    denied = require_admin_api()
    if denied:
        return denied
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'ไม่พบไฟล์'}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({'success': False, 'message': 'ไม่ได้เลือกไฟล์'}), 400
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
        return jsonify({'success': False, 'message': 'รองรับเฉพาะ jpg png gif webp'}), 400
    upload_dir = os.path.join(app.root_path, 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex[:12]}.{ext}"
    f.save(os.path.join(upload_dir, filename))
    url = f'/static/uploads/{filename}'
    return jsonify({'success': True, 'url': url})


@app.route('/api/menu', methods=['POST'])
@login_required
def add_menu_item():
    data = request.json
    item = MenuItem(
        name=data['name'],
        description=data.get('description', ''),
        price=float(data['price']),
        cost_price=float(data.get('cost_price', 0) or 0),
        category_id=data.get('category_id') or None,
        is_available=data.get('is_available', True),
        image_url=data.get('image_url', '') or '',
        stock_qty=data.get('stock_qty') if data.get('stock_qty') not in (None, '') else None,
    )
    db.session.add(item)
    db.session.commit()
    return jsonify({'success': True, 'id': item.id})


@app.route('/api/menu/<int:item_id>', methods=['PUT'])
@login_required
def update_menu_item(item_id):
    item = MenuItem.query.get_or_404(item_id)
    data = request.json
    item.name = data.get('name', item.name)
    item.description = data.get('description', item.description)
    item.price = float(data.get('price', item.price))
    if 'cost_price' in data:
        item.cost_price = float(data.get('cost_price') or 0)
    item.category_id = data.get('category_id', item.category_id)
    item.is_available = data.get('is_available', item.is_available)
    if 'image_url' in data:
        item.image_url = data.get('image_url') or ''
    if 'stock_qty' in data:
        val = data.get('stock_qty')
        item.stock_qty = int(val) if val is not None and str(val) != '' else None
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/menu/<int:item_id>', methods=['DELETE'])
@login_required
def delete_menu_item(item_id):
    item = MenuItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/categories', methods=['GET'])
@login_required
def list_categories():
    cats = Category.query.order_by(Category.sort_order).all()
    result = []
    for c in cats:
        count = MenuItem.query.filter_by(category_id=c.id).count()
        result.append({'id': c.id, 'name': c.name, 'sort_order': c.sort_order, 'item_count': count, 'image_url': c.image_url or ''})
    return jsonify(result)


@app.route('/api/categories', methods=['POST'])
@login_required
def add_category():
    data = request.json
    cat = Category(name=data['name'], sort_order=data.get('sort_order', 0), image_url=data.get('image_url', '') or '')
    db.session.add(cat)
    db.session.commit()
    return jsonify({'success': True, 'id': cat.id})


@app.route('/api/categories/<int:cat_id>', methods=['PUT'])
@login_required
def update_category(cat_id):
    cat = Category.query.get_or_404(cat_id)
    data = request.json
    cat.name = data.get('name', cat.name)
    cat.sort_order = data.get('sort_order', cat.sort_order)
    if 'image_url' in data:
        cat.image_url = data.get('image_url') or ''
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/categories/<int:cat_id>', methods=['DELETE'])
@login_required
def delete_category(cat_id):
    cat = Category.query.get_or_404(cat_id)
    # ย้ายสินค้าในหมวดนี้ออก (ไม่ลบสินค้า)
    MenuItem.query.filter_by(category_id=cat_id).update({'category_id': None})
    db.session.delete(cat)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/admin/products')
@login_required
@role_required('admin')
def admin_products():
    categories = Category.query.order_by(Category.sort_order).all()
    items = MenuItem.query.order_by(MenuItem.sort_order).all()
    # group by category
    grouped = {}
    no_cat = []
    for item in items:
        if item.category_id:
            grouped.setdefault(item.category_id, []).append(item)
        else:
            no_cat.append(item)
    return render_template('admin/products.html',
                           categories=categories,
                           grouped=grouped,
                           no_cat=no_cat,
                           items=items,
                           current_user=get_current_user())


# ---- Table Management API ----

@app.route('/api/tables', methods=['POST'])
@login_required
def add_table():
    data = request.json
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'กรุณาระบุชื่อโต๊ะ'}), 400

    code = str(uuid.uuid4())[:8]
    table = Table(name=name, code=code)
    db.session.add(table)
    db.session.commit()
    return jsonify({
        'success': True,
        'id': table.id,
        'code': table.code,
        'qr': generate_qr_code(table.code)
    })


@app.route('/api/tables/<int:table_id>', methods=['DELETE'])
@login_required
def delete_table(table_id):
    table = Table.query.get_or_404(table_id)
    table.is_active = False
    db.session.commit()
    return jsonify({'success': True})



# ==================== USER MANAGEMENT ====================

@app.route('/api/users')
@login_required
def api_users():
    denied = require_admin_api()
    if denied:
        return denied
    users = User.query.order_by(User.id).all()
    return jsonify([{
        'id': u.id,
        'username': u.username,
        'name': u.name,
        'role': u.role,
        'is_active': u.is_active,
        'created_at': u.created_at.strftime('%d/%m/%Y') if u.created_at else '',
    } for u in users])


@app.route('/api/users', methods=['POST'])
@login_required
def api_create_user():
    denied = require_admin_api()
    if denied:
        return denied
    data = request.json or {}
    username = (data.get('username') or '').strip()
    name = (data.get('name') or '').strip()
    password = data.get('password') or ''
    role = data.get('role') or 'staff'
    if not username or not name or not password:
        return jsonify({'success': False, 'message': 'กรอกชื่อผู้ใช้ ชื่อ และรหัสผ่าน'}), 400
    if role not in ('admin', 'staff', 'kitchen', 'cashier'):
        return jsonify({'success': False, 'message': 'บทบาทไม่ถูกต้อง'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'success': False, 'message': 'ชื่อผู้ใช้นี้มีอยู่แล้ว'}), 400
    u = User(username=username, name=name, role=role, is_active=True)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return jsonify({'success': True, 'id': u.id})


@app.route('/api/users/<int:uid>', methods=['PUT'])
@login_required
def api_update_user(uid):
    denied = require_admin_api()
    if denied:
        return denied
    u = User.query.get_or_404(uid)
    data = request.json or {}
    if 'name' in data and data['name'].strip():
        u.name = data['name'].strip()
    if 'role' in data and data['role'] in ('admin', 'staff', 'kitchen', 'cashier'):
        # ห้ามลดสิทธิ์ admin คนสุดท้าย
        if u.role == 'admin' and data['role'] != 'admin':
            admin_count = User.query.filter_by(role='admin', is_active=True).count()
            if admin_count <= 1:
                return jsonify({'success': False, 'message': 'ต้องมี Admin อย่างน้อย 1 คน'}), 400
        u.role = data['role']
    if 'is_active' in data:
        if u.role == 'admin' and not data['is_active']:
            admin_count = User.query.filter_by(role='admin', is_active=True).count()
            if admin_count <= 1:
                return jsonify({'success': False, 'message': 'ต้องมี Admin อย่างน้อย 1 คน'}), 400
        u.is_active = bool(data['is_active'])
    if data.get('password'):
        u.set_password(data['password'])
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/users/<int:uid>', methods=['DELETE'])
@login_required
def api_delete_user(uid):
    denied = require_admin_api()
    if denied:
        return denied
    u = User.query.get_or_404(uid)
    if u.id == session.get('user_id'):
        return jsonify({'success': False, 'message': 'ลบบัญชีตัวเองไม่ได้'}), 400
    if u.role == 'admin':
        admin_count = User.query.filter_by(role='admin', is_active=True).count()
        if admin_count <= 1:
            return jsonify({'success': False, 'message': 'ต้องมี Admin อย่างน้อย 1 คน'}), 400
    db.session.delete(u)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/me')
@login_required
def api_me():
    u = get_current_user()
    return jsonify({
        'id': u.id,
        'username': u.username,
        'name': u.name,
        'role': u.role,
        'can_edit_bills': can_edit_bills(u),
        'is_admin': is_admin_user(u),
    })


# ==================== INIT DB + SEED ====================

def seed_data():
    # สร้างผู้ใช้เริ่มต้น
    if User.query.count() == 0:
        admin = User(username='admin', name='ผู้ดูแลระบบ', role='admin')
        admin.set_password('admin123')

        cashier = User(username='cashier', name='แคชเชียร์', role='cashier')
        cashier.set_password('cashier123')

        kitchen = User(username='kitchen', name='พนักงานครัว', role='kitchen')
        kitchen.set_password('kitchen123')

        db.session.add_all([admin, cashier, kitchen])
        db.session.commit()
        print("✅ Default users created (admin/admin123, cashier/cashier123, kitchen/kitchen123)")

    # หัวบิลเริ่มต้น
    if not Setting.query.get('shop_name'):
        defaults = {
            'shop_name': 'สติๆ',
            'shop_address': '',
            'shop_phone': '',
            'shop_tax_id': '',
            'receipt_footer': 'ขอบคุณที่ใช้บริการ',
        }
        for k, v in defaults.items():
            db.session.add(Setting(key=k, value=v))
        db.session.commit()
        print("✅ Default receipt settings created")

    if PrintStation.query.count() == 0:
        # default stations - category ids filled after categories exist
        stations = [
            PrintStation(name='ครัว', station_type='kitchen', category_ids='1'),
            PrintStation(name='บาร์น้ำ', station_type='bar', category_ids='2'),
            PrintStation(name='แคชเชียร์', station_type='cashier', category_ids=''),
        ]
        db.session.add_all(stations)
        db.session.commit()
        print("✅ Default print stations created")

    if Category.query.count() == 0:
        cats = [
            Category(name='อาหารจานหลัก', sort_order=1),
            Category(name='เครื่องดื่ม', sort_order=2),
            Category(name='ของหวาน', sort_order=3),
        ]
        db.session.add_all(cats)
        db.session.commit()

        items = [
            MenuItem(name='ผัดกะเพราหมูสับ', price=60, category_id=1, description='ระดับความเผ็ดตามสั่ง'),
            MenuItem(name='ข้าวผัดกุ้ง', price=70, category_id=1),
            MenuItem(name='ต้มยำกุ้ง', price=120, category_id=1, description='ต้มยำน้ำข้น'),
            MenuItem(name='ผัดไทยกุ้งสด', price=80, category_id=1),
            MenuItem(name='ชาเย็น', price=35, category_id=2),
            MenuItem(name='กาแฟเย็น', price=40, category_id=2),
            MenuItem(name='น้ำเปล่า', price=15, category_id=2),
            MenuItem(name='โค้ก', price=25, category_id=2),
            MenuItem(name='บัวลอย', price=40, category_id=3),
            MenuItem(name='ไอศกรีม', price=30, category_id=3),
        ]
        db.session.add_all(items)

        # สร้างโต๊ะตัวอย่าง
        for i in range(1, 9):
            t = Table(name=f'โต๊ะ {i}', code=f'table{i:02d}')
            db.session.add(t)

        db.session.commit()
        print("✅ Seed data created successfully")


with app.app_context():
    db.create_all()
    # migrate: add columns if missing
    from sqlalchemy import text as sa_text
    for sql in [
        'ALTER TABLE menu_items ADD COLUMN cost_price FLOAT DEFAULT 0',
        'ALTER TABLE menu_items ADD COLUMN stock_qty INTEGER',
        'ALTER TABLE orders ADD COLUMN payment_method VARCHAR(20) DEFAULT ""',
        'ALTER TABLE categories ADD COLUMN image_url VARCHAR(300) DEFAULT ""',
    ]:
        try:
            db.session.execute(sa_text(sql))
            db.session.commit()
        except Exception:
            db.session.rollback()
    seed_data()
    # ensure stock categories exist
    for name, order in [('อาหารทานเล่น', 10), ('มิกซ์เซอร์', 11), ('เบียร์', 12), ('เหล้า', 13)]:
        if not Category.query.filter_by(name=name).first():
            db.session.add(Category(name=name, sort_order=order))
    db.session.commit()


if __name__ == '__main__':
    print("\n" + "="*50)
    print("🚀 สติๆ System is running!")
    print("="*50)
    print("🔐 เข้าสู่ระบบ      : http://127.0.0.1:5000/login")
    print("   admin / admin123")
    print("   cashier / cashier123")
    print("   kitchen / kitchen123")
    print("📱 หน้าลูกค้าตัวอย่าง : http://127.0.0.1:5000/order/table01")
    print("👨‍💼 แอดมิน          : http://127.0.0.1:5000/admin")
    print("🍳 หน้าครัว         : http://127.0.0.1:5000/kitchen")
    print("💰 หน้าแคชเชียร์    : http://127.0.0.1:5000/admin/orders")
    print("="*50 + "\n")
    app.run(debug=True, host='0.0.0.0', port=5000)

