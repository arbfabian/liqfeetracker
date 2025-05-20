import os
import json
from datetime import datetime, timedelta, timezone
from web3 import Web3
from dotenv import load_dotenv
import requests
import math # Für Preisberechnungen

load_dotenv()

# --- Konfiguration ---
ARBITRUM_RPC_URL = os.getenv('ARBITRUM_RPC')
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS')
CONFIG_FILE_POSITIONS = "positions_to_track.txt"
JSON_DATA_FILE = "fees_data.json"

# DEINE SPEZIFISCHE POOL ADRESSE (WETH/WBTC 0.05% auf Arbitrum)
# Wenn du andere Pools tracken willst, muss dies dynamischer werden oder pro Position konfiguriert werden.
# Für den Moment verwenden wir diese feste Adresse.
WETH_WBTC_005_POOL_ADDRESS_ARBITRUM = "0x2f5e87C9312fa29aed5c179E456625D79015299c"

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
UNISWAP_V3_POOL_ABI_MINIMAL = json.loads("""
[
    {"inputs": [],"name": "slot0","outputs": [{"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},{"internalType": "int24", "name": "tick", "type": "int24"},{"internalType": "uint16", "name": "observationIndex", "type": "uint16"},{"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},{"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},{"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},{"internalType": "bool", "name": "unlocked", "type": "bool"}],"stateMutability": "view","type": "function"}
]
""")
# Factory Logik vorerst auskommentiert, da wir eine feste Pool-Adresse verwenden
# UNISWAP_V3_FACTORY_ADDRESS_ARBITRUM = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
# UNISWAP_V3_FACTORY_ABI_MINIMAL = json.loads(...)


# --- Hilfsfunktionen für Preisberechnung ---
def tick_to_price(tick, token0_decimals, token1_decimals, is_token0_base=True):
    price_ratio = (1.0001 ** tick)
    if is_token0_base: # Preis von Token1 in Einheiten von Token0 (z.B. WETH pro WBTC)
        return price_ratio / (10 ** (token1_decimals - token0_decimals))
    else: # Preis von Token0 in Einheiten von Token1 (z.B. WBTC pro WETH)
        return (1 / price_ratio) / (10 ** (token0_decimals - token1_decimals))


def sqrt_price_x96_to_price(sqrt_price_x96, token0_decimals, token1_decimals, is_token0_base=True):
    price_ratio = (sqrt_price_x96 / (2**96)) ** 2
    if is_token0_base: # Preis von Token1 in Einheiten von Token0
        return price_ratio / (10 ** (token1_decimals - token0_decimals))
    else: # Preis von Token0 in Einheiten von Token1
        return (1 / price_ratio) / (10 ** (token0_decimals - token1_decimals))


# --- JSON Daten laden/speichern (bleibt gleich) ---
def load_json_data(filename=JSON_DATA_FILE):
    if os.path.exists(filename):
        try:
            with open(filename, 'r') as f: return json.load(f)
        except json.JSONDecodeError: print(f"Warning: Could not decode JSON from {filename}. Starting with empty data."); return {}
    return {}

def save_json_data(data, filename=JSON_DATA_FILE):
    try:
        with open(filename, 'w') as f: json.dump(data, f, indent=2)
        print(f"Data saved to {filename}")
    except Exception as e: print(f"Error saving data to {filename}: {e}")

# --- Coingecko Preisabruf (bleibt gleich) ---
def get_single_token_price_coingecko(contract_address, platform_id="arbitrum-one"):
    try:
        checksum_address = Web3.to_checksum_address(contract_address)
        url = f"https://api.coingecko.com/api/v3/coins/{platform_id}/contract/{checksum_address}"
        response = requests.get(url, timeout=10); response.raise_for_status(); data = response.json()
        price_usd = data.get("market_data", {}).get("current_price", {}).get("usd")
        if price_usd is None: print(f"    Warning: Could not parse USD price for {checksum_address}. Response: {data}")
        return price_usd
    except requests.exceptions.RequestException as e: print(f"    CoinGecko API request error for {contract_address}: {e}")
    except Exception as e_gen: print(f"    Error fetching price for {contract_address}: {e_gen}")
    return None

