"""Stock movements. Every change to on-hand quantity goes through move(); it locks the balance row, refuses
negative stock (unless explicitly allowed) and writes the stock ledger, so stock_balance always equals the sum of
the ledger. Costing is moving average per company (receive_cost)."""
from decimal import Decimal

from .. import db
from ..errors import conflict
from ..models import Item, StockBalance, StockLedger
from .common import user_id


def on_hand(branch_id, item_id, lock=False):
    sql = 'SELECT qty, reserved FROM stock_balance WHERE branch_id=:b AND item_id=:i' + (' FOR UPDATE' if lock else '')
    r = db.one(sql, b=branch_id, i=item_id)
    return (r['qty'], r['reserved']) if r else (0, 0)


def available(branch_id, item_id):
    q, r = on_hand(branch_id, item_id)
    return q - r


def move(company, branch_id, item_id, qty, txn_type, ref, date, unit_cost, *, allow_negative=False, respect_reserved=False):
    """Add (qty > 0) or remove (qty < 0) stock. Raises negative_stock when there is not enough."""
    if not qty:
        return
    s = db.session()
    bal = s.get(StockBalance, (branch_id, item_id), with_for_update=True)
    if bal is None:
        bal = StockBalance(branch_id=branch_id, item_id=item_id, qty=0, reserved=0)
        s.add(bal)
        s.flush()
    free = bal.qty - (bal.reserved if respect_reserved else 0)
    if qty < 0 and not allow_negative and free + qty < 0:
        it = s.get(Item, item_id)
        name = it.name if it else item_id
        extra = f' ({bal.reserved} reserved for online orders)' if respect_reserved and bal.reserved else ''
        raise conflict(f'Not enough stock of {name} at {branch_id}: {free} available{extra}, {-qty} needed. Negative stock is blocked.',
                       'negative_stock', item=item_id, available=free)
    bal.qty += qty
    s.add(StockLedger(company=company, branch_id=branch_id, item_id=item_id, txn_date=date, qty=qty, unit_cost=Decimal(str(unit_cost or 0)),
                      txn_type=txn_type, ref=ref, created_by=user_id()))


def reserve(branch_id, item_id, qty):
    """Hold stock for an online order (qty > 0) or release the hold (qty < 0)."""
    s = db.session()
    bal = s.get(StockBalance, (branch_id, item_id), with_for_update=True)
    if bal is None:
        bal = StockBalance(branch_id=branch_id, item_id=item_id, qty=0, reserved=0)
        s.add(bal)
    if qty > 0 and bal.qty - bal.reserved < qty:
        raise conflict(f'Only {bal.qty - bal.reserved} of {item_id} are free at the warehouse.', 'insufficient_stock', item=item_id)
    bal.reserved = max(0, bal.reserved + qty)


def company_qty(company, item_id):
    return int(db.val('''SELECT COALESCE(SUM(s.qty),0) FROM stock_balance s JOIN branches b ON b.id=s.branch_id
                         WHERE b.company=:c AND s.item_id=:i''', c=company, i=item_id) or 0)


def receive_cost(company, item_id, qty_in, landed_rate):
    """Moving-average cost after receiving qty_in at landed_rate (call BEFORE moving the stock in)."""
    it = db.session().get(Item, item_id, with_for_update=True)
    old_qty = max(0, company_qty(company, item_id))
    old_cost = Decimal(str(it.cost or 0))
    new = (old_cost * old_qty + Decimal(str(landed_rate)) * qty_in) / (old_qty + qty_in) if (old_qty + qty_in) else Decimal(str(landed_rate))
    it.cost = new.quantize(Decimal('0.0001'))
    return it.cost
