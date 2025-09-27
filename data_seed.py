# data_seed.py
import random
random.seed(42)  # stable demo data
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

CITIES = ["Delhi", "Mumbai", "Bengaluru", "Hyderabad", "Chennai", "Pune", "Kolkata", "Ahmedabad"]
COURIERS = ["Bluedart", "Delhivery", "XpressBees", "Ecom Express", "Shadowfax"]
ITEMS = [
    "Wireless Earbuds", "Phone Case", "USB-C Cable", "Power Bank", "Keyboard", "Water Bottle",
    "Running Shoes", "Backpack", "Analog Watch", "Bluetooth Speaker", "LED Bulb", "Shirt", "Jeans"
]
FIRST = ["Aman","Priya","Ravi","Neha","Kiran","Sneha","Rohit","Aisha","Kabir","Aditi","Ankit","Meera"]
STATUSES = ["Processing", "Shipped", "Out for Delivery", "Delivered", "Return Initiated", "Refunded"]

LOGIN_PHONE = "9876543210"        # <- demo login phone
LOGIN_EMAIL = "demo@customer.com" # <- demo email

def random_order_id(i: int) -> str:
    return f"ORD{10000 + i}"

def rand_phone() -> str:
    return "9" + "".join(str(random.randint(0,9)) for _ in range(9))

def email_for(name: str) -> str:
    base = name.lower()
    domain = random.choice(["gmail.com","yahoo.com","outlook.com"])
    return f"{base}{random.randint(10,99)}@{domain}"

def seed_faqs() -> dict:
    return {
        "returns_window": "You can return most items within 10 days of delivery if unused and in original packaging.",
        "refund_timeline": "Refunds are processed in 3–5 business days after the item passes QC.",
        "non_returnable": "Innerwear, perishable goods, and gift cards are not returnable.",
        "cancellation": "Orders can be cancelled only before they are shipped.",
        "packaging": "Keep the original box, all accessories, and invoice for a smooth return."
    }

def seed_orders(n: int = 260) -> dict:
    today = datetime.now().date()
    orders = {}

    buckets = [
        ("Processing", int(n * 0.20)),
        ("Shipped", int(n * 0.27)),
        ("Out for Delivery", int(n * 0.15)),
        ("Delivered", int(n * 0.28)),
        ("Return Initiated", int(n * 0.05)),
        ("Refunded", n - sum([int(n * x) for x in [0.20,0.27,0.15,0.28,0.05]])),
    ]

    i = 0
    for status, count in buckets:
        for _ in range(count):
            i += 1
            order_id = random_order_id(i)
            name = random.choice(FIRST)
            phone = rand_phone()
            email = email_for(name)
            order_date = today - timedelta(days=random.randint(0, 45))
            ship_date = None
            est_delivery = None
            delivered_date = None
            if status != "Processing":
                ship_date = order_date + timedelta(days=random.randint(0, 2))
                est_delivery = ship_date + timedelta(days=random.randint(2, 6))
            if status in {"Delivered","Return Initiated","Refunded"}:
                delivered_date = order_date + timedelta(days=random.randint(4, 10))
            return_eligible_until = (delivered_date + timedelta(days=10)) if delivered_date else None

            # small, unique image per order using picsum (no signup needed)
            image_url = f"https://picsum.photos/seed/{order_id}/100/100"

            orders[order_id] = {
                "order_id": order_id,
                "customer_name": name,
                "customer_email": email,
                "customer_phone": phone,
                "order_date": order_date.isoformat(),
                "items": random.sample(ITEMS, k=random.randint(1, 3)),
                "status": status,
                "ship_date": ship_date.isoformat() if ship_date else None,
                "est_delivery_date": est_delivery.isoformat() if est_delivery else None,
                "delivered_date": delivered_date.isoformat() if delivered_date else None,
                "courier": random.choice(COURIERS) if status != "Processing" else None,
                "tracking_id": f"TRK{random.randint(100000,999999)}" if status != "Processing" else None,
                "return_eligible_until": return_eligible_until.isoformat() if return_eligible_until else None,
                "return_status": "Initiated" if status == "Return Initiated" else ("Not Started" if status in {"Delivered"} else None),
                "refund_status": "Completed" if status == "Refunded" else ("Pending" if status == "Return Initiated" else None),
                "payment_method": random.choice(["UPI","Credit Card","Debit Card","COD"]),
                "address_city": random.choice(CITIES),
                "address_line": f"{random.randint(1,200)} Main Road, {random.choice(CITIES)}",
                "image_url": image_url,
                "issues_history": [],
            }

    # Ensure the demo login phone has several orders
    ensure_demo_user_orders(orders, login_phone=LOGIN_PHONE, login_email=LOGIN_EMAIL, count=10)
    return orders

def ensure_demo_user_orders(orders: dict, login_phone: str, login_email: str, count: int = 10):
    all_ids = list(orders.keys())
    random.shuffle(all_ids)
    for oid in all_ids[:count]:
        orders[oid]["customer_phone"] = login_phone
        orders[oid]["customer_email"] = login_email

def seed_all(n: int = 260):
    return seed_orders(n=n), seed_faqs()
