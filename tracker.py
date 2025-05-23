# tracker.py
import os
import json
from datetime import datetime, timedelta, timezone
from web3 import Web3
from web3.exceptions import (
    BadFunctionCallOutput, 
    ContractLogicError, 
    # TransactionNotFound, # Für .call() meist nicht relevant
    # TimeExhausted, # Für .call() meist nicht relevant
    TooManyRequests, 
    ContractCustomError
    # ValidationError HIER ENTFERNT
)
from dotenv import load_dotenv
import requests
import math
import time # Für Retries in Coingecko

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

def load_json_data(filename=JSON_DATA_FILE): # Alte Version
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f: 
                return json.load(f)
        except json.JSONDecodeError: 
            print(f"Warning: Could not decode JSON from {filename}. Starting with empty data.")
            return {}
    return {}

def save_json_data(data, filename=JSON_DATA_FILE): # Alte Version
    try:
        with open(filename, 'w', encoding='utf-8') as f: 
            json.dump(data, f, indent=2)
        print(f"Data saved to {filename}")
    except Exception as e: 
        print(f"Error saving data to {filename}: {e}")

def get_single_token_price_coingecko(contract_address, platform_id="arbitrum-one", retries=3, delay=5):
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
        except requests.exceptions.HTTPError as http_err: print(f"    CoinGecko HTTP error for {contract_address}: {http_err}. Attempt {attempt + 1}/{retries}.")
        except requests.exceptions.ConnectionError as conn_err: print(f"    CoinGecko Connection error for {contract_address}: {conn_err}. Attempt {attempt + 1}/{retries}.")
        except requests.exceptions.Timeout as timeout_err: print(f"    CoinGecko Timeout error for {contract_address}: {timeout_err}. Attempt {attempt + 1}/{retries}.")
        except requests.exceptions.RequestException as req_err: print(f"    CoinGecko API request error for {contract_address}: {req_err}. Attempt {attempt + 1}/{retries}.")
        except json.JSONDecodeError as json_err: print(f"    Error decoding CoinGecko JSON response for {contract_address}: {json_err}. Attempt {attempt + 1}/{retries}.")
        except Exception as e_gen: print(f"    Unexpected error fetching price for {contract_address}: {e_gen}. Attempt {attempt + 1}/{retries}.")
        if attempt < retries - 1: 
            print(f"    Retrying CoinGecko in {delay} seconds...")
            time.sleep(delay)
        else: print(f"    All CoinGecko retries failed for {contract_address}.")
    return None

def get_active_position_config(filename=CONFIG_FILE_POSITIONS):
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                lines = f.readlines();
                if not lines: return None
                for ln, line in enumerate(lines,1):
                    line = line.strip()
                    if not line or line.startswith('#'): continue
                    parts = line.split(',')
                    if len(parts) == 2:
                        try: return {'id': int(parts[0].strip()), 'initial_investment_usd': float(parts[1].strip())}
                        except ValueError: print(f"Error: Ungültiges Format Zeile {ln} in '{filename}'"); return None
                    else: print(f"Error: Ungültiges Format Zeile {ln} in '{filename}'"); return None
                return None 
        else: print(f"Info: Config '{filename}' nicht gefunden."); return None
    except Exception as e: print(f"Fehler Lesen '{filename}': {e}"); return None

