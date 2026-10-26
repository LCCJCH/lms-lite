# 本機環境變數範本（不含真實值）
#
# 用法：
#   1. 複製為 local_env.ps1（已列入 .gitignore，不會進 git）：
#        Copy-Item local_env.example.ps1 local_env.ps1
#   2. 填入 SECRET_KEY，可用下列指令產生：
#        python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
#   3. 每次開新的 PowerShell 視窗、執行 manage.py 前先載入：
#        . .\local_env.ps1

$env:SECRET_KEY = "<貼上本機用的隨機 key>"
$env:DEBUG = "True"

# 註冊用邀請碼與 seed_demo 帳號密碼（本機自訂即可，勿與 Heroku 相同）
$env:DEMO_INVITE_CODE = "<邀請碼>"
$env:DEMO_SEED_PASSWORD = "<seed 帳號密碼>"
