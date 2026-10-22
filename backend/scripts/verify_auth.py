"""账号体系自测：注册 / 邮箱登录 / 验证码登录（自动建号）/ 改密 / 登出 / 401 语义。

覆盖范围（后续追加）：
- 管理员白名单（ADMIN_PHONES）与 require_admin 门禁；
- 匿名设备数据认领：匿名 X-Device-Id 建的简历，登录/注册后自动归到账号；
- 简历更新 PUT /resumes/{id}：title/content/template_code 往返、content-only 保留模板、
  显式 null 清模板、非本人 404、更新不新建。

直接跑：
    backend/.venv/Scripts/python.exe backend/scripts/verify_auth.py

脚本会把 DATABASE_URL 指向临时 SQLite 文件（不碰 data/zhenresume.db），
用 FastAPI TestClient 走一遍真实路由与真实依赖（含 JWT 校验与黑名单）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 注意：**不要**把自己所在目录加进 sys.path —— backend/scripts 里有个 inspect.py，
# 一旦进到路径中就会遮蔽标准库的 inspect，导致 fastapi / sqlalchemy 导入失败。
_ROLE = Path(__file__).resolve().parent.parent
# 运行脚本时 Python 会把脚本所在目录（backend/scripts，含同名的 inspect.py）放进 sys.path[0]，
# 它会遮蔽标准库的 inspect。脚本自身不需要这个目录，直接移除。
if sys.path and Path(sys.path[0]).resolve() == Path(__file__).resolve().parent:
    sys.path.pop(0)
ROOT = _ROLE.parent if _ROLE.name == "scripts" else _ROLE
sys.path.insert(0, str(ROOT))

# 必须在 import app.* 之前设置：config 用 lru_cache 读一次环境变量
_tmp_dir = tempfile.mkdtemp(prefix="zhenresume-auth-")
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{(Path(_tmp_dir) / 'auth-test.db').as_posix()}"
os.environ["AUTH_SECRET"] = "test-secret-for-auth-verify-0123456789"
os.environ["SMS_DEV_MODE"] = "true"
os.environ["SMS_DEV_CODE"] = "123456"
# 管理员白名单：Alice 的手机号命中，注册时就应被提升为 admin；
# 验证码自动建号的用户不在白名单里，必须被 403 挡住。
os.environ["ADMIN_PHONES"] = "13900139001"
os.environ["UPLOAD_DIR"] = str(Path(_tmp_dir) / "uploads")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def err_code(resp) -> str:
    try:
        return resp.json()["detail"]["code"]
    except Exception:  # noqa: BLE001
        return f"<{resp.status_code}>"


def editor_content(*, name: str, summary: str) -> dict:
    """构造一份满足 `is_meaningful()` 的最小编辑器内容（姓名 + 手机号 + 一段经历）。"""
    return {
        "basics": {
            "name": name,
            "target_title": "后端开发工程师",
            "phone": "13800138000",
            "email": "claimer@example.com",
            "city": "杭州",
            "links": [],
            "avatar": "",
        },
        "summary": summary,
        "work": [
            {
                "company": "某某科技有限公司",
                "title": "后端开发工程师",
                "start": "2021.07",
                "end": "至今",
                "location": "杭州",
                "highlights": ["主导订单服务重构，P99 延迟下降 70%"],
            }
        ],
        "projects": [],
        "education": [
            {
                "school": "某某大学",
                "degree": "本科",
                "major": "计算机科学与技术",
                "start": "2017.09",
                "end": "2021.06",
            }
        ],
        "skills": ["Python", "FastAPI", "MySQL"],
        "certificates": [],
        "awards": [],
        "custom_sections": [],
    }


def main() -> int:
    with TestClient(app) as client:
        print("\n[1] 发送验证码")
        r = client.post("/api/v1/auth/sms-code", json={"phone": "13900139001", "purpose": "register"})
        check("注册用途发送成功 201", r.status_code == 201, r.text)
        check("开发模式回传 dev_code", r.json().get("dev_code") == "123456", r.text)
        r2 = client.post("/api/v1/auth/sms-code", json={"phone": "13900139001", "purpose": "login"})
        check("60 秒内重发被限流 429", r2.status_code == 429 and err_code(r2) == "SMS_TOO_FREQUENT", r2.text)
        r3 = client.post("/api/v1/auth/sms-code", json={"phone": "12345", "purpose": "login"})
        check("非法手机号 400", r3.status_code == 400 and err_code(r3) == "PHONE_INVALID", r3.text)

        print("\n[2] 创建账号")
        weak = client.post(
            "/api/v1/auth/register",
            json={"email": "a@b.com", "phone": "13900139001", "code": "123456", "password": "abcdefgh"},
        )
        check("弱密码 400 PASSWORD_WEAK", weak.status_code == 400 and err_code(weak) == "PASSWORD_WEAK", weak.text)

        bad_code = client.post(
            "/api/v1/auth/register",
            json={"email": "a@b.com", "phone": "13900139001", "code": "000000", "password": "abcd1234"},
        )
        check("验证码错误 401", bad_code.status_code == 401 and err_code(bad_code) == "SMS_CODE_INVALID", bad_code.text)

        ok = client.post(
            "/api/v1/auth/register",
            json={"email": "Alice@Example.com", "phone": "13900139001", "code": "123456", "password": "abcd1234"},
        )
        check("注册成功 201", ok.status_code == 201, ok.text)
        body = ok.json()
        token = body.get("access_token", "")
        account = body.get("account", {})
        check("邮箱已规范化小写", account.get("email") == "alice@example.com", str(account))
        check("auto_created=false", account.get("auto_created") is False, str(account))
        check("响应含 access_token", bool(token) and body.get("token_type") == "bearer")

        reuse = client.post(
            "/api/v1/auth/sms-code", json={"phone": "13900139001", "purpose": "register"}
        )
        check("已注册手机号再发注册码 409", reuse.status_code == 409 and err_code(reuse) == "PHONE_TAKEN", reuse.text)

        print("\n[3] 邮箱登录")
        bad = client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "wrong-pass1"})
        check("密码错误 401 AUTH_FAILED", bad.status_code == 401 and err_code(bad) == "AUTH_FAILED", bad.text)
        check("401 带 WWW-Authenticate", bad.headers.get("www-authenticate") == "Bearer", str(bad.headers))
        good = client.post("/api/v1/auth/login", json={"email": "Alice@example.com", "password": "abcd1234"})
        check("邮箱大小写不敏感登录成功", good.status_code == 200, good.text)
        email_token = good.json()["access_token"]

        print("\n[4] /auth/me")
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {email_token}"})
        check("me 200", me.status_code == 200, me.text)
        check("me 返回额度信息", "quota" in me.json() and "has_paid" in me.json(), me.text)
        check("me 不含密码哈希", "password_hash" not in me.text)
        anon = client.get("/api/v1/auth/me")
        check("未登录访问 me 401", anon.status_code == 401, anon.text)

        print("\n[5] 手机验证码登录（自动建号）")
        client.post("/api/v1/auth/sms-code", json={"phone": "13700137002", "purpose": "login"})
        sms = client.post("/api/v1/auth/login-sms", json={"phone": "13700137002", "code": "123456"})
        check("验证码登录 200", sms.status_code == 200, sms.text)
        sms_body = sms.json()
        check("account_created=true", sms_body.get("account_created") is True, sms.text)
        auto = sms_body["account"]
        check("占位邮箱是 .local 域名", str(auto.get("email", "")).endswith(".auto@zhenresume.local"), str(auto))
        check("auto_created 标记为真", auto.get("auto_created") is True, str(auto))

        # 登录码不能拿去注册
        rc = client.post(
            "/api/v1/auth/register",
            json={"email": "evil@example.com", "phone": "13700137003", "code": "123456", "password": "abcd1234"},
        )
        check("跨手机号复用验证码被拒", rc.status_code == 401, rc.text)

        print("\n[6] 改密：旧 token 失效")
        auto_token = sms_body["access_token"]
        ch = client.post(
            "/api/v1/auth/change-password",
            json={"new_password": "newpass123", "confirm_password": "newpass123"},
            headers={"Authorization": f"Bearer {auto_token}"},
        )
        check("自动建号可直接设置新密码", ch.status_code == 200, ch.text)
        new_token = ch.json()["access_token"]
        old_use = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {auto_token}"})
        check("旧 token 失效 401", old_use.status_code == 401 and err_code(old_use) == "AUTH_REVOKED", old_use.text)
        new_use = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_token}"})
        check("新 token 可用", new_use.status_code == 200, new_use.text)
        new_login = client.post("/api/v1/auth/login", json={"email": auto["email"], "password": "newpass123"})
        check("用新密码邮箱登录成功", new_login.status_code == 200, new_login.text)

        print("\n[7] 登出")
        out = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {new_token}"})
        check("登出 204", out.status_code == 204, out.text)
        after = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_token}"})
        check("登出后 token 失效", after.status_code == 401, after.text)

        print("\n[8] 匿名通道仍然可用（AUTH_REQUIRED=false）")
        dev = {"X-Device-Id": "verify-device-1"}
        r = client.get("/api/v1/templates", headers=dev)
        check("匿名 X-Device-Id 访问业务接口正常", r.status_code == 200, r.text[:200])

        print("\n[9] 改邮箱")
        patch = client.patch(
            "/api/v1/auth/me",
            json={"email": "alice.new@example.com"},
            headers={"Authorization": f"Bearer {email_token}"},
        )
        check("PATCH me 改邮箱成功", patch.status_code == 200 and patch.json()["account"]["email"] == "alice.new@example.com", patch.text)
        dup = client.patch(
            "/api/v1/auth/me",
            json={"email": auto["email"]},
            headers={"Authorization": f"Bearer {email_token}"},
        )
        check("邮箱冲突 409", dup.status_code == 409 and err_code(dup) == "EMAIL_TAKEN", dup.text)

        print("\n[10] 管理后台门禁（require_admin）")
        me_admin = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {email_token}"})
        check(
            "白名单手机号注册的账号 role=admin",
            me_admin.status_code == 200 and me_admin.json()["account"]["role"] == "admin",
            me_admin.text,
        )
        anon_admin = client.get("/api/v1/admin/metrics", headers={"X-Device-Id": "auth-anon-device"})
        check(
            "匿名设备访问后台 403 ADMIN_REQUIRED",
            anon_admin.status_code == 403 and err_code(anon_admin) == "ADMIN_REQUIRED",
            anon_admin.text,
        )
        plain_login = client.post("/api/v1/auth/login", json={"email": auto["email"], "password": "newpass123"})
        check("普通账号仍可正常登录", plain_login.status_code == 200, plain_login.text)
        plain_token = plain_login.json().get("access_token", "")
        blocked_read = client.get("/api/v1/admin/metrics", headers={"Authorization": f"Bearer {plain_token}"})
        check(
            "普通账号读后台指标 403",
            blocked_read.status_code == 403 and err_code(blocked_read) == "ADMIN_REQUIRED",
            blocked_read.text,
        )
        blocked_write = client.post(
            "/api/v1/admin/prompts",
            headers={"Authorization": f"Bearer {plain_token}"},
            json={"name": "ats_agent", "content": "（越权验证）这条不应该被写入。"},
        )
        check(
            "普通账号改线上 Prompt 403",
            blocked_write.status_code == 403 and err_code(blocked_write) == "ADMIN_REQUIRED",
            blocked_write.text,
        )
        admin_read = client.get("/api/v1/admin/metrics", headers={"Authorization": f"Bearer {email_token}"})
        check("管理员读后台指标 200", admin_read.status_code == 200, admin_read.text[:300])

        print("\n[11] 匿名设备数据认领（X-Device-Id → 账号）")
        device_id = "verify-claim-device-2026"
        anon_content = editor_content(name="认领用户", summary="匿名期间写的第一版个人优势。")
        created = client.post(
            "/api/v1/resumes/from-editor",
            headers={"X-Device-Id": device_id},
            json={"title": "匿名期创建的简历", "template_code": "general_experienced", "content": anon_content},
        )
        check("匿名 from-editor 201", created.status_code == 201, created.text[:300])
        created_body = created.json() if created.status_code == 201 else {}
        claimed_id = created_body.get("resume_id", "")
        check(
            "创建响应顶层回传 template_code",
            created_body.get("template_code") == "general_experienced",
            created.text[:300],
        )

        # 同一个 device id 走短信注册（新手机号）→ 登录响应里直接拿 token
        client.post("/api/v1/auth/sms-code", json={"phone": "13800138000", "purpose": "register"})
        reg = client.post(
            "/api/v1/auth/register",
            headers={"X-Device-Id": device_id},
            json={"email": "claimer@example.com", "phone": "13800138000", "code": "123456", "password": "abcd1234"},
        )
        check("带 X-Device-Id 注册 201", reg.status_code == 201, reg.text[:300])
        claim_token = reg.json().get("access_token", "") if reg.status_code == 201 else ""
        # 刻意只带 Bearer、**不**带设备头：能读到简历才说明是"认领"而不是"同一设备碰巧命中"
        claim_headers = {"Authorization": f"Bearer {claim_token}"}

        listed = client.get("/api/v1/resumes", headers=claim_headers)
        listed_body = listed.json() if listed.status_code == 200 else {}
        listed_items = listed_body.get("items", [])
        total_before = listed_body.get("total", 0)
        check("Bearer + 无设备头可列出认领来的简历", listed.status_code == 200, listed.text[:300])
        check(
            "匿名期间的简历已归属新账号",
            any(item.get("resume_id") == claimed_id for item in listed_items),
            str(listed_items)[:300],
        )

        print("\n[12] 简历更新（PUT /api/v1/resumes/{id}）")
        new_content = editor_content(
            name="认领用户",
            summary="登录后修改过的个人优势：主导过日均 200 万次调用的订单服务重构。",
        )
        updated = client.put(
            f"/api/v1/resumes/{claimed_id}",
            headers=claim_headers,
            json={"title": "登录后改的标题", "template_code": "tech_engineer", "content": new_content},
        )
        check("PUT 更新 200", updated.status_code == 200, updated.text[:300])
        updated_body = updated.json() if updated.status_code == 200 else {}
        check(
            "PUT 返回新 title / template_code",
            updated_body.get("title") == "登录后改的标题"
            and updated_body.get("template_code") == "tech_engineer",
            str(updated_body)[:300],
        )
        updated_editor = updated_body.get("editor_content") or {}
        check("PUT 重算 editor_content", updated_editor.get("summary") == new_content["summary"], str(updated_editor)[:300])
        check(
            "PUT 重算 raw_text（诊断链路读最新内容）",
            "日均 200 万次调用" in (updated_body.get("raw_text") or ""),
            (updated_body.get("raw_text") or "")[:300],
        )

        readback = client.get(f"/api/v1/resumes/{claimed_id}", headers=claim_headers)
        rb = readback.json() if readback.status_code == 200 else {}
        check(
            "GET 回读 title / template_code / editor_content 均已更新",
            readback.status_code == 200
            and rb.get("title") == "登录后改的标题"
            and rb.get("template_code") == "tech_engineer"
            and (rb.get("editor_content") or {}).get("summary") == new_content["summary"],
            readback.text[:300],
        )

        # content-only：只改正文时必须保留旧的模板选择
        content_only = editor_content(name="认领用户", summary="只改内容、不动模板选择的保存。")
        preserved = client.put(
            f"/api/v1/resumes/{claimed_id}", headers=claim_headers, json={"content": content_only}
        )
        check(
            "content-only 更新保留 template_code",
            preserved.status_code == 200 and preserved.json().get("template_code") == "tech_engineer",
            preserved.text[:300],
        )

        # 只有显式传 null 才清除模板
        cleared = client.put(f"/api/v1/resumes/{claimed_id}", headers=claim_headers, json={"template_code": None})
        check(
            "显式 template_code=null 清除模板",
            cleared.status_code == 200 and cleared.json().get("template_code") is None,
            cleared.text[:300],
        )

        # 更新是原地改，不是再建一份
        after = client.get("/api/v1/resumes", headers=claim_headers)
        total_after = after.json().get("total") if after.status_code == 200 else -1
        check("更新前后简历总数不变（不新建）", total_after == total_before == 1, f"{total_before} -> {total_after}")

        # 入参校验：空 body / 空白标题 / 内容过少都要 400 VALIDATION_ERROR
        empty_body = client.put(f"/api/v1/resumes/{claimed_id}", headers=claim_headers, json={})
        check(
            "PUT 空 body 400 VALIDATION_ERROR",
            empty_body.status_code == 400 and err_code(empty_body) == "VALIDATION_ERROR",
            empty_body.text[:300],
        )
        blank_title = client.put(f"/api/v1/resumes/{claimed_id}", headers=claim_headers, json={"title": "   "})
        check(
            "PUT 空白标题 400 VALIDATION_ERROR",
            blank_title.status_code == 400 and err_code(blank_title) == "VALIDATION_ERROR",
            blank_title.text[:300],
        )
        meaningless = client.put(f"/api/v1/resumes/{claimed_id}", headers=claim_headers, json={"content": {}})
        check(
            "PUT 内容过少 400 VALIDATION_ERROR",
            meaningless.status_code == 400 and err_code(meaningless) == "VALIDATION_ERROR",
            meaningless.text[:300],
        )

        # 非本人（alice 的 token）不能改：不存在与无权限统一 404，避免泄露他人简历 id
        forbidden = client.put(
            f"/api/v1/resumes/{claimed_id}",
            headers={"Authorization": f"Bearer {email_token}"},
            json={"title": "越权修改"},
        )
        check(
            "非本人 PUT 404 RESUME_NOT_FOUND",
            forbidden.status_code == 404 and err_code(forbidden) == "RESUME_NOT_FOUND",
            forbidden.text[:300],
        )

    print(f"\n结果：{PASS} 通过，{FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
