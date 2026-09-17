from imagegen.fal import FalAdapter
from imagegen.envelope import GenRequest
from image_pipeline.helpers import fal_queue_submit, fal_result, http_failure


def req(**kw):
    return GenRequest(prompt="macro of a circuit leaf", **kw)


def test_queue_protocol_submit_poll_result(monkeypatch, net):
    monkeypatch.setenv("FAL_KEY", "fal-k")
    monkeypatch.setattr("imagegen.fal.time.sleep", lambda s: None)
    net["queue"] = [
        (fal_queue_submit("r1"), None),
        ({"status": "IN_QUEUE", "queue_position": 4}, None),
        ({"status": "IN_PROGRESS"}, None),
        ({"status": "COMPLETED"}, None),
        (fal_result(), None),
    ]
    r = FalAdapter().generate(req(aspect="landscape"))
    assert r.success and r.seed == 777 and net["downloads"]
    urls = [c["url"] for c in net["calls"]]
    assert urls[0] == "https://queue.fal.run/fal-ai/flux-2/klein/9b"
    assert "/status" in urls[1] and urls[-1].endswith("/requests/r1")
    assert net["calls"][0]["payload"]["image_size"] == "landscape_16_9"
    assert net["calls"][0]["headers"]["Authorization"] == "Key fal-k"


def test_sync_model_posts_direct(monkeypatch, net):
    monkeypatch.setenv("FAL_KEY", "fal-k")
    net["queue"].append((fal_result(), None))
    r = FalAdapter().generate(req(model="fal-ai/flux-2-pro"))
    assert net["calls"][0]["url"] == "https://fal.run/fal-ai/flux-2-pro"
    assert net["calls"][0]["payload"]["sync_mode"] is True and r.success


def test_exact_size_uses_custom_image_size(monkeypatch, net):
    monkeypatch.setenv("FAL_KEY", "fal-k")
    monkeypatch.setattr("imagegen.fal.time.sleep", lambda s: None)
    net["queue"] = [(fal_queue_submit(), None), ({"status": "COMPLETED"}, None),
                    (fal_result(), None)]
    r = FalAdapter().generate(req(size=(1920, 1080)))
    assert net["calls"][0]["payload"]["image_size"] == {"width": 1920, "height": 1080}
    assert any("exact 1920x1080" in n for n in r.notes)


def test_aspect_ratio_style_model_snaps(monkeypatch, net):
    monkeypatch.setenv("FAL_KEY", "fal-k")
    monkeypatch.setattr("imagegen.fal.time.sleep", lambda s: None)
    net["queue"] = [(fal_queue_submit(), None), ({"status": "COMPLETED"}, None),
                    (fal_result(), None)]
    r = FalAdapter().generate(req(model="fal-ai/nano-banana-2", size=(1500, 1000)))
    assert net["calls"][0]["payload"]["aspect_ratio"] == "3:2"


def test_refs_route_to_edit_endpoint(monkeypatch, net, tmp_path):
    monkeypatch.setenv("FAL_KEY", "fal-k")
    monkeypatch.setattr("imagegen.fal.time.sleep", lambda s: None)
    from image_pipeline.helpers import make_png
    ref = tmp_path / "product.png"
    ref.write_bytes(make_png(40, 40))
    net["queue"] = [(fal_queue_submit(), None), ({"status": "COMPLETED"}, None),
                    (fal_result(), None)]
    r = FalAdapter().generate(req(references=(str(ref),)))
    assert net["calls"][0]["url"] == "https://queue.fal.run/fal-ai/flux-2/klein/9b/edit"
    assert net["calls"][0]["payload"]["image_urls"][0].startswith("data:")
    assert r.success


def test_no_edit_endpoint_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "fal-k")
    from image_pipeline.helpers import make_png
    ref = tmp_path / "p.png"
    ref.write_bytes(make_png(10, 10))
    r = FalAdapter().generate(req(model="fal-ai/z-image/turbo", references=(str(ref),)))
    assert not r.success and r.error_type == "modality_unsupported"


def test_queue_failed_status_surfaces(monkeypatch, net):
    monkeypatch.setenv("FAL_KEY", "fal-k")
    monkeypatch.setattr("imagegen.fal.time.sleep", lambda s: None)
    net["queue"] = [(fal_queue_submit(), None),
                    ({"status": "FAILED", "error": "nsfw"}, None)]
    r = FalAdapter().generate(req())
    assert not r.success and r.error_type == "api_error"


def test_timeout_from_transport(monkeypatch, net):
    monkeypatch.setenv("FAL_KEY", "fal-k")
    net["queue"].append((None, http_failure("timeout")))
    r = FalAdapter().generate(req())
    assert not r.success and r.error_type == "timeout"
