import os
import sys
from dotenv import load_dotenv
from web3 import Web3


def sanitize_pk(pk: str) -> str:
    pk = pk.strip().strip('"').strip("'")
    if not pk:
        raise ValueError("PK is empty after sanitization")
    return pk if pk.startswith("0x") else "0x" + pk


def derive_addresses(pk_hex: str) -> tuple[str, str]:
    try:
        account = Web3().eth.account.from_key(pk_hex)
    except Exception as e:
        raise ValueError(f"Invalid private key: {e}")
    address = account.address
    checksum_address = Web3.to_checksum_address(address)
    return address, checksum_address


def main() -> None:
    load_dotenv(".env")

    # Accept PK from CLI arg or .env
    arg_pk = None
    if len(sys.argv) > 1:
        if sys.argv[1] in ("--pk", "-k") and len(sys.argv) > 2:
            arg_pk = sys.argv[2]
        else:
            arg_pk = sys.argv[1]

    pk_env = os.getenv("PK")
    pk_raw = arg_pk or pk_env
    if not pk_raw:
        print("❌ PK not provided. Pass with '--pk <hex>' or set 'PK' in .env")
        sys.exit(1)

    try:
        pk_hex = sanitize_pk(pk_raw)
        address, checksum = derive_addresses(pk_hex)
    except ValueError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

    print(f"✅ Derived address: {address}")
    print(f"✅ Checksum address: {checksum}")

    bot_addr_env = os.getenv("BOT_TRADER_ADDRESS")
    if bot_addr_env:
        try:
            bot_checksum = Web3.to_checksum_address(bot_addr_env)
            match = bot_checksum == checksum
            print(f"🔎 BOT_TRADER_ADDRESS (.env): {bot_addr_env}")
            print(f"🔎 BOT checksum: {bot_checksum}")
            print(f"🔗 Match: {'YES' if match else 'NO'}")
        except Exception as e:
            print(f"⚠️ Invalid BOT_TRADER_ADDRESS in .env: {e}")


if __name__ == "__main__":
    main()