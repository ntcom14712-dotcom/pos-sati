# 🍽️ สติๆ - ระบบสั่งอาหารผ่าน QR Code

ระบบ Point of Sale สำหรับร้านอาหาร ที่ลูกค้าสามารถสแกน QR Code ที่โต๊ะ แล้วสั่งอาหารได้ทันที

## ฟีเจอร์หลัก

- ✅ ลูกค้าสแกน QR → เห็นเมนู → สั่งอาหาร + ระบุหมายเหตุ
- ✅ หน้าครัว เห็นออเดอร์แบบเกือบเรียลไทม์ (อัปเดตทุก 4 วินาที)
- ✅ หน้าแคชเชียร์ จัดการออเดอร์ + บันทึกการชำระเงิน
- ✅ จัดการเมนู (เพิ่ม / แก้ไข / ลบ / เปิด-ปิดขาย)
- ✅ จัดการโต๊ะ + สร้าง QR Code อัตโนมัติ
- ✅ พิมพ์ QR Code และใบเสร็จผ่านเบราว์เซอร์
- ✅ รองรับหลายโต๊ะ

## บัญชีผู้ใช้เริ่มต้น

| Username | Password | บทบาท |
|----------|----------|--------|
| admin | admin123 | ผู้ดูแลระบบ |
| cashier | cashier123 | แคชเชียร์ |
| kitchen | kitchen123 | พนักงานครัว |

หน้า Login: http://127.0.0.1:5000/login

## วิธีติดตั้งและรัน

### 1. ติดตั้ง dependencies

```bash
cd pos-qr
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. รันเซิร์ฟเวอร์

```bash
python app.py
```

### 3. เปิดใช้งาน

| หน้า | URL |
|------|-----|
| แดชบอร์ด | http://127.0.0.1:5000/admin |
| หน้าครัว | http://127.0.0.1:5000/kitchen |
| แคชเชียร์ | http://127.0.0.1:5000/admin/orders |
| จัดการเมนู | http://127.0.0.1:5000/admin/menu |
| จัดการโต๊ะ + QR | http://127.0.0.1:5000/admin/tables |
| หน้าลูกค้า (ตัวอย่าง) | http://127.0.0.1:5000/order/table01 |

## โครงสร้างโปรเจกต์

```
pos-qr/
├── app.py                  # Flask backend ทั้งหมด
├── requirements.txt
├── README.md
├── pos.db                  # สร้างอัตโนมัติเมื่อรันครั้งแรก
├── static/
│   └── qrcodes/
└── templates/
    ├── base.html
    ├── kitchen.html
    ├── customer/
    │   └── order.html
    └── admin/
        ├── dashboard.html
        ├── menu.html
        ├── tables.html
        └── orders.html
```

## ข้อมูลตัวอย่างที่ระบบสร้างให้อัตโนมัติ

- หมวดหมู่: อาหารจานหลัก, เครื่องดื่ม, ของหวาน
- เมนู 10 รายการ
- โต๊ะ 1–8 (รหัส table01 ถึง table08)

## การนำไปใช้งานจริง

1. เปลี่ยน URL ในฟังก์ชัน `generate_qr_code()` ให้เป็นโดเมนจริงของคุณ
2. ตั้งค่า `SECRET_KEY` ให้ปลอดภัย
3. ใช้ฐานข้อมูล PostgreSQL / MySQL แทน SQLite (แนะนำสำหรับ production)
4. เพิ่มระบบ Login สำหรับพนักงาน
5. เชื่อม PromptPay / Payment Gateway ได้ตามต้องการ

## License

MIT - ใช้พัฒนาต่อได้อย่างอิสระ
