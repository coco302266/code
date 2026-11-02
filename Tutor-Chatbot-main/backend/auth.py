"""用户口令哈希与校验。

为什么不用 passlib / bcrypt：这两个都要多装一份二进制依赖，而本项目
只需要「存得下、校验得动」这一件事。PBKDF2-HMAC-SHA256 是标准库自带、
FIPS 认可的口令派生函数，200k 轮迭代在本机约 60ms，足够挡住离线爆破。

存储格式：``pbkdf2_sha256$<iterations>$<salt_hex>$<digest_hex>``
- 带上算法与迭代轮数，将来提高迭代次数时老口令仍能校验（按存储值算）。
- 盐随每次写入随机生成，同一密码在不同账号下哈希值不同。

一条硬规则：**任何接口都不得回传 password_hash**。User 模型刻意不加
``__repr__`` 里的口令字段，接口层也只挑 id/username/role 三个字段返回。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000
_SALT_BYTES = 16

# 口令长度下限。定为 6 是为了不挡住课堂现场注册（学生大多随手设一个），
# 上限 64 防止有人拿超长串把 PBKDF2 当 DoS 放大器——PBKDF2 的耗时与
# 口令长度基本无关，但请求体解析和日志会被撑爆。
PASSWORD_MIN_LEN = 6
PASSWORD_MAX_LEN = 64


def hash_password(raw: str) -> str:
    """把明文口令变成可入库的哈希串。"""
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", raw.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS
    )
    return f"{_ALGO}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(raw: str, stored: str | None) -> bool:
    """校验口令。

    ``stored`` 为空表示这是「密码功能上线前就存在」的老账号（password_hash
    为 NULL）：一律返回 False，由调用方决定怎么处理（当前做法是首次登录
    时把用户输入的口令绑定上去，见 app.py 的 /auth/login）。
    """
    if not stored:
        return False
    try:
        algo, iterations, salt, expected = stored.split("$")
        rounds = int(iterations)
    except (ValueError, AttributeError):
        # 库里的值被手工改坏时不要把异常抛给接口，当成校验失败即可。
        return False
    if algo != _ALGO or rounds <= 0:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", raw.encode("utf-8"), salt.encode("utf-8"), rounds
    )
    # compare_digest：定长比较，避免按字节提前返回泄露前缀信息。
    return hmac.compare_digest(digest.hex(), expected)


def password_problem(raw: str) -> str | None:
    """返回口令不合规的原因；合规返回 None。

    文案直接面向学生，所以不用「长度校验失败」这类术语。
    """
    if not raw:
        return "密码不能为空。"
    if len(raw) < PASSWORD_MIN_LEN:
        return f"密码至少 {PASSWORD_MIN_LEN} 位。"
    if len(raw) > PASSWORD_MAX_LEN:
        return f"密码最多 {PASSWORD_MAX_LEN} 位。"
    if raw.strip() != raw:
        return "密码首尾不能有空格。"
    return None
