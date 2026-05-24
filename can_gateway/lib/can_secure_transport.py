"""Encrypted CAN transport (Secure TLV type CAN_FRAME) shared by configurator and tools."""

from __future__ import annotations

import hmac
import struct
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Iterable, Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from protocol_constants import (
    CAN_ID_BINARY_BIND_EVENT,
    CAN_ID_BUTTON_BIND_EVENT,
    CAN_ID_CONFIG_REQUEST,
    CAN_ID_CONFIG_RESPONSE,
    CAN_ID_DEVICE_INFO,
    CAN_ID_RELAY_BIND_EVENT,
    CAN_ID_SECURE_TLV_REQUEST,
    CAN_ID_SECURE_TLV_RESPONSE,
    CAN_ID_SENSOR_BIND_EVENT,
    CAN_ID_SHUTTER_BIND_EVENT,
    CAN_ID_SHUTTER_CMD,
    PLAINTEXT_TELEMETRY_CAN_IDS,
    COMMAND_CAN_MUTE,
    COMMAND_GET_SUMMARY,
    COMMAND_PROVISION_APPLY,
    COMMAND_PROVISION_APPLY_MASTER_KEY,
    COMMAND_PROVISION_GET_MASTER_KEY_STATE,
    COMMAND_PROVISION_GET_STATE,
    COMMAND_PROVISION_SET_CIPHERTEXT_PART,
    COMMAND_PROVISION_SET_MASTER_KEY_PART,
    COMMAND_PROVISION_SET_TARGET_MAC,
    COMMAND_SET_MODULE_ID_BY_MAC,
    SECURE_TLV_CHUNK_BYTES,
    SECURE_TLV_MAC_BYTES,
    SECURE_TLV_TYPE_CAN_FRAME,
    SECURE_TLV_TYPE_CONFIG_REQUEST,
    SECURE_TLV_TYPE_CONFIG_RESPONSE,
)

NODE_KEY_CTX = b"CAN-NODE-KEY|v1|"
CAN_FRAME_VALUE_LEN = 14

PROVISION_COMMANDS = frozenset(
    {
        COMMAND_PROVISION_SET_TARGET_MAC,
        COMMAND_PROVISION_SET_CIPHERTEXT_PART,
        COMMAND_PROVISION_APPLY,
        COMMAND_PROVISION_GET_STATE,
        COMMAND_PROVISION_SET_MASTER_KEY_PART,
        COMMAND_PROVISION_APPLY_MASTER_KEY,
        COMMAND_PROVISION_GET_MASTER_KEY_STATE,
    }
)

PROVISION_MASTER_KEY_WRITE_COMMANDS = frozenset(
    {
        COMMAND_PROVISION_SET_MASTER_KEY_PART,
        COMMAND_PROVISION_APPLY_MASTER_KEY,
    }
)


def derive_node_key(master_key: bytes, mac_bytes: bytes) -> bytes:
    if len(master_key) != 32:
        raise ValueError("master_key must be 32 bytes")
    if len(mac_bytes) != 6:
        raise ValueError("mac_bytes must be 6 bytes")
    return hmac.new(master_key, NODE_KEY_CTX + mac_bytes, sha256).digest()[:16]


def xor_crypt(key16: bytes, msg_id: int, payload: bytes) -> bytes:
    if not payload:
        return b""
    cipher = Cipher(algorithms.AES(key16), modes.ECB())
    encryptor = cipher.encryptor()
    block_prefix = bytes([ord("S"), ord("T"), ord("L"), ord("V"), msg_id & 0xFF])
    out = bytearray(payload)
    offset = 0
    counter = 0
    while offset < len(out):
        block_in = block_prefix + bytes([counter & 0xFF]) + b"\x00" * 10
        stream = encryptor.update(block_in)
        take = min(16, len(out) - offset)
        for idx in range(take):
            out[offset + idx] ^= stream[idx]
        offset += take
        counter += 1
    encryptor.finalize()
    return bytes(out)


def mac_tag(key16: bytes, msg_id: int, tlv_type: int, value: bytes) -> bytes:
    data = bytes([msg_id & 0xFF, tlv_type & 0xFF]) + value
    return hmac.new(key16, data, sha256).digest()[:SECURE_TLV_MAC_BYTES]


