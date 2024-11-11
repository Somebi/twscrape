import asyncio
import email as emaillib
import imaplib
import os
import time
from datetime import datetime

from twscrape.login import LoginConfig

from .logger import logger

import socket
import ssl

def create_http_tunnel(proxy_host, proxy_port, target_host, target_port, proxy_user=None, proxy_pass=None):
    # Create a socket connection to the proxy
    sock = socket.create_connection((proxy_host, proxy_port))
    connect_command = f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"

    # Add proxy authentication if provided
    if proxy_user and proxy_pass:
        import base64
        credentials = f"{proxy_user}:{proxy_pass}"
        encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
        connect_command += f"Proxy-Authorization: Basic {encoded_credentials}\r\n"

    connect_command += "\r\n"

    # Send the CONNECT request to the proxy
    sock.sendall(connect_command.encode('utf-8'))

    # Read the response from the proxy
    response = sock.recv(4096).decode('utf-8')
    if "200 Connection established" not in response:
        sock.close()
        raise Exception(f"Failed to establish a tunnel: {response}")

    return sock

class IMAP4_SSL_HTTP_Proxy(imaplib.IMAP4_SSL):
    def __init__(self, host, port=imaplib.IMAP4_SSL_PORT, proxy_host=None, proxy_port=None, proxy_user=None, proxy_pass=None, ssl_context=None):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.proxy_user = proxy_user
        self.proxy_pass = proxy_pass
        self.ssl_context = ssl_context
        super().__init__(host, port)

    def open(self, host='', port=imaplib.IMAP4_SSL_PORT):
        self.host = host
        self.port = port
        raw_sock = create_http_tunnel(
            self.proxy_host, self.proxy_port, self.host, self.port, self.proxy_user, self.proxy_pass
        )
        if self.ssl_context is None:
            self.ssl_context = ssl.create_default_context()
        self.sock = self.ssl_context.wrap_socket(raw_sock, server_hostname=self.host)
        self.file = self.sock.makefile('rb')


def env_int(key: str | list[str], default: int) -> int:
    key = [key] if isinstance(key, str) else key
    val = [os.getenv(k) for k in key]
    val = [int(x) for x in val if x is not None]
    return val[0] if val else default


TWS_WAIT_EMAIL_CODE = env_int(["TWS_WAIT_EMAIL_CODE", "LOGIN_CODE_TIMEOUT"], 30)


class EmailLoginError(Exception):
    def __init__(self, message="Email login error"):
        self.message = message
        super().__init__(self.message)


class EmailCodeTimeoutError(Exception):
    def __init__(self, message="Email code timeout"):
        self.message = message
        super().__init__(self.message)


IMAP_MAPPING: dict[str, str] = {
    "yahoo.com": "imap.mail.yahoo.com",
    "icloud.com": "imap.mail.me.com",
    "outlook.com": "imap-mail.outlook.com",
    "hotmail.com": "imap-mail.outlook.com",
}


def add_imap_mapping(email_domain: str, imap_domain: str):
    IMAP_MAPPING[email_domain] = imap_domain


def _get_imap_domain(email: str) -> str:
    email_domain = email.split("@")[1]
    if email_domain in IMAP_MAPPING:
        return IMAP_MAPPING[email_domain]
    return f"imap.{email_domain}"


def _wait_email_code(imap: imaplib.IMAP4_SSL, count: int, min_t: datetime | None) -> str | None:
    for i in range(count, 0, -1):
        _, rep = imap.fetch(str(i), "(RFC822)")
        for x in rep:
            if isinstance(x, tuple):
                msg = emaillib.message_from_bytes(x[1])

                # https://www.ietf.org/rfc/rfc9051.html#section-6.3.12-13
                msg_time = msg.get("Date", "").split("(")[0].strip()
                msg_time = datetime.strptime(msg_time, "%a, %d %b %Y %H:%M:%S %z")

                msg_from = str(msg.get("From", "")).lower()
                msg_subj = str(msg.get("Subject", "")).lower()
                logger.info(f"({i} of {count}) {msg_from} - {msg_time} - {msg_subj}")

                if min_t is not None and msg_time < min_t:
                    return None

                if "info@x.com" in msg_from and "confirmation code is" in msg_subj:
                    # eg. Your Twitter confirmation code is XXX
                    return msg_subj.split(" ")[-1].strip()

    return None


async def imap_get_email_code(
    imap: imaplib.IMAP4_SSL, email: str, min_t: datetime | None = None
) -> str:
    try:
        logger.info(f"Waiting for confirmation code for {email}...")
        start_time = time.time()
        while True:
            _, rep = imap.select("INBOX")
            msg_count = int(rep[0].decode("utf-8")) if len(rep) > 0 and rep[0] is not None else 0
            code = _wait_email_code(imap, msg_count, min_t)
            if code is not None:
                return code

            if TWS_WAIT_EMAIL_CODE < time.time() - start_time:
                raise EmailCodeTimeoutError(f"Email code timeout ({TWS_WAIT_EMAIL_CODE} sec)")

            await asyncio.sleep(5)
    except Exception as e:
        imap.select("INBOX")
        imap.close()
        raise e


async def imap_login(email: str, password: str, cfg: LoginConfig):
    domain = _get_imap_domain(email)

    if not cfg.imap_proxy_host:
        imap = imaplib.IMAP4_SSL(domain)
    else:
        assert cfg.imap_proxy_host, "imap_proxy_host is required in LoginConfig"
        assert cfg.imap_proxy_port, "imap_proxy_port is required in LoginConfig"
        assert cfg.imap_proxy_user, "imap_proxy_user is required in LoginConfig"
        assert cfg.imap_proxy_pass, "imap_proxy_pass is required in LoginConfig"

        imap = IMAP4_SSL_HTTP_Proxy(
            host=domain,
            port=993,
            proxy_host=cfg.imap_proxy_host,
            proxy_port=cfg.imap_proxy_port,
            proxy_user=cfg.imap_proxy_user,
            proxy_pass=cfg.imap_proxy_pass
        )

    try:
        imap.login(email, password)
        imap.select("INBOX", readonly=True)
    except imaplib.IMAP4.error as e:
        logger.error(f"Error logging into {email} on {domain}: {e}")
        raise EmailLoginError() from e

    return imap
