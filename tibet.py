import click
import json
import sys
import os
import asyncio
from tibet_lib import *
from private_key_things import *
from chia.wallet.util.wallet_types import WalletType

@click.group()
def cli():
    pass

cached_config = None

def get_config_item(*args):
    global cached_config
    if cached_config is None:
        get_config()
    
    ret = cached_config
    for arg in args:
        if ret is None:
            return ret
        ret = ret.get(arg, None)
    
    return ret

def get_config():
    global cached_config
    if cached_config is None:
        try:
            cached_config = json.loads(open("config.json", "r").read())
        except:
            open("config.json", "w").write("{}")
            cached_config = {}
        
    return cached_config


def save_config(config):
    cached_config = config
    open("config.json", "w").write(json.dumps(config, sort_keys=True, indent=4))


@click.command()
@click.option('--chia-root', default=None, help='Chia root directory (e.g., ~/.chia/mainnet)')
@click.option('--use-preset', default='custom', type=click.Choice(['custom', 'simulator', 'testnet10', 'mainnet'], case_sensitive=False))
def config_node(chia_root, use_preset):
    if use_preset == 'custom' and (chia_root is None or full_node_rpc_port is None or wallet_rpc_port is None):
        click.echo("Use a preset or fill out all options.")
        sys.exit(1)
    
    if use_preset == "mainnet":
        chia_root = "~/.chia/mainnet"
    elif use_preset == "testnet10":
        chia_root = "~/.chia/testnet10"
    elif use_preset == "simulator":
        chia_root = "~/.chia/simulator/main"
    
    chia_root = os.path.expanduser(chia_root)

    config = get_config()
    config["chia_root"] = chia_root
    save_config(config)
    click.echo("Config updated and saved successfully.")


@click.command()
def test_node_config():
    asyncio.run(_test_node_config())

async def _test_node_config():
    full_node_client = await get_full_node_client(get_config_item("chia_root"))
    full_node_client_status = await full_node_client.healthz()
    click.echo("full node client... " + str(full_node_client_status))

    full_node_client.close()
    await full_node_client.await_closed()

    wallet_client = await get_wallet_client(get_config_item("chia_root"))
    wallet_client_status = await wallet_client.healthz()
    click.echo("wallet client... " + str(wallet_client_status))

    wallet_client.close()
    await wallet_client.await_closed()


@click.command()
@click.option("--push-tx", is_flag=True, show_default=True, default=False, help="Push the signed spend bundle to the network and update launcher is in config.")
def launch_router(push_tx):
    asyncio.run(_launch_router(push_tx))

async def _launch_router(push_tx):
    wallet_client = await get_wallet_client(get_config_item("chia_root"))

    coins = await wallet_client.select_coins(2, 1, min_coin_amount=2) # wallet id 1, amount 2

    coin = coins[0]
    coin_puzzle = await get_standard_coin_puzzle(wallet_client, coin)

    click.echo(f"Using coin 0x{coin.name().hex()}...")

    launcher_id, sb = await launch_router_from_coin(coin, coin_puzzle)
    click.echo(f"Router launcher id: {launcher_id}")

    signed_sb = await sign_spend_bundle(wallet_client, sb)

    if push_tx:
        click.echo(f"Pushing tx...")
        full_node_client = await get_full_node_client(get_config_item("chia_root"))
        resp = await full_node_client.push_tx(signed_sb)
        click.echo(resp)
        full_node_client.close()
        await full_node_client.await_closed()

        click.echo("Saving config...")
        config = get_config()
        config["router_launcher_id"] = launcher_id
        config["router_last_processed_id"] = launcher_id
        config["pairs"] = {}
        save_config(config)
        click.echo("Done.")
    else:
        open("spend_bundle.json", "w").write(json.dumps(signed_sb.to_json_dict(), sort_keys=True, indent=4))
        click.echo("Spend bundle written to spend_bundle.json.")
        click.echo("Use --push-tx to broadcast this spend.")

    wallet_client.close()
    await wallet_client.await_closed()