def encode_can_frame_value(can_id: int, data: Iterable[int], *, extended: bool = True) -> bytes:
    payload = bytes(int(b) & 0xFF for b in data)[:8]
    dlc = len(payload)
    value = bytearray(CAN_FRAME_VALUE_LEN)
    value[0] = 1 if extended else 0
    struct.pack_into("<I", value, 1, can_id & 0x1FFFFFFF)
    value[5] = dlc
    value[6 : 6 + dlc] = payload
    return bytes(value)


def decode_can_frame_value(value: bytes) -> tuple[int, bool, bytes]:
    if len(value) < 6:
        raise ValueError("CAN frame TLV value too short")
    extended = (value[0] & 1) != 0
    can_id = struct.unpack_from("<I", value, 1)[0] & 0x1FFFFFFF
    dlc = min(8, value[5])
    data = bytes(value[6 : 6 + dlc])
    return can_id, extended, data


def is_plaintext_bootstrap_tx(can_id: int, data: bytes, *, module_has_master_key: bool = False) -> bool:
    if can_id == CAN_ID_DEVICE_INFO:
        return True
    if can_id == CAN_ID_SHUTTER_CMD:
        return True
    if can_id in (
        CAN_ID_BUTTON_BIND_EVENT,
        CAN_ID_RELAY_BIND_EVENT,
        CAN_ID_BINARY_BIND_EVENT,
        CAN_ID_SHUTTER_BIND_EVENT,
        CAN_ID_SENSOR_BIND_EVENT,
    ):
        return True
    if can_id != CAN_ID_CONFIG_REQUEST or len(data) < 2:
        return False
    cmd = data[1]
    if cmd == COMMAND_GET_SUMMARY:
        return True
    if cmd == COMMAND_SET_MODULE_ID_BY_MAC:
        return True
    if cmd in PROVISION_COMMANDS:
        if module_has_master_key and cmd in PROVISION_MASTER_KEY_WRITE_COMMANDS:
            return False
        return True
    if cmd == COMMAND_CAN_MUTE and data[0] == 0xFF:
        return True
    return False


def is_plaintext_bootstrap_rx(can_id: int, data: bytes, *, module_has_master_key: bool = False) -> bool:
    if can_id == CAN_ID_DEVICE_INFO:
        return True
    if can_id == CAN_ID_SHUTTER_CMD:
        return True
    if can_id in (
        CAN_ID_BUTTON_BIND_EVENT,
        CAN_ID_RELAY_BIND_EVENT,
        CAN_ID_BINARY_BIND_EVENT,
        CAN_ID_SHUTTER_BIND_EVENT,
        CAN_ID_SENSOR_BIND_EVENT,
    ):
        return True
    if can_id == CAN_ID_CONFIG_REQUEST and len(data) >= 2:
        cmd = data[1]
        if cmd == COMMAND_GET_SUMMARY:
            return True
        if cmd == COMMAND_SET_MODULE_ID_BY_MAC:
            return True
        if cmd in PROVISION_COMMANDS:
            if module_has_master_key and cmd in PROVISION_MASTER_KEY_WRITE_COMMANDS:
                return False
            return True
        if cmd == COMMAND_CAN_MUTE and data[0] == 0xFF:
            return True
    if can_id == CAN_ID_CONFIG_RESPONSE and len(data) >= 2:
        cmd = data[1]
        if cmd in (COMMAND_GET_SUMMARY, COMMAND_SET_MODULE_ID_BY_MAC):
            return True
        if cmd in PROVISION_COMMANDS:
            if module_has_master_key and cmd in PROVISION_MASTER_KEY_WRITE_COMMANDS:
                return False
            return True
    if can_id == CAN_ID_SECURE_TLV_REQUEST:
        return True
    return False


@dataclass
class _RxAssembly:
    msg_id: int = 0
    segment_count: int = 0
    ciphertext: bytearray = field(default_factory=lambda: bytearray(64))
    received_mask: int = 0


