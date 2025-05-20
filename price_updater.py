# price_updater.py
import os
import json
from datetime import datetime, timedelta, timezone
from web3 import Web3
from dotenv import load_dotenv
import math

load_dotenv()

# --- Konfiguration ---
ARBITRUM_RPC_URL = os.getenv('ARBITRUM_RPC') # Brauchst du für den Pool-Call
CONFIG_FILE_POSITIONS = "positions_to_track.txt" # Um die aktive Position zu kennen
FEES_DATA_FILE = "fees_data.json" # Um Token-Details der aktiven Position zu lesen
PRICE_TICKS_FILE = "price_ticks.json" # Zieldatei für Preis-Ticks
MAX_AGE_DAYS = 30 # Daten maximal 30 Tage aufbewahren

# DEINE SPEZIFISCHE POOL ADRESSE (WETH/WBTC 0.05% auf Arbitrum)
# Diese sollte mit der deiner aktiven Position übereinstimmen.
# Für mehr Flexibilität könnte man dies auch aus fees_data.json oder einer Config lesen.
WETH_WBTC_005_POOL_ADDRESS_ARBITRUM = "0x2f5e87C9312fa29aed5c179E456625D79015299c" 

# Minimal ABI für Uniswap V3 Pool, um slot0() aufzurufen
UNISWAP_V3_POOL_ABI_MINIMAL = json.loads("""
[
    {"inputs": [],"name": "slot0","outputs": [{"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},{"internalType": "int24", "name": "tick", "type": "int24"}, ...],"stateMutability": "view","type": "function"},
    {"inputs": [],"name": "token0","outputs": [{"internalType": "address", "name": "", "type": "address"}],"stateMutability": "view","type": "function"},
    {"inputs": [],"name": "token1","outputs": [{"internalType": "address", "name": "", "type": "address"}],"stateMutability": "view","type": "function"}
]
""") # Minimal ABI, nur relevante Teile für slot0, token0, token1

# Minimal ABI für ERC20 (nur decimals und symbol)
ERC20_ABI_MINIMAL = json.loads("""
[
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"payable":false,"stateMutability":"view","type":"function"},
    {"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"payable":false,"stateMutability":"view","type":"function"}
]
""")


def get_active_position_id(filename=CONFIG_FILE_POSITIONS):
    # Vereinfachte Funktion, die nur die ID der ersten gültigen Zeile zurückgibt
    try:
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'): continue
                    parts = line.split(',')
                    if len(parts) >= 1: # Brauchen mindestens die ID
                        try:
                            return int(parts[0].strip())
                        except ValueError:
                            continue # Ungültige ID, nächste Zeile versuchen
        return None
    except Exception as e:
        print(f"Fehler beim Lesen der Position ID aus '{filename}': {e}")
        return None