@click.command()
@click.option('--amount', default=1000000, help='Amount, in CATs (1 CAT = 1000 mojos)')
@click.option("--push-tx", is_flag=True, show_default=True, default=False, help="Push the signed spend bundle to the network and add cat to wallet.")
def launch_test_token(amount, push_tx):
    asyncio.run(_launch_test_token(amount, push_tx))


async def _launch_test_token(amount, push_tx):
    click.echo(f"Creating test CAT with a supply of {amount} ({amount * 1000} mojos used)...")
    wallet_client = await get_wallet_client(get_config_item("chia_root"))

    coins = await wallet_client.select_coins(amount * 1000, 1, min_coin_amount=amount * 1000) # wallet id 1 = XCH

    coin = coins[0]
    coin_puzzle = await get_standard_coin_puzzle(wallet_client, coin)

    click.echo(f"Using coin 0x{coin.name().hex()}...")

    tail_id, sb = await create_test_cat(amount, coin, coin_puzzle)
    click.echo(f"Token asset id: {tail_id}")

    signed_sb = await sign_spend_bundle(wallet_client, sb)

    if push_tx:
        click.echo(f"Pushing tx...")
        full_node_client = await get_full_node_client(get_config_item("chia_root"))
        resp = await full_node_client.push_tx(signed_sb)
        click.echo(resp)
        full_node_client.close()
        await full_node_client.await_closed()

        click.echo("Adding asset id to wallet...")
        resp = await wallet_client.create_wallet_for_existing_cat(bytes.fromhex(tail_id))
        click.echo(resp)
        click.echo("Done.")
    else:
        open("spend_bundle.json", "w").write(json.dumps(signed_sb.to_json_dict(), sort_keys=True, indent=4))
        click.echo("Spend bundle written to spend_bundle.json.")
        click.echo("Use --push-tx to broadcast this spend.")

    wallet_client.close()
    await wallet_client.await_closed()


@click.command()
@click.option('--asset-id', required=True, help='Asset id (TAIL hash) of token to be offered in pair (token-XCH)')
@click.option("--push-tx", is_flag=True, show_default=True, default=False, help="Push the signed spend bundle to the network and add liquidity CAT to wallet.")
def create_pair(asset_id, push_tx):
    # very basic check to prevent most mistakes
    if len(asset_id) != 64:
        click.echo("Oops! That asset id doesn't look right...")
        sys.exit(1)

    asyncio.run(_create_pair(asset_id, push_tx))


async def _create_pair(tail_hash, push_tx):
    click.echo(f"Creating pair for {tail_hash}...")

    router_launcher_id = get_config_item("router_launcher_id")
    router_last_processed_id = get_config_item("router_last_processed_id")
    if router_launcher_id is None or router_last_processed_id is None:
        click.echo("Oops - looks like someone forgot to launch their router.")
        sys.exit(1)
    
    click.echo("But first, we do a little sync")
    full_node_client = await get_full_node_client(get_config_item("chia_root"))
    current_router_coin, latest_creation_spend, pairs = await sync_router(full_node_client, router_last_processed_id)
    router_last_processed_id_new = current_router_coin.name().hex()
    click.echo(f"Last router id: {router_last_processed_id_new}")

    if len(pairs) != 0 or router_last_processed_id_new != router_last_processed_id:
        click.echo("New pairs found! Saving them...")
        router_last_processed_id = router_last_processed_id_new

        config = get_config()
        config["router_last_processed_id"] = router_last_processed_id
        config["pairs"] = config.get("pairs", {})
        for pair in pairs:
            if config["pairs"].get(pair[0], -1) == -1:
                config["pairs"][pair[0]] = pair[1]
        save_config(config)

    wallet_client = await get_wallet_client(get_config_item("chia_root"))
    coins = await wallet_client.select_coins(2, 1, min_coin_amount=2) # wallet id 1 = XCH

    coin = coins[0]
    coin_puzzle = await get_standard_coin_puzzle(wallet_client, coin)

    click.echo(f"Using coin 0x{coin.name().hex()}...")

    pair_launcher_id, sb = await create_pair_from_coin(
        coin,
        coin_puzzle,
        tail_hash,
        router_launcher_id,
        current_router_coin,
        latest_creation_spend
    )
    click.echo(f"Pair launcher id: {pair_launcher_id}")

    pair_liquidity_tail_hash = pair_liquidity_tail_puzzle(bytes.fromhex(pair_launcher_id)).get_tree_hash().hex()
    click.echo(f"Liquidity asset id: {pair_liquidity_tail_hash}")

    signed_sb = await sign_spend_bundle(wallet_client, sb)

    if push_tx:
        click.echo(f"Pushing tx...")
        resp = await full_node_client.push_tx(signed_sb)
        click.echo(resp)
        
        click.echo("Adding liquidity asset id to wallet...")
        resp = await wallet_client.create_wallet_for_existing_cat(bytes.fromhex(pair_liquidity_tail_hash))
        click.echo(resp)
        click.echo("Done.")
    else:
        open("spend_bundle.json", "w").write(json.dumps(signed_sb.to_json_dict(), sort_keys=True, indent=4))
        click.echo("Spend bundle written to spend_bundle.json.")
        click.echo("Use --push-tx to broadcast this spend.")

    wallet_client.close()
    await wallet_client.await_closed()
    full_node_client.close()
    await full_node_client.await_closed()


