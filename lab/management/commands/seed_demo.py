"""建立展示用的群組、帳號、檢測項目與示範申請單（簡報 §11）。可重複執行，不重複建立。

密碼取自環境變數 DEMO_SEED_PASSWORD。示範申請單一律經由 lab.services 建立，
因此自帶狀態歷程；每種狀態各至少一張。
"""
import os
from datetime import timedelta

from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.roles import ADMIN, APPLICANT, LAB_STAFF
from lab import services
from lab.models import Request, TestItem

# Admin 群組在 /admin/ 只能管理這三個 model（app_label, model）
ADMIN_MODELS = [("auth", "user"), ("auth", "group"), ("lab", "testitem")]
ADMIN_ACTIONS = ["view", "add", "change", "delete"]

USERS = [
    # username, 顯示名稱, 群組, is_staff
    ("demo_admin", "示範管理者", ADMIN, True),
    ("lab1", "王實驗", LAB_STAFF, False),
    ("lab2", "李實驗", LAB_STAFF, False),
    ("applicant1", "陳申請", APPLICANT, False),
    ("applicant2", "林申請", APPLICANT, False),
]

TEST_ITEMS = [
    # 分類, 名稱, 單位, 定量極限（空白顯示「—」）, 說明；完全依 docs/DEMO-TESTITEMS-LOQ-v1.md
    # 類內依此順序顯示（reset_demo 依序重建，pk 即顯示順序）
    # a. 有機（organic）— 20 項
    ("organic", "農藥殘留（多重殘留）", "mg/kg", "0.01", "LC-MS/MS、GC-MS/MS；參考 MOHWP0055.05（111.08.17）"),
    ("organic", "塑化劑（鄰苯二甲酸酯類）", "mg/kg", "0.05", "GC-MS；參考食藥署「食品中鄰苯二甲酸酯類塑化劑檢驗方法」"),
    ("organic", "苯駢芘及 PAH4", "µg/kg", "0.5", "GC-MS/MS；參考食藥署 110 年多環芳香烴檢驗說明資料"),
    ("organic", "黃麴毒素（B1/B2/G1/G2）", "µg/kg", "0.5", "LC-MS/MS；參考 MOHWT0010.03（114.10.29）"),
    ("organic", "3-單氯丙二醇（3-MCPD）", "mg/kg", "0.05", "GC-MS；參考部授食字第1021950046號"),
    ("organic", "防腐劑（苯甲酸、己二烯酸）", "g/kg", "0.02", "HPLC；參考 MOHWA0020.02"),
    ("organic", "防腐劑（對羥苯甲酸酯類）", "g/kg", "0.005", "HPLC；參考 MOHWA0020.02"),
    ("organic", "咖啡因", "mg/100 mL", "0.5", "HPLC；參考衛授食字第1061902225號"),
    ("organic", "三聚氰胺", "mg/kg", "0.1", "示範值，非引用公告方法"),
    ("organic", "順丁烯二酸", "mg/kg", "1", "示範值，非引用公告方法"),
    ("organic", "一級芳香胺", "mg/kg", "0.01", "示範值，非引用公告方法"),
    ("organic", "甲醛", "mg/kg", "1", "示範值，非引用公告方法"),
    ("organic", "氯苯／氯酚類", "mg/kg", "0.01", "示範值，非引用公告方法"),
    ("organic", "喹寧", "mg/kg", "1", "示範值，非引用公告方法"),
    ("organic", "雙酚類（BPA/BPS/BPF）", "mg/kg", "0.01", "示範值，非引用公告方法"),
    ("organic", "EDTA", "mg/kg", "1", "示範值，非引用公告方法"),
    ("organic", "酚", "mg/kg", "0.05", "示範值，非引用公告方法"),
    ("organic", "甲苯酚", "mg/kg", "0.05", "示範值，非引用公告方法"),
    ("organic", "銅葉綠素鈉", "mg/kg", "1", "示範值，非引用公告方法"),
    ("organic", "全氟烷基物質（PFAS）", "µg/kg", "0.1", "示範值，非引用公告方法"),
    # b. 無機（inorganic）— 22 項
    ("inorganic", "鉛（Pb）", "mg/kg", "0.02", "ICP-MS；參考 MOHWH0030.00"),
    ("inorganic", "鎘（Cd）", "mg/kg", "0.02", "ICP-MS；參考 MOHWH0030.00"),
    ("inorganic", "汞（Hg）", "mg/kg", "0.02", "ICP-MS；參考 MOHWH0030.00"),
    ("inorganic", "砷（As）", "mg/kg", "0.05", "ICP-MS；參考 MOHWH0030.00"),
    ("inorganic", "銅（Cu）", "mg/kg", "0.05", "ICP-MS；參考 MOHWH0030.00"),
    ("inorganic", "錫（Sn）", "mg/kg", "5", "ICP-OES；參考衛授食字第1091902451號（金屬罐裝食品）"),
    ("inorganic", "硼（B）", "mg/kg", "0.5", "示範值，非引用公告方法"),
    ("inorganic", "鈉（Na）", "mg/kg", "5", "示範值，非引用公告方法"),
    ("inorganic", "鎂（Mg）", "mg/kg", "5", "示範值，非引用公告方法"),
    ("inorganic", "鋁（Al）", "mg/kg", "0.5", "示範值，非引用公告方法"),
    ("inorganic", "鉀（K）", "mg/kg", "5", "示範值，非引用公告方法"),
    ("inorganic", "鈣（Ca）", "mg/kg", "5", "示範值，非引用公告方法"),
    ("inorganic", "鉻（Cr）", "mg/kg", "0.05", "示範值，非引用公告方法"),
    ("inorganic", "錳（Mn）", "mg/kg", "0.05", "示範值，非引用公告方法"),
    ("inorganic", "鐵（Fe）", "mg/kg", "0.5", "示範值，非引用公告方法"),
    ("inorganic", "鈷（Co）", "mg/kg", "0.02", "示範值，非引用公告方法"),
    ("inorganic", "鎳（Ni）", "mg/kg", "0.05", "示範值，非引用公告方法"),
    ("inorganic", "鋅（Zn）", "mg/kg", "0.5", "示範值，非引用公告方法"),
    ("inorganic", "鍺（Ge）", "mg/kg", "0.05", "示範值，非引用公告方法"),
    ("inorganic", "硒（Se）", "mg/kg", "0.02", "示範值，非引用公告方法"),
    ("inorganic", "銀（Ag）", "mg/kg", "0.02", "示範值，非引用公告方法"),
    ("inorganic", "金（Au）", "mg/kg", "0.02", "示範值，非引用公告方法"),
    # c. 物化性（physchem）— 2 項
    ("physchem", "水分", "%", "", "105°C 烘乾減重法"),
    ("physchem", "pH 值", "—", "", "玻璃電極法，25°C"),
    # d. 其他（other）— 3 項
    ("other", "總生菌數", "CFU/g", "10", "平板計數法，35°C 培養 48 小時；LOQ 為示範值"),
    ("other", "化學需氧量（COD）", "mg/L", "5", "重鉻酸鉀迴流法（NIEA W515）；LOQ 為示範值"),
    ("other", "生化需氧量（BOD）", "mg/L", "2", "五日培養法（NIEA W510）；LOQ 為示範值"),
]

