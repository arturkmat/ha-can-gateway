"""Provisioning helpers for CAN node key distribution.

This module implements a deterministic per-device key derivation based on:
- global 32-byte MASTER_KEY
- unique efuse MAC address (6 bytes)

Node key derivation:
    node_key = HMAC_SHA256(MASTER_KEY, b"CAN-NODE-KEY|v1|" + mac_bytes)[:16]

Provisioning payload:
    ciphertext = AES-128-ECB(dst_key).encrypt(target_key)
"""

from __future__ import annotations

import hmac
import re
from hashlib import sha256

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class CanProvisioner:
    """Derive node keys and build provisioning payloads."""

    _CTX = b"CAN-NODE-KEY|v1|"
    _MAC_HEX_RE = re.compile(r"^[0-9a-fA-F]{12}$")

    def __init__(self, master_key: bytes) -> None:
        if not isinstance(master_key, (bytes, bytearray)):
            raise TypeError("master_key must be bytes-like")
        if len(master_key) != 32:
            raise ValueError("master_key must be exactly 32 bytes")
        self._master_key = bytes(master_key)

    @classmethod
    def _normalize_efuse_mac(cls, efuse_mac_hex: str) -> bytes:
        if not isinstance(efuse_mac_hex, str):
            raise TypeError("efuse_mac_hex must be a string")
        cleaned = efuse_mac_hex.replace(":", "").replace("-", "").strip()
        if not cls._MAC_HEX_RE.fullmatch(cleaned):
            raise ValueError(
                "efuse_mac_hex must be a 12-hex-character MAC (with or without ':'/'-')"
            )
        return bytes.fromhex(cleaned)

    def derive_node_key(self, efuse_mac_hex: str) -> bytes:
        """Return 16-byte per-node key derived from efuse MAC."""
        mac_bytes = self._normalize_efuse_mac(efuse_mac_hex)
        digest = hmac.new(self._master_key, self._CTX + mac_bytes, sha256).digest()
        return digest[:16]

    def generate_provisioning_payload(
        self, target_efuse_mac: str, destination_efuse_mac: str
    ) -> bytes:
        """Return 16-byte ciphertext carrying target key for destination node.

        The plaintext is the 16-byte target key.
        Encryption key is destination node 16-byte key.
        Cipher mode is AES-128-ECB (single 16-byte block payload).
        """
        target_key = self.derive_node_key(target_efuse_mac)
        destination_key = self.derive_node_key(destination_efuse_mac)
        cipher = Cipher(algorithms.AES(destination_key), modes.ECB())
        encryptor = cipher.encryptor()
        return encryptor.update(target_key) + encryptor.finalize()
