"""Spawn-safe wire definitions. Importing this module never loads the application.

Each round has a separate monotonic mirror sequence (statistics tokens do not
advance it). The header is (sequence, full, now, optional_ids, array('d'),
optional_catalog). Directory frames carry (size, delta_chunks, opportunity_chunks);
quote frames carry delta_chunks. A delta row is (slot, immutable_field_tuple).
Slots are directory handles, not normalized tickers: each row retains exchange,
market type, raw symbol, DEX and both multipliers. A changed/reused slot invalidates
its quote. Truncation deletes all trailing slots. Full resets only the mirrors,
never the ledger. Only an acknowledged contiguous sequence permits another delta.
"""
import os
import sys
from collections import namedtuple
from operator import itemgetter

CAPTURE_CHUNK_SIZE = 500

MARKET_FIELDS = "exchange market_type raw_symbol dex symbol_alias_price_multiplier contract_size_multiplier timestamp upstream_timestamp bid ask quote_kind funding_next_time funding_rate_pct funding_interval_hours asset_id symbol identity_collision estimated_fields".split()
OP_FIELDS = "id symbol open_spread_pct buy_exchange buy_market_type buy_raw_symbol buy_dex buy_price_multiplier buy_contract_size_multiplier sell_exchange sell_market_type sell_raw_symbol sell_dex sell_price_multiplier sell_contract_size_multiplier buy_volume_24h_usdt sell_volume_24h_usdt buy_fee_pct sell_fee_pct fake_score fake_flag executable buy_routes sell_routes fake_reasons".split()
MarketInput = namedtuple("MarketInput", MARKET_FIELDS)
OpportunityInput = namedtuple("OpportunityInput", OP_FIELDS)
DIRECTORY_FIELDS = "exchange market_type raw_symbol dex symbol_alias_price_multiplier contract_size_multiplier asset_id symbol".split()
MarketDirectory = namedtuple("MarketDirectory", DIRECTORY_FIELDS)
directory_values = itemgetter(*DIRECTORY_FIELDS)
market_values = itemgetter(*MARKET_FIELDS)
op_values = itemgetter(*OP_FIELDS)


def rss_mb(pid):
    """Current resident set, including idle/hung children; no optional dependency."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
                        *[(str(i), ctypes.c_size_t) for i in range(9)]]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
        handle = kernel.OpenProcess(0x1000 | 0x10, False, pid)
        if not handle:
            raise OSError(ctypes.get_last_error(), "cannot inspect shadow child RSS")
        try:
            info = Counters()
            info.cb = ctypes.sizeof(info)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(info), info.cb):
                raise OSError(ctypes.get_last_error(), "cannot read shadow child RSS")
            return getattr(info, "1") / 1024 / 1024
        finally:
            kernel.CloseHandle(handle)
    with open(f"/proc/{pid}/statm", encoding="ascii") as source:
        resident = int(source.read().split()[1])
    return resident * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024


def child_entry(settings, incoming, outgoing, stop):
    # The spawn target is a module-level function; never pickle Settings or an
    # application instance (which would import parent application dependencies).
    from app.services.shadow_worker import run_child
    run_child(settings, incoming, outgoing, stop)