RETURN_REASON = "樣品描述不足，請補充保存條件與樣品數量後重新送出。"

DEMO_REQUESTS = [
    # 申請者, 樣品名稱, 樣品描述, 檢測項目, 目標狀態, 申請者備註
    # 樣品名稱彼此不同：seed 以（申請者, 樣品名稱）判斷是否已建立
    ("applicant1", "烏龍茶葉", "真空包裝 200 g，常溫保存", ["鉛（Pb）", "鎘（Cd）"], Request.Status.DRAFT,
     "示範資料：初始狀態為草稿"),
    ("applicant1", "糙米", "夾鏈袋 500 g，冷藏", ["鉛（Pb）", "鎘（Cd）", "砷（As）"], Request.Status.SUBMITTED,
     "示範資料：初始狀態為已送出"),
    ("applicant1", "醬油", "玻璃瓶", ["水分", "pH 值", "3-單氯丙二醇（3-MCPD）"],
     Request.Status.RETURNED,
     "示範資料：初始狀態為已退回"),
    ("applicant2", "龍眼蜂蜜", "玻璃瓶 300 g，常溫保存", ["水分", "總生菌數"], Request.Status.SUBMITTED,
     "示範資料：初始狀態為已送出"),
    ("applicant2", "高麗菜", "塑膠袋 1 顆，冷藏", ["鉛（Pb）", "鎘（Cd）", "水分", "農藥殘留（多重殘留）"],
     Request.Status.ACCEPTED,
     "示範資料：初始狀態為已受理"),
    ("applicant1", "花生粉", "夾鏈袋 300 g，乾燥保存", ["砷（As）", "水分", "黃麴毒素（B1/B2/G1/G2）"],
     Request.Status.SCHEDULED,
     "示範資料：初始狀態為已排程，預定今日執行"),
    ("applicant2", "紅茶包", "鋁箔袋 20 包，常溫保存", ["鉛（Pb）", "汞（Hg）", "咖啡因"], Request.Status.SCHEDULED,
     "示範資料：初始狀態為已排程，預定明日執行"),
    ("applicant2", "白米", "真空包裝 1 kg，常溫保存", ["鎘（Cd）", "水分"], Request.Status.SCHEDULED,
     "示範資料：初始狀態為已排程，指派給李實驗"),
    ("applicant1", "雞蛋", "紙盒 10 顆，冷藏", ["鉛（Pb）", "鎘（Cd）", "總生菌數"], Request.Status.IN_PROGRESS,
     "示範資料：初始狀態為實驗中"),
    ("applicant2", "鮮乳", "紙盒 936 mL，冷藏", ["鉛（Pb）", "水分", "pH 值", "三聚氰胺"],
     Request.Status.COMPLETED,
     "示範資料：初始狀態為已完成"),
    ("applicant1", "綠茶粉", "鋁箔袋 100 g，常溫避光保存", ["鉛（Pb）", "鎘（Cd）", "水分"],
     Request.Status.RESULT_AVAILABLE, "示範資料：初始狀態為已發布"),
]

