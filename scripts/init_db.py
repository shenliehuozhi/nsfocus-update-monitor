#!/usr/bin/env python3
"""Initialize database and create admin user."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.models.database import init_db
from src.models import init_all_tables


def main():
    data_dir = os.getenv('MONITOR_DATA_DIR',
                         os.path.join(os.path.dirname(__file__), '..', 'data'))
    os.makedirs(data_dir, exist_ok=True)

    init_db(data_dir)
    init_all_tables()

    print(f'Database initialized at {data_dir}/nsfocus_monitor.db')
    print('Tables created successfully.')
    print()
    print('To create admin user:')
    print(f'  python {__file__} --create-admin --username admin --password <password>')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--create-admin', action='store_true')
    parser.add_argument('--username', default='admin')
    parser.add_argument('--password', default='')

    # Filter out custom args before calling main logic
    args_remaining = [a for a in sys.argv[1:] if not a.startswith('--') or a in ('--create-admin',)]
    
    args = parser.parse_args()

    data_dir = os.getenv('MONITOR_DATA_DIR',
                         os.path.join(os.path.dirname(__file__), '..', 'data'))
    os.makedirs(data_dir, exist_ok=True)
    init_db(data_dir)
    init_all_tables()
    print(f'Database initialized at {data_dir}/nsfocus_monitor.db')

    if args.create_admin:
        if not args.password:
            print('Error: --password required')
            sys.exit(1)
        import bcrypt
        from src.models.user import create_user
        pw_hash = bcrypt.hashpw(args.password.encode(), bcrypt.gensalt()).decode()
        uid = create_user(args.username, pw_hash, is_admin=True)
        print(f'Admin user created: id={uid}, username={args.username}')
