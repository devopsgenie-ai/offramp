import os

from jose import jwt

JWT_SECRET = os.environ["JWT_SECRET"]


def decode(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