# 排程：預設指派 lab1、日期為執行 seed 當天（實驗人員「今日」區有內容）；例外如下
DEFAULT_STAFF = "lab1"
SCHEDULE_OVERRIDES = {
    # 樣品名稱: (實驗人員, 距今天數)
    "紅茶包": ("lab1", 1),
    "白米": ("lab2", 0),
}

# 示範結果值：樣品名稱 → {項目: (結果值, 備註)}；一律為 "ND" 或不低於該項定量極限的純數字
DEMO_RESULTS = {
    "雞蛋": {"鉛（Pb）": ("ND", "低於定量極限")},
    "鮮乳": {
        "鉛（Pb）": ("ND", "低於定量極限"),
        "水分": ("87.6", ""),
        "pH 值": ("6.7", "25°C"),
        "三聚氰胺": ("ND", "低於定量極限"),
    },
    "綠茶粉": {
        "鉛（Pb）": ("0.32", ""),
        "鎘（Cd）": ("ND", "低於定量極限"),
        "水分": ("4.8", ""),
    },
}

STAGES = [
    Request.Status.SUBMITTED,
    Request.Status.ACCEPTED,
    Request.Status.SCHEDULED,
    Request.Status.IN_PROGRESS,
    Request.Status.COMPLETED,
    Request.Status.RESULT_AVAILABLE,
]