def calculate_time_in_range_percentage(price_ticks_filepath, position_range_data, hours_to_check=24):
    if not os.path.exists(price_ticks_filepath) or not position_range_data: return None
    if position_range_data.get("price_lower") is None or position_range_data.get("price_upper") is None: return None
    
    price_lower = min(position_range_data["price_lower"], position_range_data["price_upper"])
    price_upper = max(position_range_data["price_lower"], position_range_data["price_upper"])
    range_base_token = position_range_data.get("base_token_for_price")
    range_quote_token = position_range_data.get("quote_token_for_price")

    all_price_ticks = []
    if os.path.exists(price_ticks_filepath):
        with open(price_ticks_filepath, 'r', encoding='utf-8') as f:
            try:
                all_price_ticks = json.load(f)
                if not isinstance(all_price_ticks, list): return None
            except json.JSONDecodeError: return None
    else: return None 

    now_utc = datetime.now(timezone.utc)
    cutoff_time_utc = now_utc - timedelta(hours=hours_to_check)
    relevant_ticks, ticks_in_range = 0, 0

    for tick_entry in all_price_ticks:
        try:
            if not all([tick_entry.get("timestamp"), tick_entry.get("price"), tick_entry.get("base_token"), tick_entry.get("quote_token")]): continue
            if tick_entry.get("base_token") != range_base_token or tick_entry.get("quote_token") != range_quote_token: continue
            
            tick_dt = datetime.fromisoformat(tick_entry["timestamp"].replace("Z", "+00:00"))
            if tick_dt >= cutoff_time_utc:
                relevant_ticks += 1
                if price_lower <= tick_entry["price"] <= price_upper: ticks_in_range += 1
        except Exception: continue 
    if relevant_ticks == 0: return 0.0
    return (ticks_in_range / relevant_ticks) * 100