@click.command()
def sync_pairs():
    asyncio.run(_sync_pairs())


async def _sync_pairs():
    router_last_processed_id = get_config_item("router_last_processed_id")
    if router_last_processed_id is None or len(router_last_processed_id) != 64:
        click.echo("No router launcher id. Please either set it or launch a new router.")
        sys.exit(1)
    
    full_node_client = await get_full_node_client(get_config_item("chia_root"))

    current_router_coin, latest_creation_spend, pairs = await sync_router(full_node_client, router_last_processed_id)
    router_last_processed_id_new = current_router_coin.name().hex()
    click.echo(f"Last router id: {router_last_processed_id_new}")

    if len(pairs) != 0 or router_last_processed_id_new != router_last_processed_id:
        click.echo("New pairs found! Saving them...")
        router_last_processed_id = router_last_processed_id_new

        config = get_config()
        config["router_last_processed_id"] = router_last_processed_id
        config["pairs"] = config.get("pairs", {})
        for pair in pairs:
            if config["pairs"].get(pair[0], -1) == -1:
                config["pairs"][pair[0]] = pair[1]
        save_config(config)

    click.echo("Bye!")
    full_node_client.close()
    await full_node_client.await_closed()


@click.command()
@click.option("--asset-id", required=True, help='Asset id (TAIL hash) of token to be offered in pair (token-XCH)')
@click.option("--offer", default=None, help='Offer to build liquidity tx from. By default, a new offer will be generated. You can also provide the offer directly or the path to a file containing the offer.')
@click.option("--token-amount", default=0, help="If offer is none, this amount of tokens will be asked for in the offer. Unit is mojos (1 CAT = 1000 mojos).")
@click.option("--xch-amount", default=0, help="Only required if pair has no liquidity. If offer is none, this amount of XCH will be asked for in the generated offer. Unit is mojos.")
@click.option("--push-tx", is_flag=True, show_default=True, default=False, help="Push the signed spend bundle to the network and add liquidity CAT to wallet.")
def deposit_liquidity(asset_id, offer, xch_amount, token_amount, push_tx):
    if len(asset_id) != 64:
        click.echo("Oops! That asset id doesn't look right...")
        sys.exit(1)
    asyncio.run(_deposit_liquidity(asset_id, offer, xch_amount, token_amount, push_tx))


