from django.conf import settings
from django.db import models
from django.db.models import Case, Value, When


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("建立時間", auto_now_add=True)
    updated_at = models.DateTimeField("更新時間", auto_now=True)

    class Meta:
        abstract = True


class TestItemCategory(models.TextChoices):
    """檢測項目分類；choices 的順序即顯示順序。"""

    ORGANIC = "organic", "有機"
    INORGANIC = "inorganic", "無機"
    PHYSCHEM = "physchem", "物化性"
    OTHER = "other", "其他"


def category_rank(prefix=""):
    """依分類的顯示順序排序用；未知分類排在最後。"""
    values = TestItemCategory.values
    return Case(
        *[When(**{f"{prefix}category": v}, then=Value(i)) for i, v in enumerate(values)],
        default=Value(len(values)),
    )


class TestItem(TimeStampedModel):
    """檢測項目主檔（簡報 §4.1），由 /admin/ 維護。類內依建立順序（即 seed 順序）排列。"""

    Category = TestItemCategory

    name = models.CharField("項目名稱", max_length=100, unique=True)
    category = models.CharField("分類", max_length=20, choices=TestItemCategory.choices)
    unit = models.CharField("單位", max_length=20)
    loq = models.CharField("定量極限", max_length=20, blank=True, help_text="單位同本項目的單位；空白時顯示「—」")
    description = models.CharField("說明", max_length=200, blank=True)
    is_active = models.BooleanField("啟用", default=True)

    class Meta:
        ordering = [category_rank(), "id"]
        verbose_name = "檢測項目"
        verbose_name_plural = "檢測項目"

    def __str__(self):
        return f"{self.name}（{self.unit}）" if self.unit else self.name


class Request(TimeStampedModel):
    """申請單（簡報 §4.2）。status 只能經由 lab.services 修改。"""

    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        SUBMITTED = "submitted", "已送出"
        RETURNED = "returned", "退回修改"
        ACCEPTED = "accepted", "已受理"
        SCHEDULED = "scheduled", "已排程"
        IN_PROGRESS = "in_progress", "實驗中"
        COMPLETED = "completed", "已完成"
        RESULT_AVAILABLE = "result_available", "結果已發布"

    request_no = models.CharField("申請單號", max_length=20, unique=True, null=True, blank=True)
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="lab_requests", verbose_name="申請者"
    )
    sample_name = models.CharField("樣品名稱", max_length=100)
    sample_description = models.TextField("樣品描述", blank=True)
    note = models.TextField("備註", blank=True)
    status = models.CharField("狀態", max_length=20, choices=Status.choices, default=Status.DRAFT)
    return_reason = models.TextField("退回理由", blank=True)
    submitted_at = models.DateTimeField("送出時間", null=True, blank=True)
    published_at = models.DateTimeField("發布時間", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "申請單"
        verbose_name_plural = "申請單"

    def __str__(self):
        return f"{self.request_no or '（未配號）'} {self.sample_name}"

    @property
    def is_editable(self):
        return self.status in (self.Status.DRAFT, self.Status.RETURNED)

    @property
    def is_deletable(self):
        """只有從未送出過（未配號）的草稿可以刪除。"""
        return self.status == self.Status.DRAFT and not self.request_no


class RequestItem(TimeStampedModel):
    """申請項目與結果（簡報 §4.3）。"""

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="items", verbose_name="申請單")
    test_item = models.ForeignKey(TestItem, on_delete=models.PROTECT, verbose_name="檢測項目")
    result_value = models.CharField("結果值", max_length=32, blank=True)
    result_note = models.CharField("結果備註", max_length=200, blank=True)

    class Meta:
        ordering = [category_rank("test_item__"), "test_item_id"]
        unique_together = [("request", "test_item")]
        verbose_name = "申請項目"
        verbose_name_plural = "申請項目"

    def __str__(self):
        return str(self.test_item)


class RequestStatusLog(TimeStampedModel):
    """狀態歷程（簡報 §4.5），每次狀態轉換寫一筆。"""

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="status_logs", verbose_name="申請單")
    from_status = models.CharField("原狀態", max_length=20, choices=Request.Status.choices, blank=True)
    to_status = models.CharField("新狀態", max_length=20, choices=Request.Status.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="操作者"
    )
    comment = models.TextField("說明", blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "狀態歷程"
        verbose_name_plural = "狀態歷程"

    def __str__(self):
        return f"{self.from_status or '—'} → {self.to_status}"


class Schedule(TimeStampedModel):
    """排程（簡報 §4.4），與申請單一對一。不設狀態欄位，狀態只存在 Request.status。"""

    request = models.OneToOneField(Request, on_delete=models.CASCADE, related_name="schedule", verbose_name="申請單")
    scheduled_date = models.DateField("預定實驗日")
    assigned_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="lab_schedules", verbose_name="指派實驗人員"
    )
    scheduled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="排程者"
    )
    started_at = models.DateTimeField("開始時間", null=True, blank=True)
    completed_at = models.DateTimeField("完成時間", null=True, blank=True)

    class Meta:
        ordering = ["scheduled_date", "id"]
        verbose_name = "排程"
        verbose_name_plural = "排程"

    def __str__(self):
        return f"{self.request} @ {self.scheduled_date}"
