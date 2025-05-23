import os
import json
from datetime import datetime, timedelta, timezone
from web3 import Web3
from web3.exceptions import (
    BadFunctionCallOutput, ContractLogicError, TransactionNotFound, TimeExhausted,
    TooManyRequests, ValidationError, ContractCustomError
)
from dotenv import load_dotenv
import math
import shutil
import tempfile
import time
import requests

load_dotenv()

ARBITRUM_RPC_URL = os.getenv('ARBITRUM_RPC')
CONFIG_FILE_POSITIONS = "positions_to_track.txt"
PRICE_TICKS_FILE = "price_ticks.json"
MAX_AGE_DAYS = 30 
WETH_WBTC_005_POOL_ADDRESS_ARBITRUM = "0x2f5e87C9312fa29aed5c179E456625D79015299c" 

UNISWAP_V3_POOL_ABI_MINIMAL = json.loads("""
[
    {"inputs": [],"name": "slot0","outputs": [{"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},{"internalType": "int24", "name": "tick", "type": "int24"},{"internalType": "uint16", "name": "observationIndex", "type": "uint16"},{"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},{"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},{"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},{"internalType": "bool", "name": "unlocked", "type": "bool"}],"stateMutability": "view","type": "function"},
    {"inputs": [],"name": "token0","outputs": [{"internalType": "address", "name": "", "type": "address"}],"stateMutability": "view","type": "function"},
    {"inputs": [],"name": "token1","outputs": [{"internalType": "address", "name": "", "type": "address"}],"stateMutability": "view","type": "function"}
]
""")
ERC20_ABI_MINIMAL = json.loads("""
[
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"payable":false,"stateMutability":"view","type":"function"},
    {"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"payable":false,"stateMutability":"view","type":"function"}
]
""")

def w3_call_with_retry(func, retries=3, delay=5, allowed_exceptions=None):
    if allowed_exceptions is None:
        allowed_exceptions = (
            requests.exceptions.ConnectionError, requests.exceptions.Timeout, # Obwohl requests hier nicht direkt verwendet wird, sind die Fehlerklassen nützlich
            ConnectionError, TimeoutError, TooManyRequests,
        )
    for attempt in range(retries):
        try:
            return func()
        except allowed_exceptions as e:
            print(f"    Web3 call failed (Attempt {attempt + 1}/{retries}) with allowed error: {type(e).__name__} - {e}")
            if attempt < retries - 1: 
                print(f"    Retrying in {delay} seconds...")
                time.sleep(delay)
            else: 
                print(f"    All Web3 call retries failed for {func.__name__ if hasattr(func, '__name__') else 'lambda function'}.")
        except (ContractLogicError, BadFunctionCallOutput, ValidationError, ContractCustomError) as contract_err:
            print(f"    Web3 contract related error (no retry): {type(contract_err).__name__} - {contract_err}")
            return None 
        except Exception as e:
            print(f"    Unexpected error during Web3 call (Attempt {attempt + 1}/{retries}): {type(e).__name__} - {e}")
            if attempt < retries - 1: 
                print(f"    Retrying in {delay} seconds for unexpected error...")
                time.sleep(delay)
            else: 
                print(f"    All Web3 call retries failed after unexpected error for {func.__name__ if hasattr(func, '__name__') else 'lambda function'}.")
    return None

def load_json_data(filename=PRICE_TICKS_FILE):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                content = json.load(f)
                if isinstance(content, list): return content
                else: print(f"Warnung: {filename} nicht Liste."); return []
        except json.JSONDecodeError: print(f"Warnung: JSON aus {filename} nicht dekodierbar."); return []
        except Exception as e: print(f"Fehler beim Laden von {filename}: {e}."); return []
    return []

def save_json_data_safely(data, filename=PRICE_TICKS_FILE):
    try:
        file_dir = os.path.dirname(os.path.abspath(filename)); file_dir = file_dir if file_dir else '.'
        if not os.path.exists(file_dir): os.makedirs(file_dir, exist_ok=True)
        temp_file_descriptor, temp_filepath = tempfile.mkstemp(suffix='.tmp', prefix=os.path.basename(filename) + '-', dir=file_dir)
        print(f"  Schreibe Daten sicher nach: {filename} (via {temp_filepath})")
        with os.fdopen(temp_file_descriptor, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2)
        shutil.move(temp_filepath, filename)
        print(f"  Daten erfolgreich nach {filename} gespeichert.")
    except Exception as e:
        print(f"  FEHLER beim sicheren Speichern nach {filename}: {e}")
        if 'temp_filepath' in locals() and os.path.exists(temp_filepath):
            try: os.remove(temp_filepath); print(f"  Temporäre Datei {temp_filepath} gelöscht.")
            except Exception as e_remove: print(f"  Fehler beim Löschen der temp. Datei {temp_filepath}: {e_remove}")

def get_active_position_id(filename=CONFIG_FILE_POSITIONS):
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'): continue
                    parts = line.split(',')
                    if len(parts) >= 1: 
                        try: return int(parts[0].strip())
                        except ValueError: continue 
        return None
    except Exception as e: print(f"Fehler beim Lesen der Position ID aus '{filename}': {e}"); return None

