from dataclasses import dataclass
from typing import Any
import imaplib
from httpx import AsyncClient
from .account import Account

@dataclass
class LoginConfig:
    email_first: bool = False
    manual: bool = False
    imap_proxy_host: str = None
    imap_proxy_port: int = None
    imap_proxy_user: str = None
    imap_proxy_pass: str = None

@dataclass
class TaskCtx:
    client: AsyncClient
    acc: Account
    cfg: LoginConfig
    prev: Any
    imap: None | imaplib.IMAP4_SSL
