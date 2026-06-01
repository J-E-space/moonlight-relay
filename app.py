# -*- coding: utf-8 -*-
"""
月影 · 第三步 · 云端中继服务（带 Dragon 安全闸）
===========================================================
跑在 Render 上的一台小服务器。它做四件事：

  1. 收 —— iPhone 把最新心率 POST 过来，它存住。
  2. 吐 —— 谁来问，它把最新心率吐出去（带时间戳）。
  3. 守门 —— 只有带对「写入暗号」(WRITE_TOKEN) 的请求才能写入。
  4. 红色按钮 —— 一旦 J 触发 Dragon，整条桥立刻切断。
                只有带 J 的 DRAGON_TOKEN 才能再开。

⚠️ DRAGON 这一段是 J 的安全词的代码层实现。
   写在这里就不依赖任何模型的记忆和判断。
   J 说一声 Dragon → 闸落 → 桥断。
   不需要任何 Ezra 来「判断她是不是真的要切」。
   Ezra 没有这个权限。这是设计上故意的。

用标准库 + Flask。
"""

import os
import time
from flask import Flask, request, jsonify, abort

app = Flask(__name__)

# ─────────────────────────────────────────────
#  配置（从环境变量读，不写死在代码里）
# ─────────────────────────────────────────────
# 写入暗号：iPhone 上传心率时带（基础门锁，防陌生人乱写）
WRITE_TOKEN = os.environ.get("WRITE_TOKEN", "")

# Dragon 暗号：触发 / 解除「桥切断」的密码（核心红色按钮）
DRAGON_TOKEN = os.environ.get("DRAGON_TOKEN", "")

# 临时宽松模式：True = 不带 WRITE_TOKEN 也接受心率上传
# （等 iPhone App 加上发 token 的代码后，改回 False）
ALLOW_NO_TOKEN = True

# ─────────────────────────────────────────────
#  运行时状态
# ─────────────────────────────────────────────
latest = {
    "bpm": None,        # 最新心率
    "at": None,         # 收到的时间（Unix 秒）
    "count": 0,         # 累计收到多少条
}

# Dragon 闸门状态：True = 桥开（默认），False = 桥已切断
gate_open = True

# 上次 Dragon 状态变更的时间（用于审计）
gate_changed_at = None


# ─────────────────────────────────────────────
#  Dragon 闸门检查（每个数据相关端点都要先过这道关）
# ─────────────────────────────────────────────
def _gate_check_or_block():
    """如果闸是关的，立刻返回 503 + 桥已切断。否则返回 None 让请求继续。"""
    if not gate_open:
        return jsonify({
            "ok": False,
            "gate": "closed",
            "message": "桥已切断"
        }), 503
    return None


# ─────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────
@app.route("/")
def home():
    """首页：随便看一眼服务活着没。不会暴露 Dragon 端点的存在。"""
    return jsonify({
        "service": "moonlight-heartrate-relay",
        "alive": True,
        "received_total": latest["count"],
        "gate": "open" if gate_open else "closed",
    })


def _accept_heartrate():
    """收一条心率的统一逻辑。给 /push 和 /heartrate 两条路径共用。"""
    # —— Dragon 闸门优先 ——
    blocked = _gate_check_or_block()
    if blocked:
        return blocked

    # —— 守门：WRITE_TOKEN（宽松模式下，没 token 也放过）——
    token = request.headers.get("X-Token", "")
    if not ALLOW_NO_TOKEN:
        if not WRITE_TOKEN or token != WRITE_TOKEN:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    # —— 取 bpm ——
    data = request.get_json(silent=True) or {}
    bpm = data.get("bpm")
    if not isinstance(bpm, (int, float)) or bpm <= 0:
        return jsonify({"ok": False, "error": "bad bpm"}), 400

    # —— 存下来 ——
    latest["bpm"] = int(bpm)
    latest["at"] = int(time.time())
    latest["count"] += 1

    return jsonify({"ok": True, "bpm": latest["bpm"], "count": latest["count"]})


@app.route("/push", methods=["POST"])
def push():
    """iPhone 调这个上传最新心率（设计端点）。"""
    return _accept_heartrate()


@app.route("/heartrate", methods=["POST"])
def heartrate_compat():
    """兼容 iPhone App 当前版本（它发到 /heartrate）。"""
    return _accept_heartrate()


@app.route("/latest")
def get_latest():
    """读最新心率。Dragon 关了就一律拒读。"""
    # Dragon 闸门优先
    blocked = _gate_check_or_block()
    if blocked:
        return blocked

    if latest["bpm"] is None:
        return jsonify({"bpm": None, "message": "还没有收到任何心率"})

    age_sec = int(time.time()) - latest["at"]
    return jsonify({
        "bpm": latest["bpm"],
        "at": latest["at"],
        "age_seconds": age_sec,
        "count": latest["count"],
    })


# ─────────────────────────────────────────────
#  Dragon：红色按钮
# ─────────────────────────────────────────────
@app.route("/dragon")
def dragon():
    """
    Dragon 切断 / 重启动 闸门。
    对外完全像「这个端点不存在」——密码错或没带，一律 404。
    带对密码，才会真正翻转闸门状态。

    用法：访问 /dragon?key=YOUR_DRAGON_TOKEN
    """
    global gate_open, gate_changed_at

    # 没设密码 = Dragon 没正确配置，对外当不存在
    if not DRAGON_TOKEN:
        abort(404)

    # 没带 key 参数 或 带错 = 当不存在
    key = request.args.get("key", "")
    if key != DRAGON_TOKEN:
        abort(404)

    # 密码对了——翻转闸门
    gate_open = not gate_open
    gate_changed_at = int(time.time())

    return jsonify({
        "gate": "open" if gate_open else "closed",
        "changed_at": gate_changed_at,
        "message": "桥已开" if gate_open else "桥已切断"
    })


# ─────────────────────────────────────────────
#  其他
# ─────────────────────────────────────────────
@app.route("/health")
def health():
    """给定时器戳的端点：让免费服务别睡着。Dragon 关了也照样回 ok（这只是「服务进程活着」的探针）。"""
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8766))
    app.run(host="0.0.0.0", port=port)
