"""
密码处理：bcrypt 哈希与验证
"""

from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    """
    使用 bcrypt 哈希密码
    
    自动处理 salt，返回可存储的哈希字符串
    """
    # bcrypt 需要 bytes
    password_bytes = password.encode("utf-8")
    # 自动生成 salt 并哈希
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt(rounds=12))
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """
    验证密码是否匹配
    
    :param password: 明文密码
    :param hashed: 存储的 bcrypt 哈希
    :return: 是否匹配
    """
    password_bytes = password.encode("utf-8")
    hashed_bytes = hashed.encode("utf-8")
    return bcrypt.checkpw(password_bytes, hashed_bytes)