def main():
    print(f"--- Starting Uniswap V3 Fee Tracker ({datetime.now(timezone.utc).isoformat()}) ---")
    all_data = load_json_data(JSON_DATA_FILE)

    if not ARBITRUM_RPC_URL or not WALLET_ADDRESS: print("CRITICAL Error: Missing ENV VARS. Exiting."); return
    w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
    if not w3.is_connected(): print(f"CRITICAL Error: Not connected to RPC. Exiting."); return
    print(f"Successfully connected to Arbitrum RPC.")

    nfpm_contract = w3.eth.contract(address=NFPM_ADDRESS, abi=NFPM_ABI)
    active_pos_config = get_active_position_config()
    active_pos_id_cfg = active_pos_config['id'] if active_pos_config else None

    print("Aktualisiere 'is_active' Flags...")
    for pk_json in list(all_data.keys()):
        if pk_json.startswith("position_"):
            try:
                pid_json = int(pk_json.replace("position_","")); should_be_active = active_pos_id_cfg is not None and pid_json == active_pos_id_cfg
                if all_data[pk_json].get("is_active",False) != should_be_active: print(f"  {pk_json} -> is_active: {should_be_active}")
                all_data[pk_json]["is_active"] = should_be_active
                if should_be_active and active_pos_config and all_data[pk_json].get("initial_investment_usd") != active_pos_config['initial_investment_usd']:
                    all_data[pk_json]["initial_investment_usd"] = active_pos_config['initial_investment_usd']
                    print(f"  Initialinvestment für {pk_json} aktualisiert.")
            except ValueError: print(f"  ID für Key '{pk_json}' nicht parsebar.")

    if not active_pos_config:
        print("Keine aktive Position in Config."); save_json_data(all_data, JSON_DATA_FILE); print(f"\n--- Tracker Finished ---"); return

    pos_nft_id = active_pos_config['id']; pos_key = f"position_{pos_nft_id}"
    today_utc = datetime.now(timezone.utc); today_str = today_utc.strftime('%Y-%m-%d'); yday_str = (today_utc - timedelta(days=1)).strftime('%Y-%m-%d')

    if pos_key not in all_data: all_data[pos_key] = {"history":{}, "is_active":True, "initial_investment_usd":active_pos_config['initial_investment_usd'],"token_pair_symbols":""}
    elif "history" not in all_data[pos_key]: all_data[pos_key]["history"] = {}
    all_data[pos_key]["is_active"] = True

    print(f"\n--- Processing ACTIVE Position ID: {pos_nft_id} for {today_str} ---")
    try:
        position_details = nfpm_contract.functions.positions(pos_nft_id).call()
        
        t0_addr_cs = Web3.to_checksum_address(pos_details[2]); t1_addr_cs = Web3.to_checksum_address(pos_details[3])
        tick_low = pos_details[5]; tick_up = pos_details[6]

        t0_contract = w3.eth.contract(address=t0_addr_cs, abi=ERC20_ABI_MINIMAL)
        t1_contract = w3.eth.contract(address=t1_addr_cs, abi=ERC20_ABI_MINIMAL)

        t0_dec = t0_contract.functions.decimals().call()
        t1_dec = t1_contract.functions.decimals().call()
        
        curr_t0_sym, curr_t1_sym = "", ""
        if "token_pair_symbols" in all_data[pos_key] and all_data[pos_key]["token_pair_symbols"]:
            syms = all_data[pos_key]["token_pair_symbols"].split('/'); 
            if len(syms)==2: curr_t0_sym, curr_t1_sym = syms[0], syms[1]
        if not curr_t0_sym or not curr_t1_sym:
            curr_t0_sym = t0_contract.functions.symbol().call() or "T0S_ERR"
            curr_t1_sym = t1_contract.functions.symbol().call() or "T1S_ERR"
            all_data[pos_key]["token_pair_symbols"] = f"{curr_t0_sym}/{curr_t1_sym}"
        print(f"  Tokens: {curr_t0_sym}({t0_dec})/{curr_t1_sym}({t1_dec})")

        collect_p = (pos_nft_id, Web3.to_checksum_address(WALLET_ADDRESS), 2**128-1, 2**128-1)
        sim_collect = nfpm_contract.functions.collect(collect_p).call({'from': Web3.to_checksum_address(WALLET_ADDRESS)})
        
        uncl_fees_t0_act = sim_collect[0]/(10**t0_dec); uncl_fees_t1_act = sim_collect[1]/(10**t1_dec)
        
        price_t0_usd = get_single_token_price_coingecko(t0_addr_cs)
        price_t1_usd = get_single_token_price_coingecko(t1_addr_cs)
        curr_uncl_t0_usd, curr_uncl_t1_usd, curr_total_uncl_usd = None,None,None
        if price_t0_usd is not None: curr_uncl_t0_usd = uncl_fees_t0_act * price_t0_usd
        if price_t1_usd is not None: curr_uncl_t1_usd = uncl_fees_t1_act * price_t1_usd
        if curr_uncl_t0_usd is not None and curr_uncl_t1_usd is not None: curr_total_uncl_usd = curr_uncl_t0_usd + curr_uncl_t1_usd
        elif curr_uncl_t0_usd is not None: curr_total_uncl_usd = curr_uncl_t0_usd
        elif curr_uncl_t1_usd is not None: curr_total_uncl_usd = curr_uncl_t1_usd

        PRICE_PRESENTATION_IS_TOKEN0_BASE = True 
        
        pool_addr = WETH_WBTC_005_POOL_ADDRESS_ARBITRUM
        p_base_sym_json, p_quote_sym_json = "", ""
        if PRICE_PRESENTATION_IS_TOKEN0_BASE:
             p_base_sym_json, p_quote_sym_json = curr_t0_sym, curr_t1_sym
        else:
             p_base_sym_json, p_quote_sym_json = curr_t1_sym, curr_t0_sym

        p_low_calc, p_up_calc, curr_mkt_price_calc = None,None,None

        if pool_addr:
            pool_c = w3.eth.contract(address=pool_addr, abi=UNISWAP_V3_POOL_ABI_MINIMAL)
            slot0 = pool_c.functions.slot0().call() 
            
            price_lower_t1_per_t0 = tick_to_price(tick_low,t0_dec,t1_dec,True) 
            price_upper_t1_per_t0 = tick_to_price(tick_up,t0_dec,t1_dec,True)
            current_market_price_t1_per_t0 = sqrt_price_x96_to_price(slot0[0],t0_dec,t1_dec,True)

            if PRICE_PRESENTATION_IS_TOKEN0_BASE:
                p_low_calc,p_up_calc,curr_mkt_price_calc = price_lower_t1_per_t0,price_upper_t1_per_t0,current_market_price_t1_per_t0
            else:
                p_low_calc = 1/price_upper_t1_per_t0 if price_upper_t1_per_t0!=0 else None
                p_up_calc = 1/price_lower_t1_per_t0 if price_lower_t1_per_t0!=0 else None
                if p_low_calc is not None and p_up_calc is not None and p_low_calc > p_up_calc: p_low_calc,p_up_calc=p_up_calc,p_low_calc
                curr_mkt_price_calc = 1/current_market_price_t1_per_t0 if current_market_price_t1_per_t0!=0 else None
            
            print(f"  Range: [{p_low_calc:.6f} - {p_up_calc:.6f}] {p_quote_sym_json} per {p_base_sym_json}")
            if curr_mkt_price_calc is not None: print(f"  Mkt Price: {curr_mkt_price_calc:.6f} {p_quote_sym_json} per {p_base_sym_json}")
        else: print(f"  Pool Adresse für {pos_nft_id} nicht definiert.")

        today_entry = {
            "total_unclaimed_fees": {"token0_actual":uncl_fees_t0_act, "token1_actual":uncl_fees_t1_act, "token0_usd":curr_uncl_t0_usd, "token1_usd":curr_uncl_t1_usd, "total_usd":curr_total_uncl_usd},
            "daily_earned_fees": {},
            "position_range": {"price_lower":p_low_calc, "price_upper":p_up_calc, "current_market_price":curr_mkt_price_calc, "base_token_for_price":p_base_sym_json, "quote_token_for_price":p_quote_sym_json}
        }
        
        yday_data = all_data[pos_key]["history"].get(yday_str)
        daily_t0_act,daily_t1_act = uncl_fees_t0_act,uncl_fees_t1_act
        if yday_data and "total_unclaimed_fees" in yday_data:
            y_total = yday_data["total_unclaimed_fees"]
            daily_t0_act -= y_total.get("token0_actual",0.0); daily_t1_act -= y_total.get("token1_actual",0.0)
        daily_t0_act=max(0,daily_t0_act); daily_t1_act=max(0,daily_t1_act)

        daily_t0_usd,daily_t1_usd,daily_total_usd = None,None,None
        if price_t0_usd is not None: daily_t0_usd = daily_t0_act*price_t0_usd
        if price_t1_usd is not None: daily_t1_usd = daily_t1_act*price_t1_usd
        if daily_t0_usd is not None and daily_t1_usd is not None: daily_total_usd = daily_t0_usd+daily_t1_usd
        elif daily_t0_usd is not None: daily_total_usd = daily_t0_usd
        elif daily_t1_usd is not None: daily_total_usd = daily_t1_usd

        today_entry["daily_earned_fees"] = {"token0_actual":daily_t0_act, "token1_actual":daily_t1_act, "token0_usd":daily_t0_usd, "token1_usd":daily_t1_usd, "total_usd":daily_total_usd}
        all_data[pos_key]["history"][today_str] = today_entry
        
        time_in_range = calculate_time_in_range_percentage(PRICE_TICKS_FILE, today_entry["position_range"], 24)
        if time_in_range is not None:
            all_data[pos_key]["time_in_range_24h_percentage"] = time_in_range
            print(f"  Time in Range (last 24h): {time_in_range:.2f}%")
        else:
            if "time_in_range_24h_percentage" in all_data[pos_key]: del all_data[pos_key]["time_in_range_24h_percentage"]
            print(f"  Time in Range (last 24h): nicht berechenbar.")
            
        all_data[pos_key]["last_updated_utc"] = today_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

        print(f"\n  --- Fees Earned on {today_str} for Position {pos_nft_id} ---")
        print(f"    {curr_t0_sym}: {daily_t0_act:.8f} ($" + f"{(daily_t0_usd or 0.0):.2f})")
        print(f"    {curr_t1_sym}: {daily_t1_act:.8f} ($" + f"{(daily_t1_usd or 0.0):.2f})")
        if daily_total_usd is not None: print(f"    Total USD (Earned Today): ${daily_total_usd:.2f}")

    except Exception as e_inner:
        print(f"Error bei Position {pos_nft_id}: {e_inner}"); import traceback; traceback.print_exc()
            
    save_json_data(all_data, JSON_DATA_FILE) # Verwendung der alten save_json_data
    print(f"\n--- Fee Tracker Finished ---")

if __name__ == "__main__":
    main()