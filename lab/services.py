"""申請單的狀態轉換服務（簡報 §5）。

Request.status 只能經由本模組修改。每個轉換在同一個 transaction.atomic() 內
完成狀態更新、副作用與歷程寫入；不合法的轉換一律 raise TransitionError。
"""
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.roles import ADMIN, APPLICANT, LAB_STAFF, get_role

from .models import Request, RequestItem, RequestStatusLog, Schedule

S = Request.Status

# 操作者類型
OWNER = "owner"  # 申請者本人
ADMIN_ONLY = "admin"  # Admin 角色
ASSIGNED_STAFF = "assigned_staff"  # 被指派的實驗人員

# 允許的轉換表：action → (允許的原狀態, 新狀態, 操作者類型)
TRANSITIONS = {
    "submit": ({S.DRAFT, S.RETURNED}, S.SUBMITTED, OWNER),
    "withdraw": ({S.SUBMITTED}, S.DRAFT, OWNER),
    "return_for_revision": ({S.SUBMITTED}, S.RETURNED, ADMIN_ONLY),
    "accept": ({S.SUBMITTED}, S.ACCEPTED, ADMIN_ONLY),
    "schedule": ({S.ACCEPTED}, S.SCHEDULED, ADMIN_ONLY),
    "start": ({S.SCHEDULED}, S.IN_PROGRESS, ASSIGNED_STAFF),
    "finish": ({S.IN_PROGRESS}, S.COMPLETED, ASSIGNED_STAFF),
    "publish": ({S.COMPLETED}, S.RESULT_AVAILABLE, ADMIN_ONLY),
}

ACTION_LABELS = {
    "submit": "送出",
    "withdraw": "撤回",
    "return_for_revision": "退回",
    "accept": "受理",
    "schedule": "排程",
    "start": "開始實驗",
    "finish": "完成實驗",
    "publish": "發布",
}

REQUEST_NO_RETRIES = 5


class TransitionError(Exception):
    """不合法的操作；訊息直接顯示給使用者。"""


def _actor_allowed(req, actor, who):
    if who == OWNER:
        return req.applicant_id == actor.pk
    if who == ADMIN_ONLY:
        return get_role(actor) == ADMIN
    if who == ASSIGNED_STAFF:
        schedule = getattr(req, "schedule", None)
        return schedule is not None and schedule.assigned_staff_id == actor.pk
    return False


def _lock(req):
    return Request.objects.select_for_update().get(pk=req.pk)


def _log(req, from_status, to_status, actor, comment=""):
    RequestStatusLog.objects.create(
        request=req, from_status=from_status, to_status=to_status, actor=actor, comment=comment
    )


def _transition(req, action, actor, comment="", side_effect=None):
    from_states, to_status, who = TRANSITIONS[action]
    with transaction.atomic():
        locked = _lock(req)
        if not _actor_allowed(locked, actor, who):
            raise TransitionError("您沒有執行此操作的權限。")
        if locked.status not in from_states:
            raise TransitionError(
                f"申請單目前為「{locked.get_status_display()}」，無法{ACTION_LABELS[action]}。"
            )
        from_status = locked.status
        if side_effect:
            side_effect(locked)
        locked.status = to_status
        locked.save()
        _log(locked, from_status, to_status, actor, comment)
    req.refresh_from_db()
    return req


def _assign_request_no(req):
    """配發 REQ-YYYY-NNNN；以唯一約束擋撞號，衝突時重試。"""
    prefix = f"REQ-{timezone.localdate().year}-"
    for _ in range(REQUEST_NO_RETRIES):
        existing = Request.objects.filter(request_no__startswith=prefix).values_list(
            "request_no", flat=True
        )
        next_no = max((int(no[len(prefix):]) for no in existing), default=0) + 1
        req.request_no = f"{prefix}{next_no:04d}"
        try:
            with transaction.atomic():
                req.save(update_fields=["request_no"])
            return
        except IntegrityError:
            continue
    req.request_no = None
    raise TransitionError("配號時發生衝突，請再送出一次。")


def _set_items(req, test_items):
    wanted = {item.pk for item in test_items}
    req.items.exclude(test_item_id__in=wanted).delete()
    current = set(req.items.values_list("test_item_id", flat=True))
    RequestItem.objects.bulk_create(
        RequestItem(request=req, test_item_id=pk) for pk in sorted(wanted - current)
    )


def _set_fields(req, data):
    for field in ("sample_name", "sample_description", "note"):
        if field in data:
            setattr(req, field, data[field])


# ---------- 申請者 ----------


def create_request(applicant, data, test_items):
    """建立草稿，並寫入第一筆歷程（'' → draft）。"""
    if get_role(applicant) != APPLICANT:
        raise TransitionError("只有申請者可以建立申請單。")
    with transaction.atomic():
        req = Request(applicant=applicant)
        _set_fields(req, data)
        req.save()
        _set_items(req, test_items)
        _log(req, "", S.DRAFT, applicant)
    return req


def update_request(req, actor, data, test_items):
    """修改 draft／returned 的申請單內容與項目；狀態不變、不寫歷程。"""
    with transaction.atomic():
        locked = _lock(req)
        if locked.applicant_id != actor.pk:
            raise TransitionError("您沒有執行此操作的權限。")
        if not locked.is_editable:
            raise TransitionError(f"申請單目前為「{locked.get_status_display()}」，無法修改。")
        _set_fields(locked, data)
        locked.save()
        _set_items(locked, test_items)
    req.refresh_from_db()
    return req


