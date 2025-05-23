# price_updater.py
import os
import json
from datetime import datetime, timedelta, timezone
from web3 import Web3
from dotenv import load_dotenv
import math
import shutil
import tempfile

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

def save_json_data_safely(data, filename=PRICE_TICKS_FILE):
    try:
        file_dir = os.path.dirname(os.path.abspath(filename))
        if not file_dir: 
            file_dir = '.'
        if not os.path.exists(file_dir):
             os.makedirs(file_dir, exist_ok=True)

        temp_file_descriptor, temp_filepath = tempfile.mkstemp(suffix='.tmp', prefix=os.path.basename(filename) + '-', dir=file_dir)
        
        print(f"  Schreibe Daten sicher nach: {filename} (via {temp_filepath})")

        with os.fdopen(temp_file_descriptor, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        
        shutil.move(temp_filepath, filename) 
        print(f"  Daten erfolgreich nach {filename} gespeichert.")

    except Exception as e:
        print(f"  FEHLER beim sicheren Speichern von Daten nach {filename}: {e}")
        if 'temp_filepath' in locals() and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
                print(f"  Temporäre Datei {temp_filepath} wurde nach Fehler gelöscht.")
            except Exception as e_remove:
                print(f"  Fehler beim Löschen der temporären Datei {temp_filepath}: {e_remove}")

def get_active_position_id(filename=CONFIG_FILE_POSITIONS):
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'): continue
                    parts = line.split(',')
                    if len(parts) >= 1: 
                        try:
                            return int(parts[0].strip())
                        except ValueError:
                            continue 
        return None
    except Exception as e:
        print(f"Fehler beim Lesen der Position ID aus '{filename}': {e}")
        return None

def sqrt_price_x96_to_price(sqrt_price_x96, token0_decimals, token1_decimals, is_token0_base=False):
    price_ratio = (sqrt_price_x96 / (2**96)) ** 2
    if is_token0_base:
        return price_ratio / (10 ** (token1_decimals - token0_decimals))
    else:
        return (1 / price_ratio) / (10 ** (token0_decimals - token1_decimals))

def main():
    print(f"--- Starting Price Updater ({datetime.now(timezone.utc).isoformat()}) ---")

    if not ARBITRUM_RPC_URL:
        print("CRITICAL Error: Missing ARBITRUM_RPC environment variable. Exiting.")
        return

    w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
    if not w3.is_connected():
        print(f"CRITICAL Error: Could not connect to Arbitrum RPC. Exiting.")
        return

    active_nft_id = get_active_position_id()
    if not active_nft_id:
        print("Info: Keine aktive Position in Config gefunden. Price Updater beendet.")
        return
    
    print(f"Aktive Position ID: {active_nft_id}")

    token0_decimals, token1_decimals = None, None
    base_token_symbol, quote_token_symbol = "TOKEN1", "TOKEN0" 
    PRICE_PRESENTATION_IS_TOKEN0_BASE = True 

    try:
        pool_contract_for_tokens = w3.eth.contract(address=WETH_WBTC_005_POOL_ADDRESS_ARBITRUM, abi=UNISWAP_V3_POOL_ABI_MINIMAL)
        token0_address = pool_contract_for_tokens.functions.token0().call()
        token1_address = pool_contract_for_tokens.functions.token1().call()

        token0_contract = w3.eth.contract(address=token0_address, abi=ERC20_ABI_MINIMAL)
        token1_contract = w3.eth.contract(address=token1_address, abi=ERC20_ABI_MINIMAL)
        
        token0_decimals = token0_contract.functions.decimals().call()
        token1_decimals = token1_contract.functions.decimals().call()
        t0_sym = token0_contract.functions.symbol().call()
        t1_sym = token1_contract.functions.symbol().call()

        if PRICE_PRESENTATION_IS_TOKEN0_BASE: 
            base_token_symbol = t0_sym
            quote_token_symbol = t1_sym
        else: 
            base_token_symbol = t1_sym
            quote_token_symbol = t0_sym
        print(f"  Token Details: {t0_sym}({token0_decimals}) / {t1_sym}({token1_decimals})")
    except Exception as e:
        print(f"Fehler beim Holen der Token-Details vom Pool: {e}")
        if WETH_WBTC_005_POOL_ADDRESS_ARBITRUM.lower() == "0x2f5e87C9312fa29aed5c179E456625D79015299c".lower():
            token0_decimals = 8 
            token1_decimals = 18
            t0_sym = "WBTC"
            t1_sym = "WETH"
            if PRICE_PRESENTATION_IS_TOKEN0_BASE: 
                base_token_symbol = t0_sym; quote_token_symbol = t1_sym
            else: 
                base_token_symbol = t1_sym; quote_token_symbol = t0_sym
            print(f"  Fallback Token Details verwendet: {t0_sym}({token0_decimals}) / {t1_sym}({token1_decimals})")
        else:
            print("Unbekannter Pool für Fallback-Token-Details. Breche ab.")
            return

    if token0_decimals is None or token1_decimals is None:
        print("Fehler: Konnte Token-Dezimalstellen endgültig nicht ermitteln. Breche ab.")
        return

    current_market_price = None
    try:
        pool_contract = w3.eth.contract(address=WETH_WBTC_005_POOL_ADDRESS_ARBITRUM, abi=UNISWAP_V3_POOL_ABI_MINIMAL)
        slot0 = pool_contract.functions.slot0().call()
        sqrt_price_x96_current = slot0[0]
        current_market_price = sqrt_price_x96_to_price(sqrt_price_x96_current, token0_decimals, token1_decimals, PRICE_PRESENTATION_IS_TOKEN0_BASE)
        print(f"  Aktueller Marktpreis: {current_market_price:.6f} {quote_token_symbol}/{base_token_symbol}")
    except Exception as e:
        print(f"Fehler beim Abrufen des Marktpreises: {e}")
        return 

    if current_market_price is None:
        print("Konnte Marktpreis nicht ermitteln. Kein Update für price_ticks.json.")
        return

    new_price_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "price": current_market_price,
        "base_token": base_token_symbol,
        "quote_token": quote_token_symbol
    }

    price_ticks_data = load_json_data(PRICE_TICKS_FILE)

    cutoff_date_utc = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    
    filtered_price_ticks = []
    for entry in price_ticks_data:
        try:
            entry_ts_str = entry["timestamp"]
            entry_ts = datetime.fromisoformat(entry_ts_str.replace("Z", "+00:00"))
            if entry_ts >= cutoff_date_utc:
                filtered_price_ticks.append(entry)
        except Exception as e:
            print(f"Warnung: Konnte Timestamp '{entry.get('timestamp')}' nicht parsen oder vergleichen: {e}. Eintrag wird ignoriert.")
            continue

    filtered_price_ticks.append(new_price_entry)
    
    save_json_data_safely(filtered_price_ticks, PRICE_TICKS_FILE)

    print(f"--- Price Updater Finished ---")

if __name__ == "__main__":
    main()