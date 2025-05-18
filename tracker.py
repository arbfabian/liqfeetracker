import os
import json
from datetime import datetime, timedelta, timezone
from web3 import Web3
from dotenv import load_dotenv
import requests
# import sys # sys.exit wird nicht mehr für Config-Fehler verwendet

load_dotenv()

# --- Konfiguration ---
ARBITRUM_RPC_URL = os.getenv('ARBITRUM_RPC')
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS')
CONFIG_FILE_POSITIONS = "positions_to_track.txt" # Enthält immer nur EINE aktive Position im Format ID,INVESTMENT_USD
JSON_DATA_FILE = "fees_data.json"

# --- Konstanten ---
NFPM_ADDRESS = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
NFPM_ABI = json.loads("""
[
    {"inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],"name": "positions","outputs": [{"internalType": "uint96", "name": "nonce", "type": "uint96"},{"internalType": "address", "name": "operator", "type": "address"},{"internalType": "address", "name": "token0", "type": "address"},{"internalType": "address", "name": "token1", "type": "address"},{"internalType": "uint24", "name": "fee", "type": "uint24"},{"internalType": "int24", "name": "tickLower", "type": "int24"},{"internalType": "int24", "name": "tickUpper", "type": "int24"},{"internalType": "uint128", "name": "liquidity", "type": "uint128"},{"internalType": "uint256", "name": "feeGrowthInside0LastX128", "type": "uint256"},{"internalType": "uint256", "name": "feeGrowthInside1LastX128", "type": "uint256"},{"internalType": "uint128", "name": "tokensOwed0", "type": "uint128"},{"internalType": "uint128", "name": "tokensOwed1", "type": "uint128"}],"stateMutability": "view","type": "function"},
    {"inputs": [{"components": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"},{"internalType": "address", "name": "recipient", "type": "address"},{"internalType": "uint128", "name": "amount0Max", "type": "uint128"},{"internalType": "uint128", "name": "amount1Max", "type": "uint128"}],"internalType": "struct INonfungiblePositionManager.CollectParams","name": "params","type": "tuple"}],"name": "collect","outputs": [{"internalType": "uint256", "name": "amount0", "type": "uint256"},{"internalType": "uint256", "name": "amount1", "type": "uint256"}],"stateMutability": "payable","type": "function"}
]
""")
ERC20_ABI_MINIMAL = json.loads("""
[
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"payable":false,"stateMutability":"view","type":"function"},
    {"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"payable":false,"stateMutability":"view","type":"function"}
]
""")

