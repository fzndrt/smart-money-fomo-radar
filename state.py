"""
state.py - State Tracker Token Alerted & Anti-Duplikasi
"""
import time
from typing import Set

_alerted_mints: Set[str] = set()

def already_alerted(mint: str) -> bool:
    """Memeriksa apakah token mint ini sudah pernah dikirimi sinyal alert."""
    return mint in _alerted_mints

def mark_alerted(mint: str):
    """Menandai token mint agar tidak terjadi spam alert berulang."""
    _alerted_mints.add(mint)

def get_alerted_count() -> int:
    """Mengembalikan jumlah token unik yang telah dialert."""
    return len(_alerted_mints)
