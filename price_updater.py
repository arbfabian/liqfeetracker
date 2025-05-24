import os
import json
from datetime import datetime, timedelta, timezone
from web3 import Web3
from dotenv import load_dotenv
import math
# import shutil # Nicht für einfache save_json_data benötigt
# import tempfile # Nicht für einfache save_json_data benötigt
# import time # Nicht für Web3-Calls ohne Retry benötigt
# import requests # Nicht direkt hier benötigt

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

def load_json_data(filename=PRICE_TICKS_FILE):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                content = json.load(f)
                if isinstance(content, list): 
                    return content
                else: 
                    print(f"Warnung: {filename} enthält keine Liste. Starte mit leerer Liste.")
                    return []
        except json.JSONDecodeError:
            print(f"Warnung: Konnte JSON aus {filename} nicht dekodieren. Starte mit leerer Liste.")
            return [] 
        except Exception as e:
            print(f"Fehler beim Laden von {filename}: {e}. Starte mit leerer Liste.")
            return []
    return []

def save_json_data(data, filename=PRICE_TICKS_FILE): # Die ursprüngliche, einfache Speicherfunktion
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        print(f"Daten erfolgreich nach {filename} gespeichert. {len(data)} Einträge.") # Info über Anzahl Einträge
    except Exception as e:
        print(f"FEHLER beim Speichern von Daten nach {filename}: {e}")

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

def sqrt_price_x96_to_price(sqrt_p, t0_dec, t1_dec, is_t0_base=True): 
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
    PRICE_PRESENTATION_IS_TOKEN0_BASE = False # WICHTIG: Für WETH pro WBTC (Base=Token0=WBTC)
    base_sym_for_json, quote_sym_for_json = "", ""


    try:
        pool_c_tokens = w3.eth.contract(address=WETH_WBTC_005_POOL_ADDRESS_ARBITRUM, abi=UNISWAP_V3_POOL_ABI_MINIMAL)
        t0_addr = pool_c_tokens.functions.token0().call() 
        t1_addr = pool_c_tokens.functions.token1().call() 

        t0_c = w3.eth.contract(address=t0_addr, abi=ERC20_ABI_MINIMAL)
        t1_c = w3.eth.contract(address=t1_addr, abi=ERC20_ABI_MINIMAL)
        
        t0_dec = t0_c.functions.decimals().call() 
        t1_dec = t1_c.functions.decimals().call() 
        t0_s = t0_c.functions.symbol().call() or "T0S_ERR" 
        t1_s = t1_c.functions.symbol().call() or "T1S_ERR" 

        if PRICE_PRESENTATION_IS_TOKEN0_BASE: 
            base_sym_for_json = t0_s # WBTC
            quote_sym_for_json = t1_s # WETH
        else: 
            base_sym_for_json = t1_s # WETH
            quote_sym_for_json = t0_s # WBTC
        print(f"  Token Details für Preis-Ticks: Base={base_sym_for_json}, Quote={quote_sym_for_json}")
        print(f"  (Pool Token0: {t0_s}({t0_dec}) / Pool Token1: {t1_s}({t1_dec}))")
    except Exception as e:
        print(f"Fehler Holen Token-Details: {e}")
        if WETH_WBTC_005_POOL_ADDRESS_ARBITRUM.lower()=="0x2f5e87C9312fa29aed5c179E456625D79015299c".lower():
            t0_dec,t1_dec,t0_s,t1_s = 8,18,"WBTC","WETH" # WBTC ist Token0, WETH ist Token1
            if PRICE_PRESENTATION_IS_TOKEN0_BASE: base_sym_for_json,quote_sym_for_json = t0_s,t1_s
            else: base_sym_for_json,quote_sym_for_json = t1_s,t0_s
            print(f"  Fallback Token Details: Base={base_sym_for_json}, Quote={quote_sym_for_json}")
        else: print("Unbekannter Pool für Fallback. Breche ab."); return
    if t0_dec is None or t1_dec is None: print("Token-Dezimalstellen nicht ermittelt. Breche ab."); return

    curr_mkt_price = None
    try:
        pool_c = w3.eth.contract(address=WETH_WBTC_005_POOL_ADDRESS_ARBITRUM, abi=UNISWAP_V3_POOL_ABI_MINIMAL)
        slot0 = pool_c.functions.slot0().call() 
        curr_mkt_price = sqrt_price_x96_to_price(slot0[0], t0_dec, t1_dec, PRICE_PRESENTATION_IS_TOKEN0_BASE)
        print(f"  Aktueller Marktpreis: {curr_mkt_price:.6f} {quote_sym_for_json}/{base_sym_for_json}")
    except Exception as e: print(f"Fehler Abrufen Marktpreis: {e}"); return 
    if curr_mkt_price is None: print("Marktpreis nicht ermittelt. Kein Update."); return

    new_entry = {"timestamp":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"), "price":curr_mkt_price, "base_token":base_sym_for_json, "quote_token":quote_sym_for_json}
    price_data = load_json_data(PRICE_TICKS_FILE)
    cutoff_date_utc = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    
    filtered_ticks = []
    for entry in price_data:
        try:
            entry_ts_str = entry["timestamp"]
            entry_ts = datetime.fromisoformat(entry_ts_str.replace("Z", "+00:00"))
            if entry_ts >= cutoff_date_utc:
                filtered_ticks.append(entry)
        except Exception as e:
            print(f"Warnung: Konnte Timestamp '{entry.get('timestamp')}' nicht parsen oder vergleichen: {e}. Eintrag wird ignoriert.")
            continue
            
    filtered_ticks.append(new_entry)
    save_json_data(filtered_ticks, PRICE_TICKS_FILE) 
    print(f"--- Price Updater Finished ---")

if __name__ == "__main__":
    main()