class Command(BaseCommand):
    help = "建立展示用的群組、帳號與檢測項目（可重複執行）"

    def handle(self, *args, **options):
        password = os.environ.get("DEMO_SEED_PASSWORD", "")
        if not password:
            raise CommandError("環境變數 DEMO_SEED_PASSWORD 未設定，未建立任何資料。")

        with transaction.atomic():
            groups = self.seed_groups()
            self.seed_users(groups, password)
            self.seed_test_items()
            self.seed_requests()

        self.stdout.write(self.style.SUCCESS("seed_demo 完成。"))

    def seed_groups(self):
        groups = {name: Group.objects.get_or_create(name=name)[0] for name in (ADMIN, LAB_STAFF, APPLICANT)}
        perms = []
        for app_label, model in ADMIN_MODELS:
            for action in ADMIN_ACTIONS:
                perms.append(
                    Permission.objects.get(
                        content_type__app_label=app_label,
                        codename=f"{action}_{model}",
                    )
                )
        groups[ADMIN].permissions.set(perms)
        self.stdout.write(f"群組：3 個；Admin 群組權限 {len(perms)} 項")
        return groups

    def seed_users(self, groups, password):
        created_count = 0
        for username, display_name, group_name, is_staff in USERS:
            user, created = User.objects.get_or_create(username=username)
            created_count += created
            user.first_name = display_name
            user.email = f"{username}@example.com"
            user.is_staff = is_staff
            user.is_superuser = False
            user.is_active = True
            user.set_password(password)
            user.save()
            user.groups.set([groups[group_name]])
        self.stdout.write(f"帳號：新增 {created_count}、更新 {len(USERS) - created_count}")

    def seed_test_items(self):
        created_count = 0
        for category, name, unit, loq, description in TEST_ITEMS:
            _, created = TestItem.objects.update_or_create(
                name=name,
                defaults={
                    "category": category,
                    "unit": unit,
                    "loq": loq,
                    "description": description,
                    "is_active": True,
                },
            )
            created_count += created
        self.stdout.write(f"檢測項目：新增 {created_count}、更新 {len(TEST_ITEMS) - created_count}")

    def seed_requests(self):
        admin = User.objects.get(username="demo_admin")
        created_count = 0
        for username, sample_name, description, item_names, target, note in DEMO_REQUESTS:
            applicant = User.objects.get(username=username)
            if Request.objects.filter(applicant=applicant, sample_name=sample_name).exists():
                continue
            items = list(TestItem.objects.filter(name__in=item_names))
            if len(items) != len(item_names):
                missing = set(item_names) - {i.name for i in items}
                raise CommandError(f"示範申請單「{sample_name}」引用了不存在的檢測項目：{'、'.join(sorted(missing))}")
            req = services.create_request(
                applicant,
                {"sample_name": sample_name, "sample_description": description, "note": note},
                items,
            )
            if target == Request.Status.RETURNED:
                services.submit(req, applicant)
                services.return_for_revision(req, admin, RETURN_REASON)
            elif target != Request.Status.DRAFT:
                self.advance(req, admin, STAGES[: STAGES.index(target) + 1])
            created_count += 1
        self.stdout.write(f"示範申請單：新增 {created_count}、略過 {len(DEMO_REQUESTS) - created_count}")

    def advance(self, req, admin, stages):
        """依序把申請單推進到 stages 的最後一個狀態。"""
        S = Request.Status
        staff_name, days = SCHEDULE_OVERRIDES.get(req.sample_name, (DEFAULT_STAFF, 0))
        staff = User.objects.get(username=staff_name)
        for stage in stages:
            if stage == S.SUBMITTED:
                services.submit(req, req.applicant)
            elif stage == S.ACCEPTED:
                services.accept(req, admin)
            elif stage == S.SCHEDULED:
                services.schedule(req, admin, timezone.localdate() + timedelta(days=days), staff)
            elif stage == S.IN_PROGRESS:
                services.start(req, staff)
                results = DEMO_RESULTS.get(req.sample_name, {})
                if results:
                    items = req.items.select_related("test_item")
                    services.save_results(
                        req,
                        staff,
                        {i.pk: results[i.test_item.name] for i in items if i.test_item.name in results},
                    )
            elif stage == S.COMPLETED:
                services.finish(req, staff)
            elif stage == S.RESULT_AVAILABLE:
                services.publish(req, admin)