async def _deposit_liquidity(token_tail_hash, offer, xch_amount, token_amount, push_tx):
    click.echo("Depositing liquidity...")
    offer_str = ""

    pair_launcher_id = get_config_item("pairs", token_tail_hash)
    if pair_launcher_id is None:
        click.echo("Corresponding pair launcher id not found in config - you might want to sync-pairs or launch-pair.")
        sys.exit(1)

    full_node_client = await get_full_node_client(get_config_item("chia_root"))

    last_synced_pair_id = get_config_item("pair_sync", pair_launcher_id)
    last_synced_pair_id_not_none = last_synced_pair_id
    if last_synced_pair_id_not_none is None:
        last_synced_pair_id_not_none = pair_launcher_id

    current_pair_coin, creation_spend, pair_state = await sync_pair(full_node_client, last_synced_pair_id_not_none, bytes.fromhex(token_tail_hash))
    current_pair_coin_id = current_pair_coin.name().hex()
    click.echo(f"Current pair coin id: {current_pair_coin_id}")

    if offer is not None:
        click.echo("No need to generate new offer.")
        if os.path.isfile(offer):
            offer_str = open(offer, "r").read().strip()
        else:
            offer_str = offer
    else:
        click.echo("Generating new offer...")

        if token_amount == 0:
            click.echo("Please set ---token-amount to use this option.")
            full_node_client.close()
            await full_node_client.await_closed()
            sys.exit(1)

        pair_liquidity_tail_hash = pair_liquidity_tail_puzzle(bytes.fromhex(pair_launcher_id)).get_tree_hash().hex()
        click.echo(f"Liquidity asset id: {pair_liquidity_tail_hash}")

        wallet_client = await get_wallet_client(get_config_item("chia_root"))
        wallets = await wallet_client.get_wallets(wallet_type = WalletType.CAT)
        
        token_wallet_id = next((_['id'] for _ in wallets if _['data'].startswith(token_tail_hash)), None)
        liquidity_wallet_id = next((_['id'] for _ in wallets if _['data'].startswith(pair_liquidity_tail_hash)), None)

        if token_wallet_id is None or liquidity_wallet_id is None:
            click.echo("You don't have a wallet for the token and/or the pair liquidity token. Please set them up before using this command.")
            wallet_client.close()
            await wallet_client.await_closed()
            full_node_client.close()
            await full_node_client.await_closed()
            sys.exit(1)

        liquidity_token_amount = token_amount
        if pair_state['liquidity'] != 0:
            liquidity_token_amount = pair_state['liquidity'] * token_amount // pair_state['token_reserve']
            xch_amount = liquidity_token_amount * pair_state['xch_reserve'] // pair_state['liquidity']

        offer_dict = {}
        offer_dict[1] = - xch_amount - liquidity_token_amount # also for liqiudity TAIL creation
        offer_dict[token_wallet_id] = -token_amount
        offer_dict[liquidity_wallet_id] = liquidity_token_amount
        offer_resp = await wallet_client.create_offer_for_ids(offer_dict)
        offer = offer_resp[0]

        offer_str = offer.to_bech32()
        open("offer.txt", "w").write(offer_str)

        click.echo("Offer successfully generated and saved to offer.txt.")

        wallet_client.close()
        await wallet_client.await_closed()

    xch_reserve_coin, token_reserve_coin, token_reserve_lineage_proof = await get_pair_reserve_info(
        full_node_client,
        bytes.fromhex(pair_launcher_id),
        current_pair_coin,
        bytes.fromhex(token_tail_hash),
        creation_spend
    )

    if current_pair_coin_id != last_synced_pair_id:
        click.echo("Pair state updated since last sync; saving it...")
        config = get_config()
        config["pair_sync"] = config.get("pair_sync", {})
        config["pair_sync"][pair_launcher_id] = current_pair_coin_id
        save_config(config)

    sb = await respond_to_deposit_liquidity_offer(
        bytes.fromhex(pair_launcher_id),
        current_pair_coin,
        creation_spend,
        bytes.fromhex(token_tail_hash),
        pair_state["liquidity"],
        pair_state["xch_reserve"],
        pair_state["token_reserve"],
        offer_str,
        xch_reserve_coin,
        token_reserve_coin,
        token_reserve_lineage_proof
    )

    if push_tx:
        click.echo(f"Pushing tx...")
        resp = await full_node_client.push_tx(sb)
        click.echo(resp)
        click.echo("Enjoy your lp fees!")
    else:
        open("spend_bundle.json", "w").write(json.dumps(sb.to_json_dict(), sort_keys=True, indent=4))
        click.echo("Spend bundle written to spend_bundle.json.")
        click.echo("Use --push-tx to broadcast this spend.")

    full_node_client.close()
    await full_node_client.await_closed()


