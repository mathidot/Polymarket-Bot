from web3 import Web3
from config import WEB3_PROVIDER


# Expose a shared Web3 instance for on-chain interactions
w3 = Web3(Web3.HTTPProvider(WEB3_PROVIDER))