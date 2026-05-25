#!/usr/bin/env python3
"""Reset user password — for admin password recovery."""

import argparse, sys, os, bcrypt

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.database import get_db


def reset_password(username: str, new_password: str):
    db = get_db()
    rows = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchall()
    if not rows:
        print(f"用户 '{username}' 不存在")
        return False

    user_id = rows[0]['id']
    pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id))
    db.commit()
    print(f"✓ 用户 '{username}' 密码已重置")
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='重置用户密码')
    parser.add_argument('--username', default='admin', help='用户名（默认: admin）')
    parser.add_argument('--password', required=True, help='新密码')
    args = parser.parse_args()

    reset_password(args.username, args.password)