@click.command()
@click.option("--asset-id", required=True, help='Asset id (TAIL hash) of token part of the pair (token-XCH)')
@click.option("--offer", default=None, help='Offer to build liquidity removal tx from. By default, a new offer will be generated. You can also provide the offer directly or the path to a file containing the offer.')
@click.option("--liquidity-token-amount", default=0, help="If offer is none, this amount of liqudity tokens will be included in the offer. Unit is mojos (1 CAT = 1000 mojos).")
@click.option("--push-tx", is_flag=True, show_default=True, default=False, help="Push the signed spend bundle to the network.")
def remove_liquidity(asset_id, offer, liquidity_token_amount, push_tx):
    if len(asset_id) != 64:
        click.echo("Oops! That asset id doesn't look right...")
        sys.exit(1)
    asyncio.run(_remove_liquidity(asset_id, offer, liquidity_token_amount, push_tx))


async def _remove_liquidity(token_tail_hash, offer, liquidity_token_amount, push_tx):
    click.echo("Removing liquidity...")
    offer_str = ""

    pair_launcher_id = get_config_item("pairs", token_tail_hash)
    if pair_launcher_id is None:
        click.echo("Corresponding pair launcher id not found in config - you might want to sync-pairs.")
        sys.exit(1)

    full_node_client = await get_full_node_client(get_config_item("chia_root"))

    last_synced_pair_id = get_config_item("pair_sync", pair_launcher_id)
    last_synced_pair_id_not_none = last_synced_pair_id
    if last_synced_pair_id_not_none is None:
        last_synced_pair_id_not_none = pair_launcher_id

    current_pair_coin, creation_spend, pair_state = await sync_pair(full_node_client, last_synced_pair_id_not_none, bytes.fromhex(token_tail_hash))
    current_pair_coin_id = current_pair_coin.name().hex()
    click.echo(f"Current pair coin id: {current_pair_coin_id}")

    if offer is not None:
        click.echo("No need to generate new offer.")
        if os.path.isfile(offer):
            offer_str = open(offer, "r").read().strip()
        else:
            offer_str = offer
    else:
        click.echo("Generating new offer...")

        if liquidity_token_amount == 0:
            click.echo("Please set ---liquidity-token-amount to use this option.")
            full_node_client.close()
            await full_node_client.await_closed()
            sys.exit(1)

        pair_liquidity_tail_hash = pair_liquidity_tail_puzzle(bytes.fromhex(pair_launcher_id)).get_tree_hash().hex()
        click.echo(f"Liquidity asset id: {pair_liquidity_tail_hash}")

        wallet_client = await get_wallet_client(get_config_item("chia_root"))
        wallets = await wallet_client.get_wallets(wallet_type = WalletType.CAT)
        
        token_wallet_id = next((_['id'] for _ in wallets if _['data'].startswith(token_tail_hash)), None)
        liquidity_wallet_id = next((_['id'] for _ in wallets if _['data'].startswith(pair_liquidity_tail_hash)), None)

        if token_wallet_id is None or liquidity_wallet_id is None:
            click.echo("You don't have a wallet for the token and/or the pair liquidity token. Please set them up before using this command.")
            wallet_client.close()
            await wallet_client.await_closed()
            full_node_client.close()
            await full_node_client.await_closed()
            sys.exit(1)

        
        token_amount = pair_state['token_reserve'] * liquidity_token_amount // pair_state['liquidity']
        xch_amount = pair_state['xch_reserve'] * liquidity_token_amount // pair_state['liquidity']

        offer_dict = {}
        offer_dict[1] = xch_amount + liquidity_token_amount # also offer xch from liquidity cat burn
        offer_dict[token_wallet_id] = token_amount
        offer_dict[liquidity_wallet_id] = -liquidity_token_amount
        offer_resp = await wallet_client.create_offer_for_ids(offer_dict)
        offer = offer_resp[0]

        offer_str = offer.to_bech32()
        open("offer.txt", "w").write(offer_str)

        click.echo("Offer successfully generated and saved to offer.txt.")

        wallet_client.close()
        await wallet_client.await_closed()

    xch_reserve_coin, token_reserve_coin, token_reserve_lineage_proof = await get_pair_reserve_info(
        full_node_client,
        bytes.fromhex(pair_launcher_id),
        current_pair_coin,
        bytes.fromhex(token_tail_hash),
        creation_spend
    )

    if current_pair_coin_id != last_synced_pair_id:
        click.echo("Pair state updated since last sync; saving it...")
        config = get_config()
        config["pair_sync"] = config.get("pair_sync", {})
        config["pair_sync"][pair_launcher_id] = current_pair_coin_id
        save_config(config)

    sb = await respond_to_remove_liquidity_offer(
        bytes.fromhex(pair_launcher_id),
        current_pair_coin,
        creation_spend,
        bytes.fromhex(token_tail_hash),
        pair_state["liquidity"],
        pair_state["xch_reserve"],
        pair_state["token_reserve"],
        offer_str,
        xch_reserve_coin,
        token_reserve_coin,
        token_reserve_lineage_proof
    )

    if push_tx:
        click.echo(f"Pushing tx...")
        resp = await full_node_client.push_tx(sb)
        click.echo(resp)
        click.echo("Enjoy your lp fees!")
    else:
        open("spend_bundle.json", "w").write(json.dumps(sb.to_json_dict(), sort_keys=True, indent=4))
        click.echo("Spend bundle written to spend_bundle.json.")
        click.echo("Use --push-tx to broadcast this spend.")

    full_node_client.close()
    await full_node_client.await_closed()


