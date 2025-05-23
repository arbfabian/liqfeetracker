import os
import json
from datetime import datetime, timedelta, timezone
from web3 import Web3
from dotenv import load_dotenv
import requests
import math
import time # Import für time.sleep in get_single_token_price_coingecko

load_dotenv()

ARBITRUM_RPC_URL = os.getenv('ARBITRUM_RPC')
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS')
CONFIG_FILE_POSITIONS = "positions_to_track.txt"
JSON_DATA_FILE = "fees_data.json"
PRICE_TICKS_FILE = "price_ticks.json"

WETH_WBTC_005_POOL_ADDRESS_ARBITRUM = "0x2f5e87C9312fa29aed5c179E456625D79015299c"

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

def tick_to_price(tick, token0_decimals, token1_decimals, is_token0_base=True):
    price_ratio = (1.0001 ** tick)
    if is_token0_base:
        return price_ratio / (10 ** (token1_decimals - token0_decimals))
    else:
        return (1 / price_ratio) / (10 ** (token0_decimals - token1_decimals))

def sqrt_price_x96_to_price(sqrt_price_x96, token0_decimals, token1_decimals, is_token0_base=True):
    price_ratio = (sqrt_price_x96 / (2**96)) ** 2
    if is_token0_base:
        return price_ratio / (10 ** (token1_decimals - token0_decimals))
    else:
        return (1 / price_ratio) / (10 ** (token0_decimals - token1_decimals))

def load_json_data(filename=JSON_DATA_FILE):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f: # encoding hinzugefügt
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: Could not decode JSON from {filename}. Starting with empty data.")
            return {}
    return {}

def save_json_data(data, filename=JSON_DATA_FILE): # Deine ursprüngliche Speicherfunktion
    try:
        with open(filename, 'w', encoding='utf-8') as f: # encoding hinzugefügt
            json.dump(data, f, indent=2)
        print(f"Data saved to {filename}")
    except Exception as e:
        print(f"Error saving data to {filename}: {e}")

def get_single_token_price_coingecko(contract_address, platform_id="arbitrum-one", retries=3, delay=5): # retries und delay hinzugefügt
    checksum_address = Web3.to_checksum_address(contract_address)
    url = f"https://api.coingecko.com/api/v3/coins/{platform_id}/contract/{checksum_address}"
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            price_usd = data.get("market_data", {}).get("current_price", {}).get("usd")
            if price_usd is not None:
                return price_usd
            else:
                print(f"    Warning: USD price not found in CoinGecko response for {checksum_address}. Attempt {attempt + 1}/{retries}.")
        except requests.exceptions.HTTPError as http_err:
            print(f"    CoinGecko HTTP error for {contract_address}: {http_err}. Attempt {attempt + 1}/{retries}.")
        except requests.exceptions.ConnectionError as conn_err:
            print(f"    CoinGecko Connection error for {contract_address}: {conn_err}. Attempt {attempt + 1}/{retries}.")
        except requests.exceptions.Timeout as timeout_err:
            print(f"    CoinGecko Timeout error for {contract_address}: {timeout_err}. Attempt {attempt + 1}/{retries}.")
        except requests.exceptions.RequestException as req_err:
            print(f"    CoinGecko API request error for {contract_address}: {req_err}. Attempt {attempt + 1}/{retries}.")
        except json.JSONDecodeError as json_err:
            print(f"    Error decoding CoinGecko JSON response for {contract_address}: {json_err}. Attempt {attempt + 1}/{retries}.")
        except Exception as e_gen:
            print(f"    Unexpected error fetching price for {contract_address}: {e_gen}. Attempt {attempt + 1}/{retries}.")
        
        if attempt < retries - 1:
            print(f"    Retrying CoinGecko in {delay} seconds...")
            time.sleep(delay)
        else:
            print(f"    All CoinGecko retries failed for {contract_address}.")
    return None

def get_active_position_config(filename=CONFIG_FILE_POSITIONS):
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                if not lines:
                    return None
                for line_number, line in enumerate(lines, 1):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split(',')
                    if len(parts) == 2:
                        try:
                            position_id = int(parts[0].strip())
                            investment_usd = float(parts[1].strip())
                            print(f"Aktive Position aus '{filename}': ID {position_id}, Investment {investment_usd} USD")
                            return {'id': position_id, 'initial_investment_usd': investment_usd}
                        except ValueError:
                            print(f"Error: Ungültiges Zahlenformat in '{filename}' Zeile {line_number}: '{line}'.")
                            return None
                    else:
                        print(f"Error: Ungültiges Format in '{filename}' Zeile {line_number}: '{line}'.")
                        return None
                return None
        else:
            print(f"Info: Konfigurationsdatei '{filename}' nicht gefunden.")
            return None
    except Exception as e:
        print(f"Fehler beim Lesen der Konfigurationsdatei '{filename}': {e}")
        return None

