from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from calendar import monthrange
from decimal import Decimal
from harvest.controllers.billing_controller import tax_inclusive
import logging

from harvest.controllers.billing_controller import BillingController

PRICE_PER_ACCOUNT = 500
TAX = Decimal("1.10")
CLOSING_DATE = 25


class Billing:
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)

    users = None

    def __init__(self, host, port, user_id=None):
        self.billing = BillingController(host, port)
        if user_id:
            self.user_id = user_id
            self.billing.set_user_id(user_id)

    def set_users(self, users):
        self.users = list({user["user_id"]: user for user in users}.values())

    def show(self, user_id=None):
        if not user_id:
            user_id = self.user_id

        billing_data = self.billing.show(user_id)

        ret = {
            "price": billing_data["history"][-1]["price"],
            "closed_on": self.get_closing_date(),
            "billed_on": self.get_cycle(),
            "status": self.get_status(),
        }
        ret.update(billing_data)
        return ret

    def calc_price(self, account_cnt, month=None, raw=False):
        if raw:
            price = account_cnt * PRICE_PER_ACCOUNT
        else:
            price = tax_inclusive(account_cnt * PRICE_PER_ACCOUNT, TAX)
        return price

    # TODO: 月指定での請求日を求められる機能があったほうが良いかも
    def get_cycle(self, needs_isoformat=True):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if CLOSING_DATE < now.day:
            now = now + relativedelta(months=1)

        last_day = monthrange(now.year, now.month)[1]
        cycle = now.replace(day=last_day, hour=0, minute=0, second=0, microsecond=0)
        return cycle.isoformat() if needs_isoformat else cycle

    def get_closing_date(self, needs_isoformat=True):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if CLOSING_DATE < now.day:
            now = now + relativedelta(months=1)

        closing = now.replace(day=CLOSING_DATE, hour=0, minute=0, second=0, microsecond=0)
        return closing.isoformat() if needs_isoformat else closing

    def get_status(self):
        status = "pending"
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        closing_date = self.get_closing_date(needs_isoformat=False)
        if now >= closing_date:
            status = "fixed"
        return status

    # 最初の請求レコードを作成
    def create(self, user_id=None):
        if not user_id:
            user_id = self.user_id
        if self.users is not None:
            users_count = len(self.users)
        else:
            # TODO: 専用例外を投げるべき
            raise Exception

        histories = self.billing.list_history(user_id)
        invoice_date = datetime.fromisoformat(self.get_cycle())
        closing_date = datetime.fromisoformat(self.get_closing_date())
        start_date = closing_date - relativedelta(months=1) + relativedelta(days=1)
        invoice_number = next(
            (
                item["invoice_number"]
                for item in histories
                if item["invoice_date"] == invoice_date.strftime("%Y/%m/%d")
            ),
            len(histories) + 1,
        )
        return self.billing.create(
            context="history_" + invoice_date.strftime("%Y%m"),
            period="%s-%s" % (start_date.strftime("%Y/%m/%d"), closing_date.strftime("%Y/%m/%d")),
            invoice_date=invoice_date.strftime("%Y/%m/%d"),
            invoice_number=invoice_number,
            user_id=user_id,
            raw_price=self.calc_price(users_count, raw=True),
            tax=TAX,
            users_count=users_count,
            baseline_member_ids=[user["user_id"] for user in self.users],
        )

    # アカウント作成の際にusers_countやpriceを再計算して既存レコードを更新
    def update(self, event, user_id, member_id=None):
        if event != "create_user":
            raise ValueError("Unsupported billing event")
        invoice_date = datetime.fromisoformat(self.get_cycle())
        context = "history_" + invoice_date.strftime("%Y%m")
        return self.billing.increment_member(user_id, context, member_id, PRICE_PER_ACCOUNT, TAX)