@dataclass
class SecureCanTransport:
    master_key: bytes
    module_macs: dict[int, bytes] = field(default_factory=dict)
    unique_macs: dict[bytes, int] = field(default_factory=dict)
    _tx_msg_id: int = 0
    _rx_by_peer: dict[int, _RxAssembly] = field(default_factory=dict)

    def register_mac(self, module_id: int, mac_bytes: bytes) -> None:
        mac_b = bytes(mac_bytes)
        if len(mac_b) != 6:
            return
        self.module_macs[int(module_id)] = mac_b
        self.unique_macs[mac_b] = int(module_id)

    def candidate_macs(self, peer_module_id: int) -> list[bytes]:
        macs: list[bytes] = []
        direct = self.module_macs.get(int(peer_module_id))
        if direct is not None:
            macs.append(direct)
        for mac_b in self.unique_macs.keys():
            if mac_b not in macs:
                macs.append(mac_b)
        return macs

    def node_key(self, module_id: int) -> Optional[bytes]:
        mac = self.module_macs.get(int(module_id))
        if mac is None and len(self.unique_macs) == 1:
            mac = next(iter(self.unique_macs.keys()))
        if mac is None:
            return None
        return derive_node_key(self.master_key, mac)

    def _next_msg_id(self) -> int:
        self._tx_msg_id = (self._tx_msg_id + 1) & 0xFF
        if self._tx_msg_id == 0:
            self._tx_msg_id = 1
        return self._tx_msg_id

    def build_secure_segments(
        self,
        *,
        peer_module_id: int,
        secure_can_id: int,
        tlv_type: int,
        value: bytes,
    ) -> Optional[list[tuple[int, list[int]]]]:
        key16 = self.node_key(peer_module_id)
        if key16 is None:
            return None
        msg_id = self._next_msg_id()
        tag = mac_tag(key16, msg_id, tlv_type, value)
        clear = bytes([tlv_type, len(value)]) + value + tag
        cipher = xor_crypt(key16, msg_id, clear)
        seg_count = (len(cipher) + SECURE_TLV_CHUNK_BYTES - 1) // SECURE_TLV_CHUNK_BYTES
        frames: list[tuple[int, list[int]]] = []
        for seg_idx in range(seg_count):
            offset = seg_idx * SECURE_TLV_CHUNK_BYTES
            chunk = bytearray(cipher[offset : offset + SECURE_TLV_CHUNK_BYTES])
            if len(chunk) < SECURE_TLV_CHUNK_BYTES:
                chunk.extend(b"\x00" * (SECURE_TLV_CHUNK_BYTES - len(chunk)))
            payload = [peer_module_id & 0xFF, msg_id, seg_idx, seg_count] + list(chunk)
            frames.append((secure_can_id, payload))
        return frames

    def wrap_outgoing(
        self, target_module_id: int, can_id: int, data: Iterable[int], *, extended: bool = True
    ) -> Optional[list[tuple[int, list[int]]]]:
        raw = bytes(int(b) & 0xFF for b in data)
        if is_plaintext_bootstrap_tx(can_id, raw):
            return [(can_id, list(raw) + [0] * (8 - len(raw)))]
        value = encode_can_frame_value(can_id, raw, extended=extended)
        return self.build_secure_segments(
            peer_module_id=target_module_id,
            secure_can_id=CAN_ID_SECURE_TLV_REQUEST,
            tlv_type=SECURE_TLV_TYPE_CAN_FRAME,
            value=value,
        )

    def build_secure_config_request(
        self, target_module_id: int, payload: Iterable[int]
    ) -> Optional[list[tuple[int, list[int]]]]:
        """Native SECURE_TLV CONFIG_REQUEST (0x730), same path as GUI send_request."""
        raw = bytes(int(b) & 0xFF for b in payload)
        if len(raw) > 8:
            raw = raw[:8]
        value = raw.ljust(8, b"\x00")
        return self.build_secure_segments(
            peer_module_id=target_module_id,
            secure_can_id=CAN_ID_SECURE_TLV_REQUEST,
            tlv_type=SECURE_TLV_TYPE_CONFIG_REQUEST,
            value=value,
        )

    def _decode_secure_assembly(
        self, peer_module_id: int, msg_id: int, asm: _RxAssembly
    ) -> Optional[tuple[int, bool, bytes, bytes]]:
        cipher_len = asm.segment_count * SECURE_TLV_CHUNK_BYTES
        cipher_blob = bytes(asm.ciphertext[:cipher_len])
        for mac_b in self.candidate_macs(peer_module_id):
            key16 = derive_node_key(self.master_key, mac_b)
            plain = xor_crypt(key16, msg_id, cipher_blob)
            if len(plain) < 2 + SECURE_TLV_MAC_BYTES:
                continue
            tlv_type = plain[0]
            tlv_len = plain[1]
            if tlv_type not in (
                SECURE_TLV_TYPE_CONFIG_REQUEST,
                SECURE_TLV_TYPE_CONFIG_RESPONSE,
                SECURE_TLV_TYPE_CAN_FRAME,
            ):
                continue
            required = 2 + tlv_len + SECURE_TLV_MAC_BYTES
            if required > len(plain):
                continue
            value = plain[2 : 2 + tlv_len]
            rx_mac = plain[2 + tlv_len : required]
            if not hmac.compare_digest(rx_mac, mac_tag(key16, msg_id, tlv_type, value)):
                continue
            self.module_macs[int(peer_module_id)] = mac_b
            self.unique_macs[mac_b] = int(peer_module_id)
            if tlv_type in (SECURE_TLV_TYPE_CONFIG_REQUEST, SECURE_TLV_TYPE_CONFIG_RESPONSE):
                inner_id = (
                    CAN_ID_CONFIG_REQUEST
                    if tlv_type == SECURE_TLV_TYPE_CONFIG_REQUEST
                    else CAN_ID_CONFIG_RESPONSE
                )
                inner = value + bytes(8 - len(value))
                return inner_id, True, inner[:8], mac_b
            can_id, ext, payload = decode_can_frame_value(value)
            return can_id, ext, payload, mac_b
        return None

    def ingest_secure_segment(
        self, peer_module_id: int, secure_can_id: int, data: bytes
    ) -> Optional[tuple[int, bool, bytes]]:
        if len(data) < 4:
            return None
        if secure_can_id not in (CAN_ID_SECURE_TLV_REQUEST, CAN_ID_SECURE_TLV_RESPONSE):
            return None
        msg_id = data[1]
        seg_idx = data[2]
        seg_count = data[3]
        if seg_count == 0 or seg_idx >= seg_count:
            return None

        asm = self._rx_by_peer.get(peer_module_id)
        if (
            asm is None
            or asm.msg_id != msg_id
            or asm.segment_count != seg_count
            or (seg_idx == 0 and asm.received_mask != 0)
        ):
            asm = _RxAssembly(msg_id=msg_id, segment_count=seg_count)
            self._rx_by_peer[peer_module_id] = asm

        offset = seg_idx * SECURE_TLV_CHUNK_BYTES
        asm.ciphertext[offset : offset + SECURE_TLV_CHUNK_BYTES] = data[4:8]
        asm.received_mask |= 1 << seg_idx
        expected = (1 << seg_count) - 1
        if asm.received_mask != expected:
            return None

        decoded = self._decode_secure_assembly(peer_module_id, msg_id, asm)
        self._rx_by_peer.pop(peer_module_id, None)
        if decoded is None:
            return None
        inner_id, ext, payload, _mac = decoded
        return inner_id, ext, payload

    def unwrap_incoming(
        self, can_id: int, data: bytes, *, default_peer: Optional[int] = None
    ) -> Optional[tuple[int, bool, bytes]]:
        raw = bytes(data[:8])
        peer = default_peer if default_peer is not None else (raw[0] if raw else 0)
        if can_id in (CAN_ID_SECURE_TLV_REQUEST, CAN_ID_SECURE_TLV_RESPONSE):
            return self.ingest_secure_segment(peer, can_id, raw)
        # Encrypted config always uses 0x731; 0x701/0x711 are always plaintext on the wire.
        if can_id in (CAN_ID_DEVICE_INFO, CAN_ID_CONFIG_RESPONSE):
            return can_id, True, raw
        if can_id in PLAINTEXT_TELEMETRY_CAN_IDS:
            return can_id, True, raw
        if is_plaintext_bootstrap_rx(can_id, raw):
            return can_id, True, raw
        return None