def delete_request(req, actor):
    """刪除從未送出過的草稿。"""
    with transaction.atomic():
        locked = _lock(req)
        if locked.applicant_id != actor.pk:
            raise TransitionError("您沒有執行此操作的權限。")
        if not locked.is_deletable:
            raise TransitionError("只有從未送出過的草稿可以刪除。")
        locked.delete()


def submit(req, actor):
    def side_effect(locked):
        if not locked.items.exists():
            raise TransitionError("請至少選擇一個檢測項目才能送出。")
        if not locked.request_no:
            _assign_request_no(locked)
        locked.submitted_at = timezone.now()

    return _transition(req, "submit", actor, side_effect=side_effect)


def withdraw(req, actor):
    return _transition(req, "withdraw", actor)


# ---------- Admin ----------


def return_for_revision(req, actor, reason):
    reason = (reason or "").strip()

    def side_effect(locked):
        if not reason:
            raise TransitionError("請填寫退回理由。")
        locked.return_reason = reason

    return _transition(req, "return_for_revision", actor, comment=reason, side_effect=side_effect)


def accept(req, actor):
    return _transition(req, "accept", actor)


def display_name(user):
    return user.first_name or user.username


def _validate_schedule(scheduled_date, staff):
    if not scheduled_date:
        raise TransitionError("請選擇預定實驗日。")
    if staff is None or not staff.is_active or not staff.groups.filter(name=LAB_STAFF).exists():
        raise TransitionError("指派的人員必須是啟用中的實驗人員。")


def schedule(req, actor, scheduled_date, staff):
    """排程並指派實驗人員：accepted → scheduled，同時建立 Schedule。"""

    def side_effect(locked):
        _validate_schedule(scheduled_date, staff)
        Schedule.objects.create(
            request=locked, scheduled_date=scheduled_date, assigned_staff=staff, scheduled_by=actor
        )

    comment = f"預定 {scheduled_date:%Y-%m-%d}，指派 {display_name(staff)}" if scheduled_date and staff else ""
    return _transition(req, "schedule", actor, comment=comment, side_effect=side_effect)


def reschedule(req, actor, scheduled_date, staff):
    """修改排程日期與指派人員；狀態不變，寫一筆 scheduled → scheduled 的歷程。"""
    with transaction.atomic():
        locked = _lock(req)
        if not _actor_allowed(locked, actor, ADMIN_ONLY):
            raise TransitionError("您沒有執行此操作的權限。")
        if locked.status != S.SCHEDULED:
            raise TransitionError(f"申請單目前為「{locked.get_status_display()}」，無法改排程。")
        _validate_schedule(scheduled_date, staff)
        sched = Schedule.objects.select_for_update().get(request=locked)
        changes = []
        if sched.scheduled_date != scheduled_date:
            changes.append(f"排程日期 {sched.scheduled_date:%Y-%m-%d} → {scheduled_date:%Y-%m-%d}")
        if sched.assigned_staff_id != staff.pk:
            changes.append(f"實驗人員 {display_name(sched.assigned_staff)} → {display_name(staff)}")
        if not changes:
            raise TransitionError("排程內容沒有變更。")
        sched.scheduled_date = scheduled_date
        sched.assigned_staff = staff
        sched.save()
        _log(locked, S.SCHEDULED, S.SCHEDULED, actor, "改排程：" + "；".join(changes))
    req.refresh_from_db()
    return req


def publish(req, actor):
    """發布結果：completed → result_available，寫入 published_at；之後結果唯讀。"""

    def side_effect(locked):
        locked.published_at = timezone.now()

    return _transition(req, "publish", actor, side_effect=side_effect)


# ---------- 實驗人員 ----------


def _stamp_schedule(locked, field):
    sched = Schedule.objects.select_for_update().get(request=locked)
    setattr(sched, field, timezone.now())
    sched.save(update_fields=[field, "updated_at"])


def start(req, actor):
    """開始實驗：scheduled → in_progress，寫入 Schedule.started_at。"""

    def side_effect(locked):
        _stamp_schedule(locked, "started_at")

    return _transition(req, "start", actor, side_effect=side_effect)


def save_results(req, actor, results):
    """暫存結果值與備註；狀態不變、不寫歷程。

    results 為 {RequestItem pk: (result_value, result_note)}，只能是這張申請單的項目。
    """
    with transaction.atomic():
        locked = _lock(req)
        if not _actor_allowed(locked, actor, ASSIGNED_STAFF):
            raise TransitionError("您沒有執行此操作的權限。")
        if locked.status != S.IN_PROGRESS:
            raise TransitionError(f"申請單目前為「{locked.get_status_display()}」，無法輸入結果。")
        items = {item.pk: item for item in locked.items.select_for_update()}
        if not set(results) <= set(items):
            raise TransitionError("結果包含不屬於此申請單的項目。")
        for pk, (value, note) in results.items():
            item = items[pk]
            item.result_value = (value or "").strip()
            item.result_note = (note or "").strip()
            item.save(update_fields=["result_value", "result_note", "updated_at"])
    return req


def finish(req, actor):
    """完成實驗：in_progress → completed；全部項目都要有結果值。"""

    def side_effect(locked):
        missing = locked.items.filter(result_value="").count()
        if missing:
            raise TransitionError(f"尚有 {missing} 個項目未填結果值，無法完成。")
        _stamp_schedule(locked, "completed_at")

    return _transition(req, "finish", actor, side_effect=side_effect)
