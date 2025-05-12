import os
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from web3 import Web3
from dotenv import load_dotenv
import requests 

load_dotenv()

# --- Konfiguration ---
ARBITRUM_RPC_URL = os.getenv('ARBITRUM_RPC')
WALLET_ADDRESS = os.getenv('WALLET_ADDRESS')
POSITION_NFT_ID = int(os.getenv('POSITION_ID'))
DB_NAME = "fees_history.db"

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

# --- Datenbankfunktionen ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Schema mit den neuen USD-Spalten
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_unclaimed_fees (
            date TEXT PRIMARY KEY,
            token0_symbol TEXT,
            token1_symbol TEXT,
            tokens_owed0_raw INTEGER,
            tokens_owed1_raw INTEGER,
            tokens_owed0_actual REAL,
            tokens_owed1_actual REAL,
            token0_usd_value REAL, 
            token1_usd_value REAL,
            total_usd_value REAL 
        )
    ''')
    conn.commit()
    conn.close()

def get_fees_for_date(date_str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT tokens_owed0_actual, tokens_owed1_actual, token0_symbol, token1_symbol, 
               token0_usd_value, token1_usd_value, total_usd_value 
        FROM daily_unclaimed_fees WHERE date = ?
    """, (date_str,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "token0_actual": row[0], "token1_actual": row[1], 
            "token0_symbol": row[2], "token1_symbol": row[3],
            "token0_usd_value": row[4], "token1_usd_value": row[5],
            "total_usd_value": row[6]
        }
    return None

def save_fees_for_date(date_str, token0_symbol, token1_symbol, 
                       owed0_raw, owed1_raw, owed0_actual, owed1_actual,
                       token0_usd=None, token1_usd=None, total_usd=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO daily_unclaimed_fees 
        (date, token0_symbol, token1_symbol, tokens_owed0_raw, tokens_owed1_raw, 
         tokens_owed0_actual, tokens_owed1_actual, 
         token0_usd_value, token1_usd_value, total_usd_value) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (date_str, token0_symbol, token1_symbol, owed0_raw, owed1_raw, 
          owed0_actual, owed1_actual, token0_usd, token1_usd, total_usd))
    conn.commit()
    conn.close()

# --- Funktion zum Holen von Preisen (CoinGecko - Angepasst) ---
def get_single_token_price_coingecko(contract_address, platform_id="arbitrum-one"):
    """ Holt den Preis für eine einzelne Kontraktadresse auf einer Plattform. """
    try:
        checksum_address = Web3.to_checksum_address(contract_address)
        # Nutze den /coins/{platform_id}/contract/{contract_address} Endpunkt
        url = f"https://api.coingecko.com/api/v3/coins/{platform_id}/contract/{checksum_address}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        # Der Preis ist in data['market_data']['current_price']['usd']
        price_usd = data.get("market_data", {}).get("current_price", {}).get("usd")
        if price_usd is None:
            print(f"Warning: Could not parse USD price for {checksum_address} from CoinGecko response: {data}")
        return price_usd
    except requests.exceptions.RequestException as e:
        print(f"CoinGecko API request error for {contract_address}: {e}")
    except Exception as e_gen:
        print(f"Error fetching price for {contract_address}: {e_gen}")
    return None

# --- Hauptlogik ---
def main():
    print("--- Starting Uniswap V3 Fee Tracker ---")
    init_db() 

    if not ARBITRUM_RPC_URL or not WALLET_ADDRESS or not POSITION_NFT_ID:
        print("Error: Missing environment variables")
        return

    w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC_URL))
    if not w3.is_connected():
        print(f"Error: Could not connect to Arbitrum RPC")
        return
    print(f"Successfully connected to Arbitrum RPC.")

    nfpm_contract = w3.eth.contract(address=NFPM_ADDRESS, abi=NFPM_ABI)

    today_utc = datetime.now(timezone.utc)
    today_date_str = today_utc.strftime('%Y-%m-%d')
    yesterday_date_str = (today_utc - timedelta(days=1)).strftime('%Y-%m-%d')
    
    print(f"Fetching data for position ID: {POSITION_NFT_ID} on {today_date_str}")

    try:
        position_details = nfpm_contract.functions.positions(POSITION_NFT_ID).call()
        token0_address_checksum = Web3.to_checksum_address(position_details[2])
        token1_address_checksum = Web3.to_checksum_address(position_details[3])
        print(f"Token0 Address: {token0_address_checksum}, Token1 Address: {token1_address_checksum}")

        collect_params = (
            POSITION_NFT_ID,
            Web3.to_checksum_address(WALLET_ADDRESS),
            2**128 - 1, 
            2**128 - 1  
        )
        simulated_collect = nfpm_contract.functions.collect(collect_params).call({'from': Web3.to_checksum_address(WALLET_ADDRESS)})
        unclaimed_fees_token0_raw = simulated_collect[0]
        unclaimed_fees_token1_raw = simulated_collect[1]
        print(f"Simulated collect amounts (Raw): Token0: {unclaimed_fees_token0_raw}, Token1: {unclaimed_fees_token1_raw}")
        
        token0_contract = w3.eth.contract(address=token0_address_checksum, abi=ERC20_ABI_MINIMAL)
        token1_contract = w3.eth.contract(address=token1_address_checksum, abi=ERC20_ABI_MINIMAL)
        
        token0_decimals = token0_contract.functions.decimals().call()
        token1_decimals = token1_contract.functions.decimals().call()
        token0_symbol = token0_contract.functions.symbol().call()
        token1_symbol = token1_contract.functions.symbol().call()

        current_unclaimed_fees_token0_actual = unclaimed_fees_token0_raw / (10**token0_decimals)
        current_unclaimed_fees_token1_actual = unclaimed_fees_token1_raw / (10**token1_decimals)

        print(f"\nTotal Unclaimed Fees for Position ID {POSITION_NFT_ID} as of {today_date_str} (from simulated collect):")
        print(f"  {token0_symbol}: {current_unclaimed_fees_token0_actual:.8f} (Raw: {unclaimed_fees_token0_raw})")
        print(f"  {token1_symbol}: {current_unclaimed_fees_token1_actual:.8f} (Raw: {unclaimed_fees_token1_raw})")

        price_token0_usd = get_single_token_price_coingecko(token0_address_checksum)
        price_token1_usd = get_single_token_price_coingecko(token1_address_checksum)
        
        current_unclaimed_token0_usd = 0
        current_unclaimed_token1_usd = 0
        current_total_unclaimed_usd = 0

        if price_token0_usd is not None:
            current_unclaimed_token0_usd = current_unclaimed_fees_token0_actual * price_token0_usd
            print(f"  {token0_symbol} USD Value (Total Unclaimed): ${current_unclaimed_token0_usd:.2f} (Price: ${price_token0_usd})")
        if price_token1_usd is not None:
            current_unclaimed_token1_usd = current_unclaimed_fees_token1_actual * price_token1_usd
            print(f"  {token1_symbol} USD Value (Total Unclaimed): ${current_unclaimed_token1_usd:.2f} (Price: ${price_token1_usd})")
        
        if price_token0_usd is not None and price_token1_usd is not None:
            current_total_unclaimed_usd = current_unclaimed_token0_usd + current_unclaimed_token1_usd
            print(f"  Total USD Value (Total Unclaimed): ${current_total_unclaimed_usd:.2f}")

        save_fees_for_date(today_date_str, token0_symbol, token1_symbol, 
                           unclaimed_fees_token0_raw, unclaimed_fees_token1_raw,
                           current_unclaimed_fees_token0_actual, current_unclaimed_fees_token1_actual,
                           current_unclaimed_token0_usd if price_token0_usd else None, 
                           current_unclaimed_token1_usd if price_token1_usd else None,
                           current_total_unclaimed_usd if price_token0_usd and price_token1_usd else None)
        print(f"Saved today's total unclaimed fees (and USD values if available) to database.")

        yesterday_fees_data = get_fees_for_date(yesterday_date_str)
        
        daily_earned_token0_actual = current_unclaimed_fees_token0_actual
        daily_earned_token1_actual = current_unclaimed_fees_token1_actual

        if yesterday_fees_data:
            daily_earned_token0_actual -= yesterday_fees_data.get("token0_actual", 0)
            daily_earned_token1_actual -= yesterday_fees_data.get("token1_actual", 0)
            print(f"Yesterday's total unclaimed: {yesterday_fees_data.get('token0_symbol', 'N/A')}: {yesterday_fees_data.get('token0_actual', 0):.8f}, {yesterday_fees_data.get('token1_symbol', 'N/A')}: {yesterday_fees_data.get('token1_actual', 0):.8f}")
        else:
            print(f"No data found for {yesterday_date_str}. Displaying total unclaimed as today's earned.")

        daily_earned_token0_actual = max(0, daily_earned_token0_actual)
        daily_earned_token1_actual = max(0, daily_earned_token1_actual)

        print(f"\n--- Fees Earned on {today_date_str} ---")
        print(f"  {token0_symbol}: {daily_earned_token0_actual:.8f}")
        print(f"  {token1_symbol}: {daily_earned_token1_actual:.8f}")

        daily_earned_token0_usd = 0
        daily_earned_token1_usd = 0
        daily_total_earned_usd = 0

        if price_token0_usd is not None:
            daily_earned_token0_usd = daily_earned_token0_actual * price_token0_usd
            print(f"  {token0_symbol} USD Value (Earned Today): ${daily_earned_token0_usd:.2f}")
        if price_token1_usd is not None:
            daily_earned_token1_usd = daily_earned_token1_actual * price_token1_usd
            print(f"  {token1_symbol} USD Value (Earned Today): ${daily_earned_token1_usd:.2f}")
        
        if price_token0_usd is not None and price_token1_usd is not None:
            daily_total_earned_usd = daily_earned_token0_usd + daily_earned_token1_usd
            print(f"  Total USD Value (Earned Today): ${daily_total_earned_usd:.2f}")
        
        print("--- Fee Tracker Finished Successfully ---")

    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()
        print("--- Fee Tracker Finished With Errors ---")

if __name__ == "__main__":
    main()