@click.command()
@click.option("--asset-id", required=True, help='Asset id (TAIL hash) of token part of the pair (token-XCH)')
@click.option("--offer", default=None, help='Offer to build tx from. By default, a new offer will be generated. You can also provide the offer directly or the path to a file containing the offer.')
@click.option("--xch-amount", default=0, help="If offer is none, this amount of xch will be included in the offer. Unit is mojos.")
@click.option("--push-tx", is_flag=True, show_default=True, default=False, help="Push the spend bundle to the network.")
def xch_to_token(asset_id, offer, xch_amount, push_tx):
    if len(asset_id) != 64:
        click.echo("Oops! That asset id doesn't look right...")
        sys.exit(1)
    asyncio.run(_xch_to_token(asset_id, offer, xch_amount, push_tx))


async def _xch_to_token(token_tail_hash, offer, xch_amount, push_tx):
    click.echo("Swapping XCH for token...")
    offer_str = ""

    pair_launcher_id = get_config_item("pairs", token_tail_hash)
    if pair_launcher_id is None:
        click.echo("Corresponding pair launcher id not found in config - you might want to sync-pairs.")
        sys.exit(1)

    full_node_client = await get_full_node_client(get_config_item("chia_root"))

    last_synced_pair_id = get_config_item("pair_sync", pair_launcher_id)
    last_synced_pair_id_not_none = last_synced_pair_id
    if last_synced_pair_id_not_none is None:
        last_synced_pair_id_not_none = pair_launcher_id

    current_pair_coin, creation_spend, pair_state = await sync_pair(full_node_client, last_synced_pair_id_not_none, bytes.fromhex(token_tail_hash))
    current_pair_coin_id = current_pair_coin.name().hex()
    click.echo(f"Current pair coin id: {current_pair_coin_id}")

    if offer is not None:
        click.echo("No need to generate new offer.")
        if os.path.isfile(offer):
            offer_str = open(offer, "r").read().strip()
        else:
            offer_str = offer
    else:
        click.echo("Generating new offer...")

        if xch_amount == 0:
            click.echo("Please set ---xch-amount to use this option.")
            full_node_client.close()
            await full_node_client.await_closed()
            sys.exit(1)

        pair_liquidity_tail_hash = pair_liquidity_tail_puzzle(bytes.fromhex(pair_launcher_id)).get_tree_hash().hex()
        click.echo(f"Liquidity asset id: {pair_liquidity_tail_hash}")

        wallet_client = await get_wallet_client(get_config_item("chia_root"))
        wallets = await wallet_client.get_wallets(wallet_type = WalletType.CAT)
        
        token_wallet_id = next((_['id'] for _ in wallets if _['data'].startswith(token_tail_hash)), None)
        
        if token_wallet_id is None:
            click.echo("You don't have a wallet for the token offered in the pair. Please set them up before using this command.")
            wallet_client.close()
            await wallet_client.await_closed()
            full_node_client.close()
            await full_node_client.await_closed()
            sys.exit(1)
        
        token_amount = 997 * xch_amount * pair_state['token_reserve'] // (1000 * pair_state['xch_reserve'] + 997 * xch_amount)

        click.echo(f"You'll receive {token_amount / 1000} tokens from this trade.")
        if token_amount == 0:
            wallet_client.close()
            await wallet_client.await_closed()
            full_node_client.close()
            await full_node_client.await_closed()

        offer_dict = {}
        offer_dict[1] = -xch_amount # offer XCH
        offer_dict[token_wallet_id] = token_amount # ask for token
        offer_resp = await wallet_client.create_offer_for_ids(offer_dict)
        offer = offer_resp[0]

        offer_str = offer.to_bech32()
        open("offer.txt", "w").write(offer_str)

        click.echo("Offer successfully generated and saved to offer.txt.")

        wallet_client.close()
        await wallet_client.await_closed()

    xch_reserve_coin, token_reserve_coin, token_reserve_lineage_proof = await get_pair_reserve_info(
        full_node_client,
        bytes.fromhex(pair_launcher_id),
        current_pair_coin,
        bytes.fromhex(token_tail_hash),
        creation_spend
    )

    if current_pair_coin_id != last_synced_pair_id:
        click.echo("Pair state updated since last sync; saving it...")
        config = get_config()
        config["pair_sync"] = config.get("pair_sync", {})
        config["pair_sync"][pair_launcher_id] = current_pair_coin_id
        save_config(config)

    sb = await respond_to_swap_offer(
        bytes.fromhex(pair_launcher_id),
        current_pair_coin,
        creation_spend,
        bytes.fromhex(token_tail_hash),
        pair_state["liquidity"],
        pair_state["xch_reserve"],
        pair_state["token_reserve"],
        offer_str,
        xch_reserve_coin,
        token_reserve_coin,
        token_reserve_lineage_proof
    )

    if push_tx:
        click.echo(f"Pushing tx...")
        resp = await full_node_client.push_tx(sb)
        click.echo(resp)
        click.echo("Enjoy your shiny new tokens!")
    else:
        open("spend_bundle.json", "w").write(json.dumps(sb.to_json_dict(), sort_keys=True, indent=4))
        click.echo("Spend bundle written to spend_bundle.json.")
        click.echo("Use --push-tx to broadcast this spend.")

    full_node_client.close()
    await full_node_client.await_closed()


