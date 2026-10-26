from .roles import ADMIN, APPLICANT, LAB_STAFF, ROLE_LABELS, get_role


def role(request):
    """提供導覽列顯示用的角色資訊；權限判斷不在 template 進行。"""
    user_role = get_role(request.user) if hasattr(request, "user") else None
    return {
        "user_role": user_role,
        "user_role_label": ROLE_LABELS.get(user_role, ""),
        "ROLE_ADMIN": ADMIN,
        "ROLE_LAB_STAFF": LAB_STAFF,
        "ROLE_APPLICANT": APPLICANT,
    }