def load_json_data(filename=JSON_DATA_FILE):
    if os.path.exists(filename):
        try:
            with open(filename, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: Could not decode JSON from {filename}. Starting with empty data.")
            return {}
    return {}

def save_json_data(data, filename=JSON_DATA_FILE):
    try:
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Data saved to {filename}")
    except Exception as e:
        print(f"Error saving data to {filename}: {e}")

def get_single_token_price_coingecko(contract_address, platform_id="arbitrum-one"):
    try:
        checksum_address = Web3.to_checksum_address(contract_address)
        url = f"https://api.coingecko.com/api/v3/coins/{platform_id}/contract/{checksum_address}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        price_usd = data.get("market_data", {}).get("current_price", {}).get("usd")
        if price_usd is None: print(f"    Warning: Could not parse USD price for {checksum_address} from CoinGecko. Response: {data}")
        return price_usd
    except requests.exceptions.RequestException as e: print(f"    CoinGecko API request error for {contract_address}: {e}")
    except Exception as e_gen: print(f"    Error fetching price for {contract_address}: {e_gen}")
    return None

def get_active_position_config(filename=CONFIG_FILE_POSITIONS):
    try:
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                lines = f.readlines()
                if not lines:
                    print(f"Info: Konfigurationsdatei '{filename}' ist leer.")
                    return None
                
                for line_number, line in enumerate(lines, 1):
                    line = line.strip()
                    if not line or line.startswith('#'): # Ignoriere Leerzeilen und Kommentare
                        continue
                    
                    parts = line.split(',')
                    if len(parts) == 2:
                        try:
                            position_id = int(parts[0].strip())
                            investment_usd = float(parts[1].strip())
                            print(f"Aktive Position aus '{filename}': ID {position_id}, Investment {investment_usd} USD")
                            return {'id': position_id, 'initial_investment_usd': investment_usd}
                        except ValueError:
                            print(f"Error: Ungültiges Zahlenformat in '{filename}' Zeile {line_number}: '{line}'. Erwarte ID (Integer), INVESTMENT_USD (Float). Keine aktive Position wird verarbeitet.")
                            return None # Wichtig: None zurückgeben bei Fehler
                    else:
                        print(f"Error: Ungültiges Format in '{filename}' Zeile {line_number}: '{line}'. Erwarte 'ID,INVESTMENT_USD'. Keine aktive Position wird verarbeitet.")
                        return None # Wichtig: None zurückgeben bei Fehler
                print(f"Info: Keine gültige (nicht-kommentierte) Positionszeile in '{filename}' gefunden.")
                return None # Falls nur leere/Kommentarzeilen da sind
        else:
            print(f"Info: Konfigurationsdatei '{filename}' nicht gefunden. Es wird keine Position aktiv geschaltet.")
            return None
    except Exception as e:
        print(f"Fehler beim Lesen der Konfigurationsdatei '{filename}': {e}")
        return None

def main():
    print(f"--- Starting Uniswap V3 Fee Tracker ---")
    all_data = load_json_data()

    if not ARBITRUM_RPC_URL or not WALLET_ADDRESS:
        print("CRITICAL Error: Missing environment variables (ARBITRUM_RPC, WALLET_ADDRESS). Exiting.")
        # Hier könnte man sys.exit(1) verwenden, wenn man den Workflow fehlschlagen lassen will
        return # Beendet das Skript normal

    w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
    if not w3.is_connected():
        print(f"CRITICAL Error: Could not connect to Arbitrum RPC: {ARBITRUM_RPC_URL}. Exiting.")
        # Hier könnte man sys.exit(1) verwenden
        return # Beendet das Skript normal
    print(f"Successfully connected to Arbitrum RPC.")

    nfpm_contract = w3.eth.contract(address=NFPM_ADDRESS, abi=NFPM_ABI)
    
    active_pos_config = get_active_position_config() 

    active_position_id_from_config = None
    if active_pos_config:
        active_position_id_from_config = active_pos_config['id']

    # Alle Positionen in fees_data.json durchgehen und is_active entsprechend setzen
    # Dies geschieht unabhängig davon, ob Gebühren aktualisiert werden.
    print("Aktualisiere 'is_active' Flags in fees_data.json...")
    for pos_key_in_json in list(all_data.keys()): 
        if pos_key_in_json.startswith("position_"):
            try:
                position_id_in_json = int(pos_key_in_json.replace("position_", ""))
                if active_position_id_from_config is not None and position_id_in_json == active_position_id_from_config:
                    if not all_data[pos_key_in_json].get("is_active", False): # Nur loggen wenn es sich ändert
                        print(f"  Position {pos_key_in_json} wird auf 'is_active: true' gesetzt.")
                    all_data[pos_key_in_json]["is_active"] = True
                    # Auch initial_investment_usd aus der Config übernehmen, falls es noch nicht existiert oder für die aktive Position
                    if "initial_investment_usd" not in all_data[pos_key_in_json] or all_data[pos_key_in_json]["initial_investment_usd"] != active_pos_config['initial_investment_usd']:
                        all_data[pos_key_in_json]["initial_investment_usd"] = active_pos_config['initial_investment_usd']
                        print(f"  Initialinvestment für aktive Position {pos_key_in_json} auf {active_pos_config['initial_investment_usd']} USD gesetzt/aktualisiert.")

                else:
                    if all_data[pos_key_in_json].get("is_active", True): # Nur loggen wenn es sich ändert
                        print(f"  Position {pos_key_in_json} wird auf 'is_active: false' gesetzt.")
                    all_data[pos_key_in_json]["is_active"] = False
            except ValueError:
                print(f"  Konnte ID für Key '{pos_key_in_json}' nicht parsen. Überspringe is_active Update für diesen Key.")


    if not active_pos_config:
        print("Keine aktive Position in der Konfigurationsdatei definiert oder Fehler beim Lesen. Es werden keine Gebühren aktualisiert.")
        # WICHTIG: Speichere die Daten, um die 'is_active' Flags zu aktualisieren!
        save_json_data(all_data) 
        print(f"\n--- Fee Tracker Finished (No active position fees updated) ---")
        return # Skript normal beenden

    # Ab hier wird nur die EINE aktive Position verarbeitet
    position_nft_id = active_pos_config['id']
    # initial_investment_from_config wird bereits oben beim Setzen des is_active Flags in all_data geschrieben
    position_key = f"position_{position_nft_id}"

    today_utc = datetime.now(timezone.utc)
    today_date_str = today_utc.strftime('%Y-%m-%d')
    yesterday_date_str = (today_utc - timedelta(days=1)).strftime('%Y-%m-%d')

    # Sicherstellen, dass die Position in all_data existiert (sollte durch obige Schleife der Fall sein)
    if position_key not in all_data:
        print(f"Info: Aktive Position {position_key} nicht in JSON gefunden, wird initialisiert.")
        all_data[position_key] = {
            "history": {}, 
            "is_active": True, 
            "initial_investment_usd": active_pos_config['initial_investment_usd'],
            "token_pair_symbols": "" # Wird unten befüllt
        }
    elif "history" not in all_data[position_key]:
         all_data[position_key]["history"] = {}
    
    all_data[position_key]["is_active"] = True # Doppelt gemoppelt, aber sicher ist sicher

    print(f"\n--- Processing ACTIVE Position ID: {position_nft_id} for {today_date_str} ---")
    try:
        position_details = nfpm_contract.functions.positions(position_nft_id).call()
        token0_address_checksum = Web3.to_checksum_address(position_details[2])
        token1_address_checksum = Web3.to_checksum_address(position_details[3])

        collect_params = (position_nft_id, Web3.to_checksum_address(WALLET_ADDRESS), 2**128 - 1, 2**128 - 1)
        simulated_collect = nfpm_contract.functions.collect(collect_params).call({'from': Web3.to_checksum_address(WALLET_ADDRESS)})
        unclaimed_fees_token0_raw = simulated_collect[0]
        unclaimed_fees_token1_raw = simulated_collect[1]
        
        token0_contract = w3.eth.contract(address=token0_address_checksum, abi=ERC20_ABI_MINIMAL)
        token1_contract = w3.eth.contract(address=token1_address_checksum, abi=ERC20_ABI_MINIMAL)
        
        token0_decimals = token0_contract.functions.decimals().call()
        token1_decimals = token1_contract.functions.decimals().call()
        
        current_token0_symbol, current_token1_symbol = "", ""
        if "token_pair_symbols" in all_data[position_key] and all_data[position_key]["token_pair_symbols"]:
            symbols = all_data[position_key]["token_pair_symbols"].split('/')
            if len(symbols) == 2: current_token0_symbol, current_token1_symbol = symbols[0], symbols[1]
        
        if not current_token0_symbol or not current_token1_symbol: # Hole Symbole, wenn nicht vorhanden
            current_token0_symbol = token0_contract.functions.symbol().call()
            current_token1_symbol = token1_contract.functions.symbol().call()
            all_data[position_key]["token_pair_symbols"] = f"{current_token0_symbol}/{current_token1_symbol}"
            print(f"  Token-Symbole für {position_key} ({current_token0_symbol}/{current_token1_symbol}) in JSON gespeichert.")

        current_unclaimed_fees_token0_actual = unclaimed_fees_token0_raw / (10**token0_decimals)
        current_unclaimed_fees_token1_actual = unclaimed_fees_token1_raw / (10**token1_decimals)

        print(f"  Total Unclaimed Fees (Simulated Collect):")
        print(f"    {current_token0_symbol}: {current_unclaimed_fees_token0_actual:.8f}")
        print(f"    {current_token1_symbol}: {current_unclaimed_fees_token1_actual:.8f}")

        price_token0_usd = get_single_token_price_coingecko(token0_address_checksum)
        price_token1_usd = get_single_token_price_coingecko(token1_address_checksum)
        
        current_unclaimed_token0_usd_val, current_unclaimed_token1_usd_val, current_total_unclaimed_usd_val = None, None, None
        if price_token0_usd is not None: current_unclaimed_token0_usd_val = current_unclaimed_fees_token0_actual * price_token0_usd
        if price_token1_usd is not None: current_unclaimed_token1_usd_val = current_unclaimed_fees_token1_actual * price_token1_usd
        
        if current_unclaimed_token0_usd_val is not None and current_unclaimed_token1_usd_val is not None: current_total_unclaimed_usd_val = current_unclaimed_token0_usd_val + current_unclaimed_token1_usd_val
        elif current_unclaimed_token0_usd_val is not None: current_total_unclaimed_usd_val = current_unclaimed_token0_usd_val
        elif current_unclaimed_token1_usd_val is not None: current_total_unclaimed_usd_val = current_unclaimed_token1_usd_val
        
        if current_total_unclaimed_usd_val is not None: print(f"    Total USD Value (Total Unclaimed): ${current_total_unclaimed_usd_val:.2f}")

        today_data_entry = {"total_unclaimed_fees": {"token0_actual": current_unclaimed_fees_token0_actual, "token1_actual": current_unclaimed_fees_token1_actual, "token0_usd": current_unclaimed_token0_usd_val, "token1_usd": current_unclaimed_token1_usd_val, "total_usd": current_total_unclaimed_usd_val}, "daily_earned_fees": {}}
        
        yesterday_full_data = all_data[position_key]["history"].get(yesterday_date_str)
        daily_earned_token0_actual, daily_earned_token1_actual = current_unclaimed_fees_token0_actual, current_unclaimed_fees_token1_actual
        
        if yesterday_full_data and "total_unclaimed_fees" in yesterday_full_data:
            yesterday_total_fees = yesterday_full_data["total_unclaimed_fees"]
            # Stelle sicher, dass yesterday_total_fees Werte hat, bevor du subtrahierst
            daily_earned_token0_actual -= yesterday_total_fees.get("token0_actual", 0.0) 
            daily_earned_token1_actual -= yesterday_total_fees.get("token1_actual", 0.0)
        
        daily_earned_token0_actual = max(0, daily_earned_token0_actual) # Verhindert negative Token-Mengen
        daily_earned_token1_actual = max(0, daily_earned_token1_actual) # Verhindert negative Token-Mengen

        daily_earned_token0_usd_val, daily_earned_token1_usd_val, daily_total_earned_usd_val = None, None, None
        if price_token0_usd is not None: daily_earned_token0_usd_val = daily_earned_token0_actual * price_token0_usd
        if price_token1_usd is not None: daily_earned_token1_usd_val = daily_earned_token1_actual * price_token1_usd
        
        if daily_earned_token0_usd_val is not None and daily_earned_token1_usd_val is not None: daily_total_earned_usd_val = daily_earned_token0_usd_val + daily_earned_token1_usd_val
        elif daily_earned_token0_usd_val is not None: daily_total_earned_usd_val = daily_earned_token0_usd_val
        elif daily_earned_token1_usd_val is not None: daily_total_earned_usd_val = daily_earned_token1_usd_val

        today_data_entry["daily_earned_fees"] = {"token0_actual": daily_earned_token0_actual, "token1_actual": daily_earned_token1_actual, "token0_usd": daily_earned_token0_usd_val, "token1_usd": daily_earned_token1_usd_val, "total_usd": daily_total_earned_usd_val}
        
        all_data[position_key]["history"][today_date_str] = today_data_entry
        all_data[position_key]["last_updated_utc"] = today_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

        print(f"\n  --- Fees Earned on {today_date_str} for Position {position_nft_id} ---")
        print(f"    {current_token0_symbol}: {daily_earned_token0_actual:.8f} ($" + f"{(daily_earned_token0_usd_val if daily_earned_token0_usd_val is not None else 0.0):.2f})")
        print(f"    {current_token1_symbol}: {daily_earned_token1_actual:.8f} ($" + f"{(daily_earned_token1_usd_val if daily_earned_token1_usd_val is not None else 0.0):.2f})")
        if daily_total_earned_usd_val is not None: print(f"    Total USD Value (Earned Today): ${daily_total_earned_usd_val:.2f}")

    except Exception as e_inner:
        print(f"An error occurred processing position ID {position_nft_id}: {e_inner}")
        import traceback
        traceback.print_exc()
        # Bei einem Fehler hier wird die Position zwar nicht aktualisiert, aber is_active bleibt wie es ist.
        # Und das Skript läuft weiter, um die JSON am Ende zu speichern.
            
    save_json_data(all_data)
    print(f"\n--- Fee Tracker Finished All Positions ---")

if __name__ == "__main__":
    main()