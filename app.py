# -*- coding: utf-8 -*-
"""
月影 · 第三步 · 云端中继服务
===========================================================
跑在 Render 上的一台小服务器。它做三件事：

  1. 收 —— iPhone 把最新心率 POST 过来，它存住。
  2. 吐 —— 谁来问，它把最新心率吐出去（带时间戳）。
  3. 守门 —— 只有带对「暗号」的请求才能写入，
            免得公网上随便谁都能塞假数据进来。

注意：这里的「暗号」(WRITE_TOKEN) 是防陌生人乱写的基础门锁，
      跟 J 的安全词 Dragon 是两回事。
      Dragon = 立即切断的红色按钮，那个之后单独、慎重地写。

用标准库 + Flask。Render 对 Flask 支持最顺。
"""

import os
import time
from flask import Flask, request, jsonify

app = Flask(__name__)

# ─────────────────────────────────────────────
#  配置（从环境变量读，不写死在代码里）
# ─────────────────────────────────────────────
# 写入暗号：iPhone 上传心率时必须带上它。
# 部署到 Render 时，在网站后台的环境变量里设置，
# 不要写死在代码里、更不要传到公开的 GitHub 上。
WRITE_TOKEN = os.environ.get("WRITE_TOKEN", "")

# ─────────────────────────────────────────────
#  内存里存「最新一条」心率
#  （先不接数据库，最新值放内存最简单。
#   服务器重启会丢，但对「读当下心率」这个用途够了。）
# ─────────────────────────────────────────────
latest = {
    "bpm": None,        # 最新心率
    "at": None,         # 收到的时间（Unix 秒）
    "count": 0,         # 累计收到多少条
}


@app.route("/")
def home():
    """首页：随便看一眼服务活着没。"""
    return jsonify({
        "service": "moonlight-heartrate-relay",
        "alive": True,
        "received_total": latest["count"],
    })


# 临时宽松模式：
#   - True  = 不带 token 也接受（今天先验证全链路通）
#   - False = 严格守门，必须带对 token
# 等以后 iPhone App 加上发 token 的代码后，把它改回 False。
ALLOW_NO_TOKEN = True


def _accept_heartrate():
    """收一条心率的统一逻辑。给 /push 和 /heartrate 两条路径共用。"""
    # —— 守门（宽松模式下，没 token 也放过）——
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
    """
    读最新心率。谁来问都能读（读不需要暗号）。
    会附带「这条数据多久以前的」，方便判断新鲜度。
    """
    if latest["bpm"] is None:
        return jsonify({"bpm": None, "message": "还没有收到任何心率"})

    age_sec = int(time.time()) - latest["at"]
    return jsonify({
        "bpm": latest["bpm"],
        "at": latest["at"],
        "age_seconds": age_sec,
        "count": latest["count"],
    })


@app.route("/health")
def health():
    """给定时器戳的端点：让免费服务别睡着。"""
    return "ok", 200


if __name__ == "__main__":
    # Render 会通过环境变量 PORT 告诉我们用哪个端口
    port = int(os.environ.get("PORT", 8766))
    app.run(host="0.0.0.0", port=port)