@click.command()
@click.option("--asset-id", required=True, help='Asset id (TAIL hash) of token part of the pair (token-XCH)')
@click.option("--offer", default=None, help='Offer to build tx from. By default, a new offer will be generated. You can also provide the offer directly or the path to a file containing the offer.')
@click.option("--token-amount", default=0, help="If offer is none, this amount of tokens will be included in the offer. Unit is mojos (1 CAT = 1000 mojos).")
@click.option("--push-tx", is_flag=True, show_default=True, default=False, help="Push the spend bundle to the network.")
def token_to_xch(asset_id, offer, token_amount, push_tx):
    if len(asset_id) != 64:
        click.echo("Oops! That asset id doesn't look right...")
        sys.exit(1)
    asyncio.run(_token_to_xch(asset_id, offer, token_amount, push_tx))


async def _token_to_xch(token_tail_hash, offer, token_amount, push_tx):
    click.echo("Swapping token for XCH...")
    offer_str = ""

    pair_launcher_id = get_config_item("pairs", token_tail_hash)
    if pair_launcher_id is None:
        click.echo("Corresponding pair launcher id not found in config - you might want to sync-pairs.")
        sys.exit(1)

    full_node_client = await get_full_node_client(get_config_item("chia_root"))

    last_synced_pair_id = get_config_item("pair_sync", pair_launcher_id)
    last_synced_pair_id_not_none = last_synced_pair_id
    if last_synced_pair_id_not_none is None:
        last_synced_pair_id_not_none = pair_launcher_id

    current_pair_coin, creation_spend, pair_state = await sync_pair(full_node_client, last_synced_pair_id_not_none, bytes.fromhex(token_tail_hash))
    current_pair_coin_id = current_pair_coin.name().hex()
    click.echo(f"Current pair coin id: {current_pair_coin_id}")

    if offer is not None:
        click.echo("No need to generate new offer.")
        if os.path.isfile(offer):
            offer_str = open(offer, "r").read().strip()
        else:
            offer_str = offer
    else:
        click.echo("Generating new offer...")

        if token_amount == 0:
            click.echo("Please set ---token-amount to use this option.")
            full_node_client.close()
            await full_node_client.await_closed()
            sys.exit(1)

        pair_liquidity_tail_hash = pair_liquidity_tail_puzzle(bytes.fromhex(pair_launcher_id)).get_tree_hash().hex()
        click.echo(f"Liquidity asset id: {pair_liquidity_tail_hash}")

        wallet_client = await get_wallet_client(get_config_item("chia_root"))
        wallets = await wallet_client.get_wallets(wallet_type = WalletType.CAT)
        
        token_wallet_id = next((_['id'] for _ in wallets if _['data'].startswith(token_tail_hash)), None)
        
        if token_wallet_id is None:
            click.echo("You don't have a wallet for the token offered in the pair. Please set them up before using this command.")
            wallet_client.close()
            await wallet_client.await_closed()
            full_node_client.close()
            await full_node_client.await_closed()
            sys.exit(1)

        xch_amount = 997 * token_amount * pair_state['xch_reserve'] // (1000 * pair_state['token_reserve'] + 997 * token_amount)

        click.echo(f"You'll receive {xch_amount / 1000000000000} XCH from this trade.")
        if token_amount == 0:
            wallet_client.close()
            await wallet_client.await_closed()
            full_node_client.close()
            await full_node_client.await_closed()

        offer_dict = {}
        offer_dict[1] = xch_amount # ask for XCH
        offer_dict[token_wallet_id] = -token_amount # offer tokens
        offer_resp = await wallet_client.create_offer_for_ids(offer_dict)
        offer = offer_resp[0]

        offer_str = offer.to_bech32()
        open("offer.txt", "w").write(offer_str)

        click.echo("Offer successfully generated and saved to offer.txt.")

        wallet_client.close()
        await wallet_client.await_closed()

    xch_reserve_coin, token_reserve_coin, token_reserve_lineage_proof = await get_pair_reserve_info(
        full_node_client,
        bytes.fromhex(pair_launcher_id),
        current_pair_coin,
        bytes.fromhex(token_tail_hash),
        creation_spend
    )

    if current_pair_coin_id != last_synced_pair_id:
        click.echo("Pair state updated since last sync; saving it...")
        config = get_config()
        config["pair_sync"] = config.get("pair_sync", {})
        config["pair_sync"][pair_launcher_id] = current_pair_coin_id
        save_config(config)

    sb = await respond_to_swap_offer(
        bytes.fromhex(pair_launcher_id),
        current_pair_coin,
        creation_spend,
        bytes.fromhex(token_tail_hash),
        pair_state["liquidity"],
        pair_state["xch_reserve"],
        pair_state["token_reserve"],
        offer_str,
        xch_reserve_coin,
        token_reserve_coin,
        token_reserve_lineage_proof
    )
    
    if push_tx:
        click.echo(f"Pushing tx...")
        resp = await full_node_client.push_tx(sb)
        click.echo(resp)
        click.echo("Enjoy your shiny new tokens!")
    else:
        open("spend_bundle.json", "w").write(json.dumps(sb.to_json_dict(), sort_keys=True, indent=4))
        click.echo("Spend bundle written to spend_bundle.json.")
        click.echo("Use --push-tx to broadcast this spend.")

    full_node_client.close()
    await full_node_client.await_closed()


if __name__ == "__main__":
    cli.add_command(config_node)
    cli.add_command(test_node_config)
    cli.add_command(launch_router)
    cli.add_command(launch_test_token)
    cli.add_command(create_pair)
    cli.add_command(sync_pairs)
    cli.add_command(deposit_liquidity)
    cli.add_command(remove_liquidity)
    cli.add_command(xch_to_token)
    cli.add_command(token_to_xch)
    cli()