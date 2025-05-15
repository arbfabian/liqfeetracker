import os
import json
from datetime import datetime, timedelta, timezone
from web3 import Web3
from dotenv import load_dotenv
import requests

load_dotenv()

# --- Konfiguration ---
ARBITRUM_RPC_URL = os.getenv('ARBITRUM_RPC')
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS')
CONFIG_FILE_POSITIONS = "positions_to_track.txt"
JSON_DATA_FILE = "fees_data.json"

# --- Konstanten ---
NFPM_ADDRESS = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
NFPM_ABI = json.loads("""
[
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "positions",
        "outputs": [
            {"internalType": "uint96", "name": "nonce", "type": "uint96"},
            {"internalType": "address", "name": "operator", "type": "address"},
            {"internalType": "address", "name": "token0", "type": "address"},
            {"internalType": "address", "name": "token1", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
            {"internalType": "int24", "name": "tickLower", "type": "int24"},
            {"internalType": "int24", "name": "tickUpper", "type": "int24"},
            {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
            {"internalType": "uint256", "name": "feeGrowthInside0LastX128", "type": "uint256"},
            {"internalType": "uint256", "name": "feeGrowthInside1LastX128", "type": "uint256"},
            {"internalType": "uint128", "name": "tokensOwed0", "type": "uint128"},
            {"internalType": "uint128", "name": "tokensOwed1", "type": "uint128"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "tokenId", "type": "uint256"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint128", "name": "amount0Max", "type": "uint128"},
                    {"internalType": "uint128", "name": "amount1Max", "type": "uint128"}
                ],
                "internalType": "struct INonfungiblePositionManager.CollectParams",
                "name": "params",
                "type": "tuple"
            }
        ],
        "name": "collect",
        "outputs": [
            {"internalType": "uint256", "name": "amount0", "type": "uint256"},
            {"internalType": "uint256", "name": "amount1", "type": "uint256"}
        ],
        "stateMutability": "payable",
        "type": "function"
    }
]
""")
ERC20_ABI_MINIMAL = json.loads("""
[
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"payable":false,"stateMutability":"view","type":"function"},
    {"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"payable":false,"stateMutability":"view","type":"function"}
]
""")

