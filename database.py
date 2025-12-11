import sqlite3
from datetime import datetime, timedelta

DB = "bot_premium.db"

def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            downloads INTEGER DEFAULT 0,
            vip_until TEXT DEFAULT NULL,
            last_reset TEXT
        )
    """)
    
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    
    if not user:
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute("INSERT INTO users (user_id, downloads, last_reset) VALUES (?, ?, ?)", 
                  (user_id, 0, today))
    
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    
    conn.close()
    return user

def increment_downloads(user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    c.execute("UPDATE users SET downloads = downloads + 1 WHERE user_id=?", (user_id,))
    
    conn.commit()
    conn.close()

def reset_daily_limit(user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("UPDATE users SET downloads = 0, last_reset=? WHERE user_id=?", (today, user_id))
    
    conn.commit()
    conn.close()

def activate_vip(user_id, days):
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    vip_until = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    
    c.execute("UPDATE users SET vip_until=? WHERE user_id=?", (vip_until, user_id))
    
    conn.commit()
    conn.close()
