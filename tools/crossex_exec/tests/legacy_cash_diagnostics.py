"""Exercise the retained whole-account diagnostic without installing an open gate."""
from tools.crossex_exec.external_accounting import ExternalAccounting, read_snapshot, anchor_value
from tools.crossex_exec.external import ExternalRegistry
from tools.crossex_exec.funding import certify_daily


def snapshot(owner):
    value=read_snapshot(owner.accounts,owner.store,lambda:owner.now)
    value['external_fx']=owner.market.spot_fx(('USDC',)) if hasattr(owner.market,'spot_fx') else {}
    return value


def anchor(owner):
    value=snapshot(owner)
    registry=ExternalRegistry(owner.store,lambda:owner.now)
    revision=owner.store.meta('equity_baseline_revision',0)
    epoch=certify_daily(anchor_value(owner.store,value,registry.split(value),owner.now),
                        'audited_baseline',revision)
    owner.store.set_meta('external_equity_anchor',epoch)
    owner.store.set_meta('funding_checkpoint',None)


def prove(owner):
    value=snapshot(owner)
    owner.engine.account_cache.publish(value,owner.now)
    value=owner.engine.account_cache.read()
    registry=ExternalRegistry(owner.store,lambda:owner.now)
    return ExternalAccounting(owner.store,registry,lambda:owner.now).evaluate(value,registry.split(value))