def sqrt_price_x96_to_price(sqrt_p, t0_dec, t1_dec, is_t0_base=False):
    pr = (sqrt_p / (2**96))**2; return pr / (10**(t1_dec-t0_dec)) if is_t0_base else (1/pr) / (10**(t0_dec-t1_dec))

def main():
    print(f"--- Starting Price Updater ({datetime.now(timezone.utc).isoformat()}) ---")

    if not ARBITRUM_RPC_URL: print("CRITICAL Error: Missing ARBITRUM_RPC. Exiting."); return
    w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
    if not w3.is_connected(): print(f"CRITICAL Error: Not connected to RPC. Exiting."); return

    active_nft_id = get_active_position_id()
    if not active_nft_id: print("Info: Keine aktive Position. Price Updater beendet."); return
    print(f"Aktive Position ID: {active_nft_id}")

    t0_dec, t1_dec = None, None
    base_sym, quote_sym = "TOKEN1", "TOKEN0"
    PRICE_BASE_IS_T0 = True 

    try:
        pool_c_tokens = w3.eth.contract(address=WETH_WBTC_005_POOL_ADDRESS_ARBITRUM, abi=UNISWAP_V3_POOL_ABI_MINIMAL)
        
        t0_addr = w3_call_with_retry(lambda: pool_c_tokens.functions.token0().call())
        if t0_addr is None: raise Exception("Konnte token0 Adresse vom Pool nicht abrufen.")
        t1_addr = w3_call_with_retry(lambda: pool_c_tokens.functions.token1().call())
        if t1_addr is None: raise Exception("Konnte token1 Adresse vom Pool nicht abrufen.")

        t0_c = w3.eth.contract(address=t0_addr, abi=ERC20_ABI_MINIMAL)
        t1_c = w3.eth.contract(address=t1_addr, abi=ERC20_ABI_MINIMAL)
        
        t0_dec = w3_call_with_retry(lambda: t0_c.functions.decimals().call())
        if t0_dec is None: raise Exception(f"Konnte Decimals für Token0 {t0_addr} nicht abrufen.")
        t1_dec = w3_call_with_retry(lambda: t1_c.functions.decimals().call())
        if t1_dec is None: raise Exception(f"Konnte Decimals für Token1 {t1_addr} nicht abrufen.")
        
        t0_s = w3_call_with_retry(lambda: t0_c.functions.symbol().call()) or "T0S_ERR"
        t1_s = w3_call_with_retry(lambda: t1_c.functions.symbol().call()) or "T1S_ERR"

        if PRICE_BASE_IS_T0: base_sym, quote_sym = t0_s, t1_s
        else: base_sym, quote_sym = t1_s, t0_s
        print(f"  Token Details: {t0_s}({t0_dec}) / {t1_s}({t1_dec})")
    except Exception as e:
        print(f"Fehler Holen Token-Details: {e}")
        if WETH_WBTC_005_POOL_ADDRESS_ARBITRUM.lower()=="0x2f5e87C9312fa29aed5c179E456625D79015299c".lower():
            t0_dec,t1_dec,t0_s,t1_s = 8,18,"WBTC","WETH"
            if PRICE_BASE_IS_T0: base_sym,quote_sym = t0_s,t1_s
            else: base_sym,quote_sym = t1_s,t0_s
            print(f"  Fallback Token Details: {t0_s}({t0_dec}) / {t1_s}({t1_dec})")
        else: print("Unbekannter Pool für Fallback. Breche ab."); return
    if t0_dec is None or t1_dec is None: print("Token-Dezimalstellen nicht ermittelt. Breche ab."); return

    curr_mkt_price = None
    try:
        pool_c = w3.eth.contract(address=WETH_WBTC_005_POOL_ADDRESS_ARBITRUM, abi=UNISWAP_V3_POOL_ABI_MINIMAL)
        slot0 = w3_call_with_retry(lambda: pool_c.functions.slot0().call())
        if slot0 is None: raise Exception(f"Konnte slot0 vom Pool {WETH_WBTC_005_POOL_ADDRESS_ARBITRUM} nicht abrufen.")
        curr_mkt_price = sqrt_price_x96_to_price(slot0[0], t0_dec, t1_dec, PRICE_BASE_IS_T0)
        print(f"  Aktueller Marktpreis: {curr_mkt_price:.6f} {quote_sym}/{base_sym}")
    except Exception as e: print(f"Fehler Abrufen Marktpreis: {e}"); return 
    if curr_mkt_price is None: print("Marktpreis nicht ermittelt. Kein Update."); return

    new_entry = {"timestamp":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"), "price":curr_mkt_price, "base_token":base_sym, "quote_token":quote_sym}
    price_data = load_json_data(PRICE_TICKS_FILE)
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    
    filtered_ticks = []
    for entry in price_data:
        try:
            entry_ts_str = entry["timestamp"]
            entry_ts = datetime.fromisoformat(entry_ts_str.replace("Z", "+00:00"))
            if entry_ts >= cutoff:
                filtered_ticks.append(entry)
        except Exception as e:
            print(f"Warnung: Konnte Timestamp '{entry.get('timestamp')}' nicht parsen oder vergleichen: {e}. Eintrag wird ignoriert.")
            continue
            
    filtered_ticks.append(new_entry)
    save_json_data_safely(filtered_ticks, PRICE_TICKS_FILE)
    print(f"--- Price Updater Finished ---")

if __name__ == "__main__":
    main()