# --- Positions-Config lesen (bleibt wie in deinem Code) ---
def get_active_position_config(filename=CONFIG_FILE_POSITIONS):
    # Dein Code für get_active_position_config hier einfügen
    try:
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                lines = f.readlines()
                if not lines: return None
                for line_number, line in enumerate(lines, 1):
                    line = line.strip()
                    if not line or line.startswith('#'): continue
                    parts = line.split(',')
                    if len(parts) == 2:
                        try:
                            position_id = int(parts[0].strip())
                            investment_usd = float(parts[1].strip())
                            print(f"Aktive Position aus '{filename}': ID {position_id}, Investment {investment_usd} USD")
                            return {'id': position_id, 'initial_investment_usd': investment_usd}
                        except ValueError: print(f"Error: Ungültiges Zahlenformat in '{filename}' Zeile {line_number}: '{line}'."); return None
                    else: print(f"Error: Ungültiges Format in '{filename}' Zeile {line_number}: '{line}'."); return None
                return None
        else: print(f"Info: Konfigurationsdatei '{filename}' nicht gefunden."); return None
    except Exception as e: print(f"Fehler beim Lesen der Konfigurationsdatei '{filename}': {e}"); return None

# --- Hauptlogik ---
def main():
    print(f"--- Starting Uniswap V3 Fee Tracker ---")
    all_data = load_json_data()

    if not ARBITRUM_RPC_URL or not WALLET_ADDRESS: print("CRITICAL Error: Missing environment variables. Exiting."); return
    w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
    if not w3.is_connected(): print(f"CRITICAL Error: Could not connect to Arbitrum RPC. Exiting."); return
    print(f"Successfully connected to Arbitrum RPC.")

    nfpm_contract = w3.eth.contract(address=NFPM_ADDRESS, abi=NFPM_ABI)
    active_pos_config = get_active_position_config()
    active_position_id_from_config = active_pos_config['id'] if active_pos_config else None

    print("Aktualisiere 'is_active' Flags in fees_data.json...")
    for pos_key_in_json in list(all_data.keys()):
        if pos_key_in_json.startswith("position_"):
            try:
                position_id_in_json = int(pos_key_in_json.replace("position_", ""))
                is_currently_active_in_json = all_data[pos_key_in_json].get("is_active", False)
                should_be_active = active_position_id_from_config is not None and position_id_in_json == active_position_id_from_config
                
                if is_currently_active_in_json != should_be_active:
                    print(f"  Position {pos_key_in_json} wird auf 'is_active: {should_be_active}' gesetzt.")
                all_data[pos_key_in_json]["is_active"] = should_be_active

                if should_be_active: # Nur für die aktive Position das Investment aktualisieren
                    current_investment_in_json = all_data[pos_key_in_json].get("initial_investment_usd")
                    config_investment = active_pos_config['initial_investment_usd']
                    if current_investment_in_json != config_investment:
                        all_data[pos_key_in_json]["initial_investment_usd"] = config_investment
                        print(f"  Initialinvestment für aktive Position {pos_key_in_json} auf {config_investment} USD gesetzt/aktualisiert.")
            except ValueError: print(f"  Konnte ID für Key '{pos_key_in_json}' nicht parsen.")

    if not active_pos_config:
        print("Keine aktive Position in Config. Es werden keine Gebühren aktualisiert.")
        save_json_data(all_data); print(f"\n--- Fee Tracker Finished (No active fees updated) ---"); return

    position_nft_id = active_pos_config['id']
    position_key = f"position_{position_nft_id}"
    today_utc = datetime.now(timezone.utc)
    today_date_str = today_utc.strftime('%Y-%m-%d')
    yesterday_date_str = (today_utc - timedelta(days=1)).strftime('%Y-%m-%d')

    if position_key not in all_data:
        all_data[position_key] = {"history": {}, "is_active": True, "initial_investment_usd": active_pos_config['initial_investment_usd'], "token_pair_symbols": ""}
    elif "history" not in all_data[position_key]: all_data[position_key]["history"] = {}
    all_data[position_key]["is_active"] = True

    print(f"\n--- Processing ACTIVE Position ID: {position_nft_id} for {today_date_str} ---")
    try:
        position_details = nfpm_contract.functions.positions(position_nft_id).call()
        token0_address_checksum = Web3.to_checksum_address(position_details[2])
        token1_address_checksum = Web3.to_checksum_address(position_details[3])
        # fee_tier_from_nft = position_details[4] # z.B. 500 für 0.05%
        tick_lower = position_details[5]
        tick_upper = position_details[6]

        token0_contract = w3.eth.contract(address=token0_address_checksum, abi=ERC20_ABI_MINIMAL)
        token1_contract = w3.eth.contract(address=token1_address_checksum, abi=ERC20_ABI_MINIMAL)
        token0_decimals = token0_contract.functions.decimals().call()
        token1_decimals = token1_contract.functions.decimals().call()

        current_token0_symbol, current_token1_symbol = "", ""
        if "token_pair_symbols" in all_data[position_key] and all_data[position_key]["token_pair_symbols"]:
            symbols = all_data[position_key]["token_pair_symbols"].split('/')
            if len(symbols) == 2: current_token0_symbol, current_token1_symbol = symbols[0], symbols[1]
        if not current_token0_symbol or not current_token1_symbol:
            current_token0_symbol = token0_contract.functions.symbol().call()
            current_token1_symbol = token1_contract.functions.symbol().call()
            all_data[position_key]["token_pair_symbols"] = f"{current_token0_symbol}/{current_token1_symbol}"
        print(f"  Tokens: {current_token0_symbol}({token0_decimals})/{current_token1_symbol}({token1_decimals})")

        # Gebühren
        collect_params = (position_nft_id, Web3.to_checksum_address(WALLET_ADDRESS), 2**128 - 1, 2**128 - 1)
        simulated_collect = nfpm_contract.functions.collect(collect_params).call({'from': Web3.to_checksum_address(WALLET_ADDRESS)})
        
        # Korrekte Definition und durchgehende Verwendung dieser Namen:
        unclaimed_fees_token0_actual = simulated_collect[0] / (10**token0_decimals) 
        unclaimed_fees_token1_actual = simulated_collect[1] / (10**token1_decimals)
        
        # (USD-Berechnung der Gebühren)
        price_token0_usd = get_single_token_price_coingecko(token0_address_checksum)
        price_token1_usd = get_single_token_price_coingecko(token1_address_checksum)
        
        # Initialisierung der USD-Wert-Variablen
        current_unclaimed_token0_usd_val = None
        current_unclaimed_token1_usd_val = None
        current_total_unclaimed_usd_val = None
        
        # Korrigierte Verwendung der Variablennamen und None-Check
        if price_token0_usd is not None: 
            current_unclaimed_token0_usd_val = unclaimed_fees_token0_actual * price_token0_usd 
        if price_token1_usd is not None: 
            current_unclaimed_token1_usd_val = unclaimed_fees_token1_actual * price_token1_usd
        
        # Logik für current_total_unclaimed_usd_val bleibt gleich,
        # aber basiert auf den potenziell aktualisierten Werten oben.
        if current_unclaimed_token0_usd_val is not None and current_unclaimed_token1_usd_val is not None:
            current_total_unclaimed_usd_val = current_unclaimed_token0_usd_val + current_unclaimed_token1_usd_val
        elif current_unclaimed_token0_usd_val is not None: # Nur Token0-Wert vorhanden
            current_total_unclaimed_usd_val = current_unclaimed_token0_usd_val
        elif current_unclaimed_token1_usd_val is not None: # Nur Token1-Wert vorhanden
            current_total_unclaimed_usd_val = current_unclaimed_token1_usd_val
        # Wenn beide None sind, bleibt current_total_unclaimed_usd_val None, was korrekt ist.

        # Hier kannst du dann mit den `current_unclaimed_..._usd_val` und `current_total_unclaimed_usd_val`
        # weiterarbeiten, um sie in `today_data_entry["total_unclaimed_fees"]` zu speichern.
        # Zum Beispiel:
        # print(f"  Total Unclaimed Fees (Simulated Collect):")
        # print(f"    {current_token0_symbol}: {unclaimed_fees_token0_actual:.8f} (~${(current_unclaimed_token0_usd_val or 0.0):.2f})")
        # print(f"    {current_token1_symbol}: {unclaimed_fees_token1_actual:.8f} (~${(current_unclaimed_token1_usd_val or 0.0):.2f})")
        # if current_total_unclaimed_usd_val is not None:
        #    print(f"    Total USD Value (Total Unclaimed): ${current_total_unclaimed_usd_val:.2f}")

        # NEU: Range-Preise und aktuellen Marktpreis ermitteln
        # Wähle, wie der Preis dargestellt werden soll:
        # True: Preis von Token1 in Einheiten von Token0 (z.B. WETH pro WBTC)
        # False: Preis von Token0 in Einheiten von Token1 (z.B. WBTC pro WETH) -> Üblicher für WBTC/WETH
        PRICE_PRESENTATION_IS_TOKEN0_BASE = False

        # Stelle sicher, dass die Token-Reihenfolge für die Preisberechnung mit der UI übereinstimmt
        # Uniswap behandelt Token0/Token1 basierend auf ihren Adressen (sortiert).
        # Wenn Token0 aus dem NFPM WBTC ist (kleinere Adresse) und Token1 WETH (größere Adresse):
        # PRICE_PRESENTATION_IS_TOKEN0_BASE = True  -> Preis von WETH in WBTC (wie viel WBTC für 1 WETH)
        # PRICE_PRESENTATION_IS_TOKEN0_BASE = False -> Preis von WBTC in WETH (wie viel WETH für 1 WBTC)
        # Die `tick_to_price` und `sqrt_price_x96_to_price` sind so geschrieben, dass sie Preis von Token1/Token0 liefern.
        # Wenn PRICE_PRESENTATION_IS_TOKEN0_BASE = False, wird der Kehrwert genommen.
        # Die Labels müssen dann entsprechend angepasst werden.
        
        # Logik, um sicherzustellen, dass Token0 und Token1 für die Preisberechnung konsistent sind mit der Pool-Definition
        # Uniswap Pools sortieren Token-Adressen. token0 ist immer die Adresse mit dem niedrigeren Hex-Wert.
        pool_token0_address_onchain = None
        pool_token1_address_onchain = None
        
        # Verwende feste Pool-Adresse
        pool_address_for_position = WETH_WBTC_005_POOL_ADDRESS_ARBITRUM # Feste Adresse
        
        # Hilfsvariablen für Preisbeschriftung
        price_base_token_symbol = ""
        price_quote_token_symbol = ""

        price_lower, price_upper, current_market_price = None, None, None

        if pool_address_for_position:
            pool_contract = w3.eth.contract(address=pool_address_for_position, abi=UNISWAP_V3_POOL_ABI_MINIMAL)
            # Optional: On-chain Token0/Token1 des Pools verifizieren, um Preisrichtung sicherzustellen
            # onchain_t0 = pool_contract.functions.token0().call()
            # onchain_t1 = pool_contract.functions.token1().call()
            # is_nft_token0_pool_token0 = (token0_address_checksum.lower() == onchain_t0.lower())

            # Annahme: Deine NFT-Token0/Token1 entsprechen der Pool-Sortierung ODER die Preisberechnungsfunktionen handhaben dies.
            # Die Preisberechnungsfunktionen sind so geschrieben, dass sie den Preis von Token1 in Token0 liefern.
            # Wenn PRICE_PRESENTATION_IS_TOKEN0_BASE auf False gesetzt ist, wird der Kehrwert genommen, um Preis von Token0 in Token1 zu erhalten.

            # Preis von Token1 in Einheiten von Token0
            price_lower_t1_per_t0 = tick_to_price(tick_lower, token0_decimals, token1_decimals, True) # Immer T1/T0
            price_upper_t1_per_t0 = tick_to_price(tick_upper, token0_decimals, token1_decimals, True) # Immer T1/T0

            slot0 = pool_contract.functions.slot0().call()
            sqrt_price_x96_current = slot0[0]
            current_market_price_t1_per_t0 = sqrt_price_x96_to_price(sqrt_price_x96_current, token0_decimals, token1_decimals, True) # Immer T1/T0

            if PRICE_PRESENTATION_IS_TOKEN0_BASE: # Preis von Token1 pro Token0 (z.B. WETH/WBTC)
                price_lower = price_lower_t1_per_t0
                price_upper = price_upper_t1_per_t0
                current_market_price = current_market_price_t1_per_t0
                price_base_token_symbol = current_token0_symbol # z.B. WBTC
                price_quote_token_symbol = current_token1_symbol # z.B. WETH
            else: # Preis von Token0 pro Token1 (z.B. WBTC/WETH) -> Kehrwert
                price_lower = 1 / price_upper_t1_per_t0 if price_upper_t1_per_t0 != 0 else None # untere Grenze wird zur oberen des Kehrwerts
                price_upper = 1 / price_lower_t1_per_t0 if price_lower_t1_per_t0 != 0 else None # obere Grenze wird zur unteren des Kehrwerts
                # Korrekte Reihenfolge für Kehrwert-Range
                if price_lower is not None and price_upper is not None and price_lower > price_upper:
                    price_lower, price_upper = price_upper, price_lower

                current_market_price = 1 / current_market_price_t1_per_t0 if current_market_price_t1_per_t0 != 0 else None
                price_base_token_symbol = current_token1_symbol # z.B. WETH
                price_quote_token_symbol = current_token0_symbol # z.B. WBTC

            print(f"  Range: [{price_lower:.6f} - {price_upper:.6f}] {price_quote_token_symbol} per {price_base_token_symbol}")
            if current_market_price is not None:
                print(f"  Current Market Price: {current_market_price:.6f} {price_quote_token_symbol} per {price_base_token_symbol}")
        else:
            print(f"  Pool address for position {position_nft_id} not defined, skipping range price calculation.")


        today_data_entry = {
            "total_unclaimed_fees": {"token0_actual": unclaimed_fees_token0_actual, "token1_actual": unclaimed_fees_token1_actual, "token0_usd": current_unclaimed_token0_usd_val, "token1_usd": current_unclaimed_token1_usd_val, "total_usd": current_total_unclaimed_usd_val},
            "daily_earned_fees": {},
            "position_range": {
                "price_lower": price_lower, "price_upper": price_upper, "current_market_price": current_market_price,
                "base_token_for_price": price_base_token_symbol, "quote_token_for_price": price_quote_token_symbol
            }
        }
        
        yesterday_full_data = all_data[position_key]["history"].get(yesterday_date_str)
        daily_earned_token0_actual = unclaimed_fees_token0_actual
        daily_earned_token1_actual = unclaimed_fees_token1_actual
        if yesterday_full_data and "total_unclaimed_fees" in yesterday_full_data:
            y_total_fees = yesterday_full_data["total_unclaimed_fees"]
            daily_earned_token0_actual -= y_total_fees.get("token0_actual", 0.0)
            daily_earned_token1_actual -= y_total_fees.get("token1_actual", 0.0)
        daily_earned_token0_actual = max(0, daily_earned_token0_actual)
        daily_earned_token1_actual = max(0, daily_earned_token1_actual)

        daily_earned_token0_usd_val, daily_earned_token1_usd_val, daily_total_earned_usd_val = None, None, None
        if price_token0_usd: daily_earned_token0_usd_val = daily_earned_token0_actual * price_token0_usd
        if price_token1_usd: daily_earned_token1_usd_val = daily_earned_token1_actual * price_token1_usd
        if daily_earned_token0_usd_val and daily_earned_token1_usd_val: daily_total_earned_usd_val = daily_earned_token0_usd_val + daily_earned_token1_usd_val
        elif daily_earned_token0_usd_val: daily_total_earned_usd_val = daily_earned_token0_usd_val
        elif daily_earned_token1_usd_val: daily_total_earned_usd_val = daily_earned_token1_usd_val

        today_data_entry["daily_earned_fees"] = {"token0_actual": daily_earned_token0_actual, "token1_actual": daily_earned_token1_actual, "token0_usd": daily_earned_token0_usd_val, "token1_usd": daily_earned_token1_usd_val, "total_usd": daily_total_earned_usd_val}
        
        all_data[position_key]["history"][today_date_str] = today_data_entry
        all_data[position_key]["last_updated_utc"] = today_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

        print(f"\n  --- Fees Earned on {today_date_str} for Position {position_nft_id} ---")
        print(f"    {current_token0_symbol}: {daily_earned_token0_actual:.8f} ($" + f"{(daily_earned_token0_usd_val or 0.0):.2f})")
        print(f"    {current_token1_symbol}: {daily_earned_token1_actual:.8f} ($" + f"{(daily_earned_token1_usd_val or 0.0):.2f})")
        if daily_total_earned_usd_val is not None: print(f"    Total USD Value (Earned Today): ${daily_total_earned_usd_val:.2f}")

    except Exception as e_inner:
        print(f"An error occurred processing position ID {position_nft_id}: {e_inner}")
        import traceback; traceback.print_exc()
            
    save_json_data(all_data)
    print(f"\n--- Fee Tracker Finished ---")

if __name__ == "__main__":
    main()