def sqrt_price_x96_to_price(sqrt_price_x96, token0_decimals, token1_decimals, is_token0_base=False):
    # is_token0_base=False -> Preis von Token0 in Einheiten von Token1 (z.B. WBTC pro WETH)
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

    # Lese Token-Details (Symbole, Dezimalstellen) aus fees_data.json für die aktive Position
    # Dies vermeidet zusätzliche RPC-Calls für Token-Verträge hier.
    # Alternativ: Diese Details könnten auch aus einer zentraleren Config oder dem Pool-Vertrag selbst kommen.
    token0_decimals, token1_decimals = None, None
    base_token_symbol, quote_token_symbol = "TOKEN1", "TOKEN0" # Standard, falls nicht gefunden
    PRICE_PRESENTATION_IS_TOKEN0_BASE = False # Standard: Preis von Token0 pro Token1

    if os.path.exists(FEES_DATA_FILE):
        with open(FEES_DATA_FILE, 'r') as f:
            all_fees_data = json.load(f)
            position_key = f"position_{active_nft_id}"
            if position_key in all_fees_data and all_fees_data[position_key].get("is_active"):
                pos_data = all_fees_data[position_key]
                # Versuche, Dezimalstellen und Symbole aus dem neuesten History-Eintrag zu bekommen,
                # oder von der Positionsebene, falls dort gespeichert.
                # Für dieses Skript ist es am einfachsten, wenn sie auf Positionsebene stehen.
                # Annahme: tracker.py speichert token_pair_symbols auf Positionsebene.
                # Und wir brauchen die Dezimalstellen, die nicht direkt in fees_data.json stehen.
                # Wir müssen sie vom Pool holen oder annehmen.
                # Für WBTC/WETH: WBTC (Token0) = 8, WETH (Token1) = 18
                # Besser: Hole sie direkt vom Pool-Vertrag einmalig hier.
                
                pool_contract_for_tokens = w3.eth.contract(address=WETH_WBTC_005_POOL_ADDRESS_ARBITRUM, abi=UNISWAP_V3_POOL_ABI_MINIMAL)
                token0_address = pool_contract_for_tokens.functions.token0().call()
                token1_address = pool_contract_for_tokens.functions.token1().call()

                token0_contract = w3.eth.contract(address=token0_address, abi=ERC20_ABI_MINIMAL)
                token1_contract = w3.eth.contract(address=token1_address, abi=ERC20_ABI_MINIMAL)
                
                token0_decimals = token0_contract.functions.decimals().call()
                token1_decimals = token1_contract.functions.decimals().call()
                t0_sym = token0_contract.functions.symbol().call()
                t1_sym = token1_contract.functions.symbol().call()

                if PRICE_PRESENTATION_IS_TOKEN0_BASE: # Preis von Token1 in Token0
                    base_token_symbol = t0_sym
                    quote_token_symbol = t1_sym
                else: # Preis von Token0 in Token1
                    base_token_symbol = t1_sym
                    quote_token_symbol = t0_sym
                print(f"  Token Details: {t0_sym}({token0_decimals}) / {t1_sym}({token1_decimals})")


    if token0_decimals is None or token1_decimals is None:
        print("Fehler: Konnte Token-Dezimalstellen nicht ermitteln. Verwende Standardwerte oder breche ab.")
        # Fallback oder Abbruch hier, für WBTC/WETH kennen wir sie:
        if base_token_symbol.upper() == "WETH" and quote_token_symbol.upper() == "WBTC": # WBTC pro WETH
            token0_decimals = 8  # WBTC
            token1_decimals = 18 # WETH
        elif base_token_symbol.upper() == "WBTC" and quote_token_symbol.upper() == "WETH": # WETH pro WBTC
            token0_decimals = 8  # WBTC
            token1_decimals = 18 # WETH
        else:
            print("Unbekanntes Token-Paar für Fallback-Dezimalstellen. Breche ab.")
            return


    # Aktuellen Marktpreis vom Pool abrufen
    current_market_price = None
    try:
        pool_contract = w3.eth.contract(address=WETH_WBTC_005_POOL_ADDRESS_ARBITRUM, abi=UNISWAP_V3_POOL_ABI_MINIMAL)
        slot0 = pool_contract.functions.slot0().call()
        sqrt_price_x96_current = slot0[0]
        current_market_price = sqrt_price_x96_to_price(sqrt_price_x96_current, token0_decimals, token1_decimals, PRICE_PRESENTATION_IS_TOKEN0_BASE)
        print(f"  Aktueller Marktpreis: {current_market_price:.6f} {quote_token_symbol}/{base_token_symbol}")
    except Exception as e:
        print(f"Fehler beim Abrufen des Marktpreises: {e}")
        return # Ohne Preis kein Update

    if current_market_price is None:
        print("Konnte Marktpreis nicht ermitteln. Kein Update für price_ticks.json.")
        return

    # Daten für price_ticks.json vorbereiten und alte Einträge entfernen
    new_price_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), # ISO 8601 mit Z
        "price": current_market_price,
        "base_token": base_token_symbol,
        "quote_token": quote_token_symbol
    }

    price_ticks_data = []
    if os.path.exists(PRICE_TICKS_FILE):
        with open(PRICE_TICKS_FILE, 'r') as f:
            try:
                price_ticks_data = json.load(f)
                if not isinstance(price_ticks_data, list):
                    price_ticks_data = []
            except json.JSONDecodeError:
                price_ticks_data = []

    cutoff_date_utc = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    
    filtered_price_ticks = []
    for entry in price_ticks_data:
        try:
            # Stelle sicher, dass der Timestamp korrekt als UTC-aware datetime-Objekt geparst wird
            entry_ts_str = entry["timestamp"]
            if entry_ts_str.endswith("Z"):
                entry_ts = datetime.fromisoformat(entry_ts_str[:-1] + "+00:00")
            else: # Fallback falls 'Z' fehlt, aber sollte nicht passieren
                entry_ts = datetime.fromisoformat(entry_ts_str).replace(tzinfo=timezone.utc)

            if entry_ts >= cutoff_date_utc:
                filtered_price_ticks.append(entry)
        except Exception as e:
            print(f"Warnung: Konnte Timestamp '{entry.get('timestamp')}' nicht parsen oder vergleichen: {e}. Eintrag wird ignoriert.")
            continue


    filtered_price_ticks.append(new_price_entry)

    try:
        with open(PRICE_TICKS_FILE, 'w') as f:
            json.dump(filtered_price_ticks, f, indent=2)
        print(f"Preis-Tick erfolgreich in {PRICE_TICKS_FILE} gespeichert. {len(filtered_price_ticks)} Einträge.")
    except Exception as e:
        print(f"Fehler beim Speichern von {PRICE_TICKS_FILE}: {e}")

    print(f"--- Price Updater Finished ---")

if __name__ == "__main__":
    main()