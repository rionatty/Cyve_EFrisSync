"""
Encryption utilities mirroring SAP B1 EncryptionHelper.cs

EFRIS uses:
  - AES-128/256 ECB mode with PKCS7 padding for data encryption
  - RSA-OAEP for key exchange
  - GZip for optional compression
  - Base64 for wire encoding
"""

import base64
import gzip
import json

from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad, unpad


def aes_encrypt(data: bytes, key: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(pad(data, AES.block_size))


def aes_decrypt(data: bytes, key: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    return unpad(cipher.decrypt(data), AES.block_size)


def rsa_decrypt(encrypted_b64: str, private_key_pem: str) -> bytes:
    key = RSA.import_key(private_key_pem)
    cipher = PKCS1_OAEP.new(key)
    return cipher.decrypt(base64.b64decode(encrypted_b64))


def rsa_encrypt(data: bytes, public_key_pem: str) -> str:
    key = RSA.import_key(public_key_pem)
    cipher = PKCS1_OAEP.new(key)
    return base64.b64encode(cipher.encrypt(data)).decode()


def encode_request_data(payload: dict, aes_key: bytes, compress: bool = False) -> dict:
    """
    Serialize → optionally gzip → AES-ECB encrypt → base64.
    Returns {"content": "...", "dataDescription": {"zipCode": "0|1", "encryptCode": "0|2"}}
    """
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    zip_code = "0"
    if compress:
        raw = gzip.compress(raw)
        zip_code = "1"

    encrypted = aes_encrypt(raw, aes_key)
    content = base64.b64encode(encrypted).decode("utf-8")

    return {
        "content": content,
        "dataDescription": {
            "zipCode": zip_code,
            "encryptCode": "2",
        },
    }


def decode_response_data(data: dict, aes_key: bytes) -> dict:
    """
    Base64 decode → AES-ECB decrypt → optionally gunzip → JSON parse.
    Handles encryptCode 0 (plain), 2 (AES), zipCode 0 (raw), 1 (gzip).
    """
    content = data.get("content", "")
    desc = data.get("dataDescription", {})
    encrypt_code = str(desc.get("encryptCode", "2"))
    zip_code = str(desc.get("zipCode", "0"))

    raw = base64.b64decode(content)

    if encrypt_code == "2":
        raw = aes_decrypt(raw, aes_key)

    if zip_code == "1":
        raw = gzip.decompress(raw)

    return json.loads(raw.decode("utf-8"))


def derive_private_key_aes_key(taxpayer_id: str, init_date: str) -> bytes:
    """
    SAP B1 derives the AES key for the private key file as:
      TaxpayerID.Substring(0,10) + date formatted as "ddMMyy"
    Returns a 16-byte key (pad/truncate to 16).
    """
    base = (taxpayer_id[:10] + init_date).encode("utf-8")
    return base[:16].ljust(16, b"\x00")
