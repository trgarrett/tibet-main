import asyncio
import os
import sys
from pathlib import Path
from typing import List

from blspy import AugSchemeMPL, PrivateKey
from cdv.cmds.rpc import get_client
from chia.consensus.default_constants import DEFAULT_CONSTANTS
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.rpc.wallet_rpc_client import WalletRpcClient
from chia.simulator.simulator_full_node_rpc_client import \
    SimulatorFullNodeRpcClient
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import INFINITE_COST, Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend
from chia.types.condition_opcodes import ConditionOpcode
from chia.types.spend_bundle import SpendBundle
from chia.util.bech32m import (bech32_decode, bech32_encode, convertbits,
                               decode_puzzle_hash, encode_puzzle_hash)
from chia.util.condition_tools import conditions_dict_for_solution
from chia.util.config import load_config
from chia.util.hash import std_hash
from chia.util.ints import uint16, uint32, uint64
from chia.wallet.cat_wallet.cat_utils import (
    SpendableCAT,
    construct_cat_puzzle,
    unsigned_spend_bundle_for_spendable_cats,
)
from chia.wallet.derive_keys import master_sk_to_wallet_sk_unhardened
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.puzzles.load_clvm import load_clvm
from chia.wallet.puzzles.p2_conditions import puzzle_for_conditions
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import (
    DEFAULT_HIDDEN_PUZZLE_HASH, calculate_synthetic_secret_key, puzzle_for_pk,
    puzzle_for_synthetic_public_key, solution_for_delegated_puzzle)
from chia.wallet.puzzles.singleton_top_layer_v1_1 import (
    P2_SINGLETON_MOD, SINGLETON_LAUNCHER, SINGLETON_LAUNCHER_HASH,
    SINGLETON_MOD, SINGLETON_MOD_HASH, generate_launcher_coin,
    launch_conditions_and_coinsol, lineage_proof_for_coinsol,
    pay_to_singleton_puzzle, puzzle_for_singleton, solution_for_singleton)
from chia.wallet.puzzles.tails import GenesisById
from chia.wallet.sign_coin_spends import sign_coin_spends
from chia.wallet.trading.offer import OFFER_MOD, OFFER_MOD_HASH, Offer
from chia.wallet.util.puzzle_compression import (
    compress_object_with_puzzles,
    decompress_object_with_puzzles,
    lowest_best_version,
)
from chia_rs import run_chia_program
from clvm.casts import int_to_bytes

async def get_private_key_DO_NOT_CALL_OUTSIDE_THIS_FILE(wallet_client):
    fingerprint = await wallet_client.get_logged_in_fingerprint()

    sk_resp = await wallet_client.get_private_key(fingerprint)
    sk_hex = sk_resp['sk']
    return PrivateKey.from_bytes(bytes.fromhex(sk_hex))

async def get_standard_coin_puzzle(wallet_client, std_coin):
    master_sk = await get_private_key_DO_NOT_CALL_OUTSIDE_THIS_FILE(wallet_client)

    i = 0
    while i < 10000:
        wallet_sk = master_sk_to_wallet_sk_unhardened(master_sk, i)
        synth_secret_key = calculate_synthetic_secret_key(wallet_sk, DEFAULT_HIDDEN_PUZZLE_HASH)
        synth_key = synth_secret_key.get_g1()
        puzzle = puzzle_for_synthetic_public_key(synth_key)
        puzzle_hash = puzzle.get_tree_hash()            
        if puzzle_hash == std_coin.puzzle_hash:
            return puzzle
        i += 1

    return None

async def sign_spend_bundle(wallet_client, sb, additional_data=DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA, no_max_keys = 1):
    master_sk = await get_private_key_DO_NOT_CALL_OUTSIDE_THIS_FILE(wallet_client)

    puzzle_hashes = [c.coin.puzzle_hash for c in sb.coin_spends]
    keys_used = 0
    i = 0
    while i < 10000:
        wallet_sk = master_sk_to_wallet_sk_unhardened(master_sk, i)
        synth_secret_key = calculate_synthetic_secret_key(wallet_sk, DEFAULT_HIDDEN_PUZZLE_HASH)
        synth_key = synth_secret_key.get_g1()
        puzzle = puzzle_for_synthetic_public_key(synth_key)
        puzzle_hash = puzzle.get_tree_hash()            
        if puzzle_hash in puzzle_hashes:
            keys_used += 1
            async def pk_to_sk(pk):
                return synth_secret_key

            sig_old = sb.aggregated_signature
            sb = await sign_coin_spends(
                sb.coin_spends,
                pk_to_sk,
                additional_data,
                DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
            )

            new_agg_sig = AugSchemeMPL.aggregate([sig_old, sb.aggregated_signature])
            sb = SpendBundle(sb.coin_spends, new_agg_sig)

        if keys_used >= no_max_keys:
            return sb
        i += 1

    return sb

async def sign_spend_bundle_with_specific_sk(sb, sk, additional_data=DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA):
    async def pk_to_sk(pk):
        return sk

    sig_old = sb.aggregated_signature
    sb = await sign_coin_spends(
        sb.coin_spends,
        pk_to_sk,
        additional_data,
        DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
    )
    new_agg_sig = AugSchemeMPL.aggregate([sig_old, sb.aggregated_signature])
    
    return SpendBundle(sb.coin_spends, new_agg_sig)
    