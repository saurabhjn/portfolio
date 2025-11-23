#!/usr/bin/env python3
import json
import os
import shutil
import getpass
from datetime import datetime
from encryption import encrypt_data

DATA_DIR = "data"
DATA_FILE = os.path.join(DATA_DIR, "investments.json")
TRANSACTIONS_FILE = os.path.join(DATA_DIR, "transactions.json")
ENCRYPTED_DATA_FILE = os.path.join(DATA_DIR, "investments.enc")
ENCRYPTED_TRANSACTIONS_FILE = os.path.join(DATA_DIR, "transactions.enc")

def migrate():
    if not os.path.exists(DATA_FILE) or not os.path.exists(TRANSACTIONS_FILE):
        print("No existing data files found. Nothing to migrate.")
        return
    
    if os.path.exists(ENCRYPTED_DATA_FILE) or os.path.exists(ENCRYPTED_TRANSACTIONS_FILE):
        print("Encrypted files already exist. Aborting to prevent overwrite.")
        return
    
    password = getpass.getpass("Enter password to encrypt data: ")
    confirm = getpass.getpass("Confirm password: ")
    
    if password != confirm:
        print("Passwords don't match!")
        return
    
    # Backup existing files
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_inv = f"{DATA_FILE}.backup_{timestamp}"
    backup_trans = f"{TRANSACTIONS_FILE}.backup_{timestamp}"
    
    shutil.copy2(DATA_FILE, backup_inv)
    shutil.copy2(TRANSACTIONS_FILE, backup_trans)
    print(f"Backed up: {backup_inv}")
    print(f"Backed up: {backup_trans}")
    
    with open(DATA_FILE, 'r') as f:
        inv_data = json.load(f)
    
    with open(TRANSACTIONS_FILE, 'r') as f:
        trans_data = json.load(f)
    
    with open(ENCRYPTED_DATA_FILE, 'w') as f:
        json.dump(encrypt_data(inv_data, password), f)
    
    with open(ENCRYPTED_TRANSACTIONS_FILE, 'w') as f:
        json.dump(encrypt_data(trans_data, password), f)
    
    print(f"\nData encrypted successfully!")
    print(f"Created: {ENCRYPTED_DATA_FILE}")
    print(f"Created: {ENCRYPTED_TRANSACTIONS_FILE}")
    print(f"\nBackup files saved. You can delete them after verifying encryption works.")

if __name__ == "__main__":
    migrate()