# --- JSON Daten laden ---
def load_json_data(filename=JSON_DATA_FILE):
    if os.path.exists(filename):
        try:
            with open(filename, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: Could not decode JSON from {filename}. Starting with empty data.")
            return {}
    return {}

# --- JSON Daten speichern ---
def save_json_data(data, filename=JSON_DATA_FILE):
    try:
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Data saved to {filename}")
    except Exception as e:
        print(f"Error saving data to {filename}: {e}")


# --- Funktion zum Holen von Preisen ---
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


# --- Funktion zum Lesen der Positions-IDs ---
def get_position_ids_from_file(filename=CONFIG_FILE_POSITIONS):
    position_ids = []
    try:
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and line.isdigit():
                        position_ids.append(int(line))
            print(f"Found position IDs in '{filename}': {position_ids}")
        else:
            print(f"Warning: Position ID file '{filename}' not found.")
    except Exception as e:
        print(f"Error reading position ID file '{filename}': {e}")
    return position_ids


# --- Hauptlogik ---
def main():
    print(f"--- Starting Uniswap V3 Fee Tracker ---")
    
    all_data = load_json_data() 

    if not ARBITRUM_RPC_URL or not WALLET_ADDRESS:
        print("Error: Missing environment variables (ARBITRUM_RPC, WALLET_ADDRESS)")
        return

    w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
    if not w3.is_connected():
        print(f"Error: Could not connect to Arbitrum RPC: {ARBITRUM_RPC_URL}")
        return
    print(f"Successfully connected to Arbitrum RPC.")

    nfpm_contract = w3.eth.contract(address=NFPM_ADDRESS, abi=NFPM_ABI)
    
    position_ids_to_track = get_position_ids_from_file()
    if not position_ids_to_track:
        print("No valid position IDs found in config file. Exiting.")
        return

    today_utc = datetime.now(timezone.utc)
    today_date_str = today_utc.strftime('%Y-%m-%d')
    yesterday_date_str = (today_utc - timedelta(days=1)).strftime('%Y-%m-%d')
    
    for position_nft_id in position_ids_to_track:
        position_key = f"position_{position_nft_id}"
        
        if position_key not in all_data:
            all_data[position_key] = {"history": {}}
        elif "history" not in all_data[position_key]:
             all_data[position_key]["history"] = {}

        print(f"\n--- Processing Position ID: {position_nft_id} for {today_date_str} ---")
        try:
            position_details = nfpm_contract.functions.positions(position_nft_id).call()
            token0_address_checksum = Web3.to_checksum_address(position_details[2])
            token1_address_checksum = Web3.to_checksum_address(position_details[3])

            collect_params = (
                position_nft_id,
                Web3.to_checksum_address(WALLET_ADDRESS),
                2**128 - 1, 
                2**128 - 1  
            )
            simulated_collect = nfpm_contract.functions.collect(collect_params).call({'from': Web3.to_checksum_address(WALLET_ADDRESS)})
            unclaimed_fees_token0_raw = simulated_collect[0]
            unclaimed_fees_token1_raw = simulated_collect[1]
            
            token0_contract = w3.eth.contract(address=token0_address_checksum, abi=ERC20_ABI_MINIMAL)
            token1_contract = w3.eth.contract(address=token1_address_checksum, abi=ERC20_ABI_MINIMAL)
            
            token0_decimals = token0_contract.functions.decimals().call()
            token1_decimals = token1_contract.functions.decimals().call()
            
            # Token-Symbole holen
            # Wenn token_pair_symbols bereits existiert, verwenden wir diese.
            # Ansonsten von der Blockchain holen und speichern.
            current_token0_symbol = None
            current_token1_symbol = None

            if "token_pair_symbols" in all_data[position_key] and all_data[position_key]["token_pair_symbols"]:
                symbols = all_data[position_key]["token_pair_symbols"].split('/')
                if len(symbols) == 2:
                    current_token0_symbol = symbols[0]
                    current_token1_symbol = symbols[1]
                    print(f"  Using stored token symbols: {current_token0_symbol}/{current_token1_symbol}")
            
            if not current_token0_symbol or not current_token1_symbol:
                current_token0_symbol = token0_contract.functions.symbol().call()
                current_token1_symbol = token1_contract.functions.symbol().call()
                all_data[position_key]["token_pair_symbols"] = f"{current_token0_symbol}/{current_token1_symbol}"
                print(f"  Fetched and stored token symbols: {current_token0_symbol}/{current_token1_symbol}")


            current_unclaimed_fees_token0_actual = unclaimed_fees_token0_raw / (10**token0_decimals)
            current_unclaimed_fees_token1_actual = unclaimed_fees_token1_raw / (10**token1_decimals)

            print(f"  Total Unclaimed Fees (Simulated Collect):")
            print(f"    {current_token0_symbol}: {current_unclaimed_fees_token0_actual:.8f} (Raw: {unclaimed_fees_token0_raw})")
            print(f"    {current_token1_symbol}: {current_unclaimed_fees_token1_actual:.8f} (Raw: {unclaimed_fees_token1_raw})")

            price_token0_usd = get_single_token_price_coingecko(token0_address_checksum)
            price_token1_usd = get_single_token_price_coingecko(token1_address_checksum)
            
            current_unclaimed_token0_usd_val = None
            current_unclaimed_token1_usd_val = None
            current_total_unclaimed_usd_val = None

            if price_token0_usd is not None:
                current_unclaimed_token0_usd_val = current_unclaimed_fees_token0_actual * price_token0_usd
                print(f"    {current_token0_symbol} USD Value (Total Unclaimed): ${current_unclaimed_token0_usd_val:.2f} (Price: ${price_token0_usd})")
            if price_token1_usd is not None:
                current_unclaimed_token1_usd_val = current_unclaimed_fees_token1_actual * price_token1_usd
                print(f"    {current_token1_symbol} USD Value (Total Unclaimed): ${current_unclaimed_token1_usd_val:.2f} (Price: ${price_token1_usd})")
            
            if current_unclaimed_token0_usd_val is not None and current_unclaimed_token1_usd_val is not None:
                current_total_unclaimed_usd_val = current_unclaimed_token0_usd_val + current_unclaimed_token1_usd_val
                print(f"    Total USD Value (Total Unclaimed): ${current_total_unclaimed_usd_val:.2f}")
            elif current_unclaimed_token0_usd_val is not None:
                 current_total_unclaimed_usd_val = current_unclaimed_token0_usd_val
                 print(f"    Total USD Value (Total Unclaimed, {current_token1_symbol} price missing): ${current_total_unclaimed_usd_val:.2f}")
            elif current_unclaimed_token1_usd_val is not None:
                 current_total_unclaimed_usd_val = current_unclaimed_token1_usd_val
                 print(f"    Total USD Value (Total Unclaimed, {current_token0_symbol} price missing): ${current_total_unclaimed_usd_val:.2f}")


            # Daten für den heutigen Tag vorbereiten
            today_data_entry = {
                # "token0_symbol" und "token1_symbol" werden hier nicht mehr gespeichert
                "total_unclaimed_fees": {
                    "token0_actual": current_unclaimed_fees_token0_actual,
                    "token1_actual": current_unclaimed_fees_token1_actual,
                    "token0_usd": current_unclaimed_token0_usd_val,
                    "token1_usd": current_unclaimed_token1_usd_val,
                    "total_usd": current_total_unclaimed_usd_val
                },
                "daily_earned_fees": {}, # Wird unten befüllt
                # "raw_data_optional" wird nicht mehr gespeichert
            }
            
            # Tägliche Gebühren berechnen
            yesterday_full_data = all_data[position_key]["history"].get(yesterday_date_str)
            
            daily_earned_token0_actual = current_unclaimed_fees_token0_actual
            daily_earned_token1_actual = current_unclaimed_fees_token1_actual
            
            if yesterday_full_data and "total_unclaimed_fees" in yesterday_full_data:
                yesterday_total_fees = yesterday_full_data["total_unclaimed_fees"]
                daily_earned_token0_actual -= yesterday_total_fees.get("token0_actual", 0)
                daily_earned_token1_actual -= yesterday_total_fees.get("token1_actual", 0)
                
                print(f"  Yesterday's total unclaimed for {position_nft_id}: "
                      f"{yesterday_total_fees.get('token0_actual', 0):.8f} {current_token0_symbol}, "
                      f"{yesterday_total_fees.get('token1_actual', 0):.8f} {current_token1_symbol}")
            else:
                print(f"  No data for {yesterday_date_str} for position {position_nft_id}. Total unclaimed is today's earned.")

            daily_earned_token0_actual = max(0, daily_earned_token0_actual)
            daily_earned_token1_actual = max(0, daily_earned_token1_actual)

            daily_earned_token0_usd_val = None
            daily_earned_token1_usd_val = None
            daily_total_earned_usd_val = None

            if price_token0_usd is not None:
                daily_earned_token0_usd_val = daily_earned_token0_actual * price_token0_usd
            if price_token1_usd is not None:
                daily_earned_token1_usd_val = daily_earned_token1_actual * price_token1_usd
            
            if daily_earned_token0_usd_val is not None and daily_earned_token1_usd_val is not None:
                daily_total_earned_usd_val = daily_earned_token0_usd_val + daily_earned_token1_usd_val
            elif daily_earned_token0_usd_val is not None:
                 daily_total_earned_usd_val = daily_earned_token0_usd_val
            elif daily_earned_token1_usd_val is not None:
                 daily_total_earned_usd_val = daily_earned_token1_usd_val


            today_data_entry["daily_earned_fees"] = {
                "token0_actual": daily_earned_token0_actual,
                "token1_actual": daily_earned_token1_actual,
                "token0_usd": daily_earned_token0_usd_val,
                "token1_usd": daily_earned_token1_usd_val,
                "total_usd": daily_total_earned_usd_val
            }
            
            all_data[position_key]["history"][today_date_str] = today_data_entry
            all_data[position_key]["last_updated_utc"] = today_utc.strftime('%Y-%m-%dT%H:%M:%SZ')


            print(f"\n  --- Fees Earned on {today_date_str} for Position {position_nft_id} ---")
            print(f"    {current_token0_symbol}: {daily_earned_token0_actual:.8f}")
            if daily_earned_token0_usd_val is not None: print(f"      USD Value: ${daily_earned_token0_usd_val:.2f}")
            print(f"    {current_token1_symbol}: {daily_earned_token1_actual:.8f}")
            if daily_earned_token1_usd_val is not None: print(f"      USD Value: ${daily_earned_token1_usd_val:.2f}")
            if daily_total_earned_usd_val is not None: print(f"    Total USD Value (Earned Today): ${daily_total_earned_usd_val:.2f}")
        
        except Exception as e_inner:
            print(f"An error occurred processing position ID {position_nft_id}: {e_inner}")
            import traceback
            traceback.print_exc()
            print(f"--- Finished processing position {position_nft_id} With Errors ---")
        
    save_json_data(all_data)
    print(f"\n--- Fee Tracker Finished All Positions ---")

if __name__ == "__main__":
    main()