def calculate_time_in_range_percentage(price_ticks_filepath, position_range_data, hours_to_check=24):
    if not os.path.exists(price_ticks_filepath) or not position_range_data:
        return None
    if position_range_data.get("price_lower") is None or position_range_data.get("price_upper") is None:
        return None

    price_lower = min(position_range_data["price_lower"], position_range_data["price_upper"])
    price_upper = max(position_range_data["price_lower"], position_range_data["price_upper"])
    range_base_token = position_range_data.get("base_token_for_price")
    range_quote_token = position_range_data.get("quote_token_for_price")

    all_price_ticks = []
    if os.path.exists(price_ticks_filepath):
        with open(price_ticks_filepath, 'r', encoding='utf-8') as f:
            try:
                all_price_ticks = json.load(f)
                if not isinstance(all_price_ticks, list):
                    return None
            except json.JSONDecodeError:
                return None
    else:
        return None

    now_utc = datetime.now(timezone.utc)
    cutoff_time_utc = now_utc - timedelta(hours=hours_to_check)
    relevant_ticks = 0
    ticks_in_range = 0

    for tick_entry in all_price_ticks:
        try:
            tick_timestamp_str = tick_entry.get("timestamp")
            tick_price = tick_entry.get("price")
            tick_base = tick_entry.get("base_token")
            tick_quote = tick_entry.get("quote_token")

            if not all([tick_timestamp_str, tick_price, tick_base, tick_quote]):
                continue
            if tick_base != range_base_token or tick_quote != range_quote_token:
                continue

            if tick_timestamp_str.endswith("Z"):
                tick_dt = datetime.fromisoformat(tick_timestamp_str[:-1] + "+00:00")
            else:
                tick_dt = datetime.fromisoformat(tick_timestamp_str).replace(tzinfo=timezone.utc)

            if tick_dt >= cutoff_time_utc:
                relevant_ticks += 1
                if price_lower <= tick_price <= price_upper:
                    ticks_in_range += 1
        except Exception:
            continue

    if relevant_ticks == 0:
        return 0.0
    return (ticks_in_range / relevant_ticks) * 100

