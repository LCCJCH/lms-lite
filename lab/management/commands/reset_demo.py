"""刪除全部業務資料與 superuser 以外的所有帳號，再完整執行 seed_demo（簡報 §11）。

必須加 --yes 才會執行；未設定 DEMO_SEED_PASSWORD 時不刪除任何資料。
群組不刪除，由 seed_demo 重設權限。
"""
import os

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from lab.models import Request, TestItem


class Command(BaseCommand):
    help = "重置展示資料：刪除全部業務資料與非 superuser 帳號，再執行 seed_demo（須加 --yes）"

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="確認執行重置")

    def handle(self, *args, **options):
        requests = Request.objects.all()
        test_items = TestItem.objects.all()
        users = User.objects.filter(is_superuser=False)
        summary = f"申請單 {requests.count()} 張、檢測項目 {test_items.count()} 筆、非 superuser 帳號 {users.count()} 個"

        if not options["yes"]:
            raise CommandError(f"將刪除：{summary}。確認無誤請加 --yes 重新執行；未刪除任何資料。")
        if not os.environ.get("DEMO_SEED_PASSWORD", ""):
            raise CommandError("環境變數 DEMO_SEED_PASSWORD 未設定，未刪除任何資料。")

        with transaction.atomic():
            # 先刪申請單：CASCADE 帶走項目、排程、歷程，解除它們對 User、TestItem 的 PROTECT
            requests.delete()
            test_items.delete()
            users.delete()
            self.stdout.write(f"已刪除：{summary}")
            call_command("seed_demo", stdout=self.stdout, stderr=self.stderr)

        self.stdout.write(self.style.SUCCESS("reset_demo 完成。"))
