"""Standalone alibaba adapter — behavior contract copied from the plugin suite
(tests/image_gen_alibaba): ladder order, chat-vs-images payload inference,
advance rules, plan reporting, refs handling."""

from __future__ import annotations

from imagegen.alibaba import AlibabaAdapter
from imagegen.envelope import GenRequest
from image_pipeline.helpers import http_failure, token_plan_body

TP = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
PAYG = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def req(**kw):
    return GenRequest(prompt="a red fox reading a map", **kw)


def test_tp_key_makes_available(monkeypatch):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "tp")
    assert AlibabaAdapter().available(req())


def test_no_keys_not_available():
    assert not AlibabaAdapter().available(req())


def test_tp_defaults_to_chat_payload(monkeypatch, net):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "tp")
    net["queue"].append((token_plan_body(), None))
    r = AlibabaAdapter().generate(req(aspect="landscape"))
    assert r.success and r.plan == "alibaba-token-plan"
    call = net["calls"][0]
    assert call["url"] == TP + "/chat/completions"
    assert call["payload"]["messages"][0]["content"][0]["text"]
    assert call["payload"]["size"] == "1280*720"
    assert call["headers"]["Authorization"] == "Bearer tp"
    # materialized + measured + seed carried from debug_info
    assert r.seed == 1234 and r.image.endswith(".png") and (r.width, r.height) == (1, 1)


def test_pinned_api_key_uses_images_surface(monkeypatch, net):
    net["queue"].append(({"data": [{"b64_json": "AAEC"}]}, None))
    r = AlibabaAdapter().generate(req(api_key="sk-explicit"))
    call = net["calls"][0]
    assert call["url"] == TP + "/images/generations"
    assert call["payload"]["prompt"] and call["payload"]["n"] == 1
    assert r.route == "direct" and r.credential_source == "cli"


def test_401_on_tp_intl_advances_to_tp_cn(monkeypatch, net):
    # The CN plan falls back to the intl key by design (same plugin ladder);
    # a 401 on intl must advance to the CN host and report both logins.
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "tp")
    TOKEN_CN = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    net["queue"] = [(None, http_failure("http", status=401, message="bad key")),
                    (token_plan_body(), None)]
    r = AlibabaAdapter().generate(req())
    assert r.success and r.plan == "alibaba-token-plan-cn"
    assert [c["url"] for c in net["calls"]] == [TP + "/chat/completions",
                                                TOKEN_CN + "/chat/completions"]
    assert "logins tried: alibaba-token-plan, alibaba-token-plan-cn" in r.note


def test_model_not_found_on_verified_plan_does_not_advance(monkeypatch, net):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "tp")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ds")
    net["queue"].append((None, http_failure("http", status=400, message="Model not found: wan9")))
    r = AlibabaAdapter().generate(req())
    assert not r.success and len(net["calls"]) == 1  # verified catalog → caller mistake


def test_endpoint_env_and_plan_pin_respected(monkeypatch, net):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "tp")
    monkeypatch.setenv("ALIBABA_IMAGE_ENDPOINT", "/images/generations")
    monkeypatch.setenv("ALIBABA_IMAGE_PLAN", "alibaba")  # pin PAYG
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ds")
    net["queue"].append(({"data": [{"b64_json": "AAEC"}]}, None))
    r = AlibabaAdapter().generate(req())
    assert r.success and r.plan == "alibaba"
    assert net["calls"][0]["url"] == PAYG + "/images/generations"


def test_refs_on_images_surface_rejected(monkeypatch):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "tp")
    a = AlibabaAdapter()
    r = a.generate(req(endpoint="/images/generations", references=("/nonexistent.png",)))
    assert not r.success and r.error_type == "io_error"  # unreadable before surface check


def test_refs_inlined_on_chat_surface(monkeypatch, net, tmp_path):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "tp")
    ref = tmp_path / "brand.png"
    from image_pipeline.helpers import make_png
    ref.write_bytes(make_png(32, 32))
    net["queue"].append((token_plan_body(), None))
    r = AlibabaAdapter().generate(req(references=(str(ref),)))
    assert r.success
    content = net["calls"][0]["payload"]["messages"][0]["content"]
    assert content[1]["type"] == "image" and content[1]["image"].startswith("data:image/png;base64,")
    assert "wan ignores size when reference images are supplied" in (r.note or "")


def test_401_full_ladder_walks_cn_then_payg(monkeypatch, net):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "tp")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "ds")
    net["queue"] = [
        (None, http_failure("http", status=401, message="bad key")),   # intl
        (None, http_failure("http", status=401, message="bad key")),   # cn (same key)
        (token_plan_body(), None),                                     # PAYG answers
    ]
    r = AlibabaAdapter().generate(req())
    assert r.success and r.plan == "alibaba" and "logins tried" in r.note


def test_timeout_advances_then_exhausts(monkeypatch, net):
    monkeypatch.setenv("ALIBABA_TOKEN_PLAN_API_KEY", "tp")
    monkeypatch.setenv("ALIBABA_IMAGE_PLAN", "alibaba-token-plan")  # single-plan pin
    net["queue"].append((None, http_failure("timeout")))
    r = AlibabaAdapter().generate(req())
    assert not r.success and "all Alibaba logins failed" in r.error
