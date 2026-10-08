import hashlib
import hmac
import secrets

ITERATIONS = 600000

def hash_password(password: str):
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${key}'

def verify_password(password: str, stored: str):
    try:
        _, rounds, salt, expected = stored.split('$')
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def token_hash(token: str):
    return hashlib.sha256(token.encode()).hexdigest()