def main():
    print(f"--- Starting Uniswap V3 Fee Tracker ({datetime.now(timezone.utc).isoformat()}) ---")
    all_data = load_json_data(JSON_DATA_FILE)

    if not ARBITRUM_RPC_URL or not WALLET_ADDRESS:
        print("CRITICAL Error: Missing environment variables. Exiting.")
        return
    w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
    if not w3.is_connected():
        print(f"CRITICAL Error: Could not connect to Arbitrum RPC. Exiting.")
        return
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

                if should_be_active and active_pos_config: # Sicherstellen, dass active_pos_config existiert
                    current_investment_in_json = all_data[pos_key_in_json].get("initial_investment_usd")
                    config_investment = active_pos_config['initial_investment_usd']
                    if current_investment_in_json != config_investment:
                        all_data[pos_key_in_json]["initial_investment_usd"] = config_investment
                        print(f"  Initialinvestment für aktive Position {pos_key_in_json} auf {config_investment} USD gesetzt/aktualisiert.")
            except ValueError:
                print(f"  Konnte ID für Key '{pos_key_in_json}' nicht parsen.")

    if not active_pos_config:
        print("Keine aktive Position in Config. Es werden keine Gebühren aktualisiert.")
        save_json_data(all_data, JSON_DATA_FILE) # Alte Speicherfunktion
        print(f"\n--- Fee Tracker Finished (No active fees updated) ---")
        return

    position_nft_id = active_pos_config['id']
    position_key = f"position_{position_nft_id}"
    today_utc = datetime.now(timezone.utc)
    today_date_str = today_utc.strftime('%Y-%m-%d')
    yesterday_date_str = (today_utc - timedelta(days=1)).strftime('%Y-%m-%d')

    if position_key not in all_data:
        all_data[position_key] = {"history": {}, "is_active": True, "initial_investment_usd": active_pos_config['initial_investment_usd'], "token_pair_symbols": ""}
    elif "history" not in all_data[position_key]:
        all_data[position_key]["history"] = {}
    all_data[position_key]["is_active"] = True

    print(f"\n--- Processing ACTIVE Position ID: {position_nft_id} for {today_date_str} ---")
    try:
        position_details = nfpm_contract.functions.positions(position_nft_id).call()
        token0_address_checksum = Web3.to_checksum_address(position_details[2])
        token1_address_checksum = Web3.to_checksum_address(position_details[3])
        tick_lower = position_details[5]
        tick_upper = position_details[6]

        token0_contract = w3.eth.contract(address=token0_address_checksum, abi=ERC20_ABI_MINIMAL)
        token1_contract = w3.eth.contract(address=token1_address_checksum, abi=ERC20_ABI_MINIMAL)
        token0_decimals = token0_contract.functions.decimals().call()
        token1_decimals = token1_contract.functions.decimals().call()

        current_token0_symbol, current_token1_symbol = "", ""
        if "token_pair_symbols" in all_data[position_key] and all_data[position_key]["token_pair_symbols"]:
            symbols = all_data[position_key]["token_pair_symbols"].split('/')
            if len(symbols) == 2:
                current_token0_symbol, current_token1_symbol = symbols[0], symbols[1]
        if not current_token0_symbol or not current_token1_symbol:
            current_token0_symbol = token0_contract.functions.symbol().call()
            current_token1_symbol = token1_contract.functions.symbol().call()
            all_data[position_key]["token_pair_symbols"] = f"{current_token0_symbol}/{current_token1_symbol}"
        print(f"  Tokens: {current_token0_symbol}({token0_decimals})/{current_token1_symbol}({token1_decimals})")

        collect_params = (position_nft_id, Web3.to_checksum_address(WALLET_ADDRESS), 2**128 - 1, 2**128 - 1)
        simulated_collect = nfpm_contract.functions.collect(collect_params).call({'from': Web3.to_checksum_address(WALLET_ADDRESS)})
        
        unclaimed_fees_token0_actual = simulated_collect[0] / (10**token0_decimals) 
        unclaimed_fees_token1_actual = simulated_collect[1] / (10**token1_decimals)
        
        price_token0_usd = get_single_token_price_coingecko(token0_address_checksum)
        price_token1_usd = get_single_token_price_coingecko(token1_address_checksum)
        
        current_unclaimed_token0_usd_val = None
        current_unclaimed_token1_usd_val = None
        current_total_unclaimed_usd_val = None
        
        if price_token0_usd is not None: 
            current_unclaimed_token0_usd_val = unclaimed_fees_token0_actual * price_token0_usd 
        if price_token1_usd is not None: 
            current_unclaimed_token1_usd_val = unclaimed_fees_token1_actual * price_token1_usd
        
        if current_unclaimed_token0_usd_val is not None and current_unclaimed_token1_usd_val is not None:
            current_total_unclaimed_usd_val = current_unclaimed_token0_usd_val + current_unclaimed_token1_usd_val
        elif current_unclaimed_token0_usd_val is not None: 
            current_total_unclaimed_usd_val = current_unclaimed_token0_usd_val
        elif current_unclaimed_token1_usd_val is not None: 
            current_total_unclaimed_usd_val = current_unclaimed_token1_usd_val

        PRICE_PRESENTATION_IS_TOKEN0_BASE = True 
        
        pool_address_for_position = WETH_WBTC_005_POOL_ADDRESS_ARBITRUM
        price_base_token_symbol_for_json = ""
        price_quote_token_symbol_for_json = ""
        
        if PRICE_PRESENTATION_IS_TOKEN0_BASE:
             price_base_token_symbol_for_json = current_token0_symbol
             price_quote_token_symbol_for_json = current_token1_symbol
        else:
             price_base_token_symbol_for_json = current_token1_symbol
             price_quote_token_symbol_for_json = current_token0_symbol
        
        price_lower_calculated, price_upper_calculated, current_market_price_calculated = None, None, None

        if pool_address_for_position:
            pool_contract = w3.eth.contract(address=pool_address_for_position, abi=UNISWAP_V3_POOL_ABI_MINIMAL)
            
            price_lower_t1_per_t0 = tick_to_price(tick_lower, token0_decimals, token1_decimals, True)
            price_upper_t1_per_t0 = tick_to_price(tick_upper, token0_decimals, token1_decimals, True) 

            slot0 = pool_contract.functions.slot0().call()
            sqrt_price_x96_current = slot0[0]
            current_market_price_t1_per_t0 = sqrt_price_x96_to_price(sqrt_price_x96_current, token0_decimals, token1_decimals, True)

            if PRICE_PRESENTATION_IS_TOKEN0_BASE:
                price_lower_calculated = price_lower_t1_per_t0
                price_upper_calculated = price_upper_t1_per_t0
                current_market_price_calculated = current_market_price_t1_per_t0
            else: 
                price_lower_calculated = 1 / price_upper_t1_per_t0 if price_upper_t1_per_t0 != 0 else None
                price_upper_calculated = 1 / price_lower_t1_per_t0 if price_lower_t1_per_t0 != 0 else None
                if price_lower_calculated is not None and price_upper_calculated is not None and price_lower_calculated > price_upper_calculated:
                    price_lower_calculated, price_upper_calculated = price_upper_calculated, price_lower_calculated
                current_market_price_calculated = 1 / current_market_price_t1_per_t0 if current_market_price_t1_per_t0 != 0 else None
            
            print(f"  Range: [{price_lower_calculated:.6f} - {price_upper_calculated:.6f}] {price_quote_token_symbol_for_json} per {price_base_token_symbol_for_json}")
            if current_market_price_calculated is not None:
                print(f"  Current Market Price: {current_market_price_calculated:.6f} {price_quote_token_symbol_for_json} per {price_base_token_symbol_for_json}")
        else:
            print(f"  Pool address for position {position_nft_id} not defined, skipping range price calculation.")

        today_data_entry = {
            "total_unclaimed_fees": {"token0_actual": unclaimed_fees_token0_actual, "token1_actual": unclaimed_fees_token1_actual, "token0_usd": current_unclaimed_token0_usd_val, "token1_usd": current_unclaimed_token1_usd_val, "total_usd": current_total_unclaimed_usd_val},
            "daily_earned_fees": {},
            "position_range": {
                "price_lower": price_lower_calculated, "price_upper": price_upper_calculated, "current_market_price": current_market_price_calculated,
                "base_token_for_price": price_base_token_symbol_for_json, "quote_token_for_price": price_quote_token_symbol_for_json
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
        if price_token0_usd is not None: daily_earned_token0_usd_val = daily_earned_token0_actual * price_token0_usd
        if price_token1_usd is not None: daily_earned_token1_usd_val = daily_earned_token1_actual * price_token1_usd
        if daily_earned_token0_usd_val is not None and daily_earned_token1_usd_val is not None: 
            daily_total_earned_usd_val = daily_earned_token0_usd_val + daily_earned_token1_usd_val
        elif daily_earned_token0_usd_val is not None: 
            daily_total_earned_usd_val = daily_earned_token0_usd_val
        elif daily_earned_token1_usd_val is not None: 
            daily_total_earned_usd_val = daily_earned_token1_usd_val

        today_data_entry["daily_earned_fees"] = {"token0_actual": daily_earned_token0_actual, "token1_actual": daily_earned_token1_actual, "token0_usd": daily_earned_token0_usd_val, "token1_usd": daily_earned_token1_usd_val, "total_usd": daily_total_earned_usd_val}
        
        all_data[position_key]["history"][today_date_str] = today_data_entry
        
        time_in_range_24h = calculate_time_in_range_percentage(
            PRICE_TICKS_FILE, 
            today_data_entry["position_range"],
            hours_to_check=24
        )
        if time_in_range_24h is not None:
            all_data[position_key]["time_in_range_24h_percentage"] = time_in_range_24h
            print(f"  Time in Range (last 24h): {time_in_range_24h:.2f}%")
        else:
            if "time_in_range_24h_percentage" in all_data[position_key]:
                del all_data[position_key]["time_in_range_24h_percentage"]
            print(f"  Time in Range (last 24h): Konnte nicht berechnet werden.")
            
        all_data[position_key]["last_updated_utc"] = today_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

        print(f"\n  --- Fees Earned on {today_date_str} for Position {position_nft_id} ---")
        print(f"    {current_token0_symbol}: {daily_earned_token0_actual:.8f} ($" + f"{(daily_earned_token0_usd_val or 0.0):.2f})")
        print(f"    {current_token1_symbol}: {daily_earned_token1_actual:.8f} ($" + f"{(daily_earned_token1_usd_val or 0.0):.2f})")
        if daily_total_earned_usd_val is not None: 
            print(f"    Total USD Value (Earned Today): ${daily_total_earned_usd_val:.2f}")

    except Exception as e_inner:
        print(f"An error occurred processing position ID {position_nft_id}: {e_inner}")
        import traceback; traceback.print_exc()
            
    save_json_data(all_data, JSON_DATA_FILE) 
    print(f"\n--- Fee Tracker Finished ---")

if __name__ == "__main__":
    main()