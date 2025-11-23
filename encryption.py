import os
import json
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.fernet import Fernet
import base64

def derive_key(password: str, salt: bytes) -> bytes:
    """Derive encryption key from password using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend()
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))

def encrypt_data(data: dict, password: str) -> dict:
    """Encrypt JSON data with password."""
    salt = os.urandom(16)
    key = derive_key(password, salt)
    f = Fernet(key)
    encrypted = f.encrypt(json.dumps(data).encode())
    return {
        'salt': base64.b64encode(salt).decode(),
        'data': base64.b64encode(encrypted).decode()
    }

def decrypt_data(encrypted_dict: dict, password: str) -> dict:
    """Decrypt JSON data with password. Raises exception if wrong password."""
    salt = base64.b64decode(encrypted_dict['salt'])
    key = derive_key(password, salt)
    f = Fernet(key)
    decrypted = f.decrypt(base64.b64decode(encrypted_dict['data']))
    return json.loads(decrypted)
