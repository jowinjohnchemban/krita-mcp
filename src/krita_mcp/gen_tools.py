"""MCP tools and prompts for generative AI: image models (generate / inpaint / outpaint / upscale / cut out),
video models, and vision models for a second opinion. Imported by server.py, which owns the MCP server."""
import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any, Optional

from .providers import ProviderError
from .providers import images as gen_images
from .providers import video as gen_video
from .providers import vision as gen_vision
from .server import Image, ToolError, _call, mcp, tool


async def _provider(fn, *a, **k):
    """Run a blocking provider call in a thread; provider failures become tool errors."""
    try:
        return await asyncio.to_thread(fn, *a, **k)
    except ProviderError as e:
        raise ToolError(str(e)) from None


async def _workspace() -> Path:
    return Path((await _call("app.info"))["workspace"]).resolve()


async def _workspace_file(rel: str, exts: tuple) -> Path:
    root = await _workspace()
    p = (root / rel).resolve()
    if root not in p.parents or p.suffix.lower() not in exts:
        raise ToolError("path must be a %s file inside the workspace (%s)" % ("/".join(exts), root))
    return p


async def _load_workflow(name: str) -> dict:
    rel = name if name.lower().endswith(".json") else "workflows/%s.json" % name
    p = await _workspace_file(rel, (".json",))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ToolError("cannot read workflow %s: %s" % (p, e)) from None


@tool
async def ai_backends(details: bool = False) -> dict:
    """Which generative-AI services are connected: image backends (ComfyUI, AUTOMATIC1111/Forge, OpenAI,
    Gemini, Stability, Replicate, fal) with their models/samplers/upscalers, video backends (HeyGen, Sora,
    Veo, Replicate, fal; details=true lists HeyGen avatars and voices), vision models for ask_vision, and
    the ComfyUI workflows saved in <workspace>/workflows. Call this before generate_image."""
    out = {"image": await _provider(gen_images.all_status),
           "video": await _provider(gen_video.all_status, details),
           "vision": gen_vision.configured()}
    try:
        wf = (await _workspace()) / "workflows"
        out["comfyui_workflows"] = sorted(f.stem for f in wf.glob("*.json")) if wf.is_dir() else []
    except ToolError:
        out["comfyui_workflows"] = []
    return out


async def _generate(prompt, mode="auto", negative_prompt=None, count=1, backend=None, model=None, strength=None,
                    seed=None, steps=None, cfg=None, sampler=None, scheduler=None, size=None, workflow=None,
                    region="auto", x=None, y=None, width=None, height=None, padding=32, name=None, extra=None):
    if mode not in ("auto", "txt2img", "img2img", "inpaint"):
        raise ToolError("mode must be auto, txt2img, img2img or inpaint")
    be = await _provider(gen_images.pick, backend)
    count = max(1, min(int(count or 1), gen_images.MAX_COUNT))
    ctx = await _call("gen.context", region=region, x=x, y=y, width=width, height=height, padding=padding,
                      target=size or be.default_side, multiple=be.multiple, sizes=be.sizes,
                      need_image=mode != "txt2img", need_mask=mode in ("auto", "inpaint") or None,
                      mask_alpha=(be.mask_format == "alpha") or None)
    if mode == "auto":
        mode = "inpaint" if ctx["has_selection"] else "txt2img"
    if mode == "inpaint" and not ctx["has_selection"]:
        raise ToolError("inpaint needs a selection: set_selection first (or use mode=img2img)")

    def dec(k):
        return base64.b64decode(ctx[k]) if ctx.get(k) else None
    job = gen_images.ImageJob(mode=mode, prompt=prompt, negative=negative_prompt or "", width=ctx["size"][0],
                              height=ctx["size"][1], count=count, seed=seed, steps=steps, cfg=cfg,
                              strength=strength, sampler=sampler, scheduler=scheduler, model=model,
                              image=dec("image_png"), mask=dec("mask_png"), mask_alpha=dec("mask_alpha_png"),
                              workflow=await _load_workflow(workflow) if workflow else None, extra=extra or {})
    imgs, meta = await _provider(be.generate, job)
    imgs = imgs[:gen_images.MAX_COUNT]
    seeds = meta.get("seeds") or []
    labels = ["seed %s" % s for s in seeds[:len(imgs)]] if seeds and None not in seeds else []
    placed = await _call("gen.place", images=[base64.b64encode(i).decode() for i in imgs], region=ctx["region"],
                         mask=ctx["has_selection"] or None, name=name or ("%s: %s" % (be.name, prompt))[:80],
                         labels=labels, sheet=True)
    sheet = placed.pop("sheet_png", None)
    summary = {"backend": be.name, "mode": mode, "generated_at": ctx["size"], "placed": placed,
               "meta": {k: v for k, v in meta.items() if v is not None}}
    if len(imgs) > 1:
        summary["next"] = "Compare the numbered sheet, then pick_variation(group, index)."
    return [summary, Image(data=base64.b64decode(sheet), format="png")] if sheet else [summary]


@tool
async def generate_image(prompt: str, mode: str = "auto", negative_prompt: Optional[str] = None, count: int = 1,
                         backend: Optional[str] = None, model: Optional[str] = None,
                         strength: Optional[float] = None, seed: Optional[int] = None,
                         steps: Optional[int] = None, cfg: Optional[float] = None, sampler: Optional[str] = None,
                         scheduler: Optional[str] = None, size: Optional[int] = None,
                         workflow: Optional[str] = None, region: str = "auto", x: Optional[int] = None,
                         y: Optional[int] = None, width: Optional[int] = None, height: Optional[int] = None,
                         padding: int = 32, name: Optional[str] = None,
                         extra: Optional[dict[str, Any]] = None) -> list:
    """Generate pixels with an image model and place them as NEW layers (one undo step).
    mode: auto (inpaint if there is a selection, else txt2img), txt2img, img2img (rework the region;
    strength 0..1 = how much changes), inpaint (regenerate only the selected pixels; soft edges blend).
    The region is the selection's bounds + padding (or x/y/width/height, or region='canvas'); results are
    cut to the selection, so nothing outside it changes. count 1-8 = variations in a group plus a numbered
    comparison sheet (choose with pick_variation). backend: comfyui, a1111, openai, gemini, stability,
    replicate, fal (default: KRITA_MCP_IMAGE_BACKEND or the first available - see ai_backends).
    model = checkpoint / model id (Replicate 'owner/name', fal 'fal-ai/...'). size = longest side to
    generate at (default 1024). workflow = a ComfyUI API-format graph saved as <workspace>/workflows/NAME.json
    using {{prompt}} {{negative}} {{seed}} {{image}} {{mask}} {{width}} {{height}} {{strength}} placeholders
    (ControlNet, IP-Adapter, Flux, ... anything ComfyUI can do). extra = provider-specific inputs."""
    return await _generate(prompt, mode, negative_prompt, count, backend, model, strength, seed, steps, cfg,
                           sampler, scheduler, size, workflow, region, x, y, width, height, padding, name, extra)


@tool
async def outpaint(prompt: str, left: int = 0, right: int = 0, top: int = 0, bottom: int = 0,
                   overlap: int = 24, negative_prompt: Optional[str] = None, count: int = 1,
                   backend: Optional[str] = None, model: Optional[str] = None, seed: Optional[int] = None,
                   size: Optional[int] = None, workflow: Optional[str] = None) -> list:
    """Extend the picture beyond its edges: grows the canvas by left/right/top/bottom pixels, selects the
    new border (reaching `overlap` px into the old image so it blends) and inpaints it."""
    await _call("gen.expand", left=left, right=right, top=top, bottom=bottom, overlap=overlap)
    return await _generate(prompt, "inpaint", negative_prompt, count, backend, model, 1.0, seed, size=size,
                           workflow=workflow, region="canvas", padding=0, name="Outpaint: " + prompt[:60])


@tool
async def pick_variation(group: str, index: int = 1, keep_only: bool = False) -> dict:
    """Show variant `index` (1-based, as numbered on the sheet) of a generate_image variants group.
    keep_only=true deletes the other variants and leaves just the chosen layer."""
    return await _call("gen.pick", group=group, index=index, keep_only=keep_only or None)


@tool
async def upscale_image(factor: float = 2.0, backend: Optional[str] = None, model: Optional[str] = None) -> dict:
    """AI super-resolution: scales the document by `factor` (all layers, Lanczos) and adds the model's
    upscaled rendering of the whole picture as a new top layer. backend: a1111 / comfyui (their upscale
    models), stability, replicate, fal. Without an AI backend use scale_image."""
    if not 1.0 < factor <= 8.0:
        raise ToolError("factor must be greater than 1 and at most 8")
    be = await _provider(gen_images.pick, backend)
    ctx = await _call("gen.context", region="canvas", multiple=1)
    w, h = ctx["region"][2], ctx["region"][3]
    if w * h > 4096 * 4096:
        raise ToolError("image too large to upscale in one piece")
    up = await _provider(be.upscale, base64.b64decode(ctx["image_png"]), factor, model)
    nw, nh = int(round(w * factor)), int(round(h * factor))
    await _call("document.scale", width=nw, height=nh, method="Lanczos3")
    placed = await _call("gen.place", images=[base64.b64encode(up).decode()], region=[0, 0, nw, nh],
                         name="AI upscale x%g (%s)" % (factor, be.name))
    return {"backend": be.name, "size": [nw, nh], "layer": placed["layers"][0]}


@tool
async def remove_background(layer: Optional[str] = None, method: str = "auto", mode: str = "mask",
                            tolerance: int = 32, feather: int = 1, color: Optional[str] = None,
                            backend: Optional[str] = None, model: Optional[str] = None) -> dict:
    """Cut the subject out of a layer. mode: mask (non-destructive transparency mask, default), erase, or
    select (just select the subject). method: color (local, no AI: floods the plain background in from the
    image border, within `tolerance` of the border colour or of `color`), ai (a segmentation model via
    stability / replicate / fal / a1111 with the rembg extension), auto (ai if a capable backend answers,
    else color)."""
    if method not in ("auto", "color", "ai"):
        raise ToolError("method must be auto, color or ai")
    if method != "color":
        try:
            be = gen_images.pick(backend)
            ctx = await _call("gen.context", region="canvas", layer=layer, multiple=1, matte="#ffffff")
            cut = await asyncio.to_thread(be.remove_background, base64.b64decode(ctx["image_png"]), model)
            res = await _call("gen.alpha_mask", layer=layer, region=ctx["region"],
                              data_base64=base64.b64encode(cut).decode(), mode=mode, feather=feather)
            res["method"] = "ai:" + be.name
            return res
        except ProviderError as e:
            if method == "ai":
                raise ToolError(str(e)) from None
    res = await _call("image.remove_background", layer=layer, tolerance=tolerance, feather=feather, mode=mode,
                      color=color)
    res["method"] = "color"
    return res


@tool
async def match_colors(layer: Optional[str] = None, reference_layer: Optional[str] = None,
                       reference_path: Optional[str] = None, reference_region: Optional[list[int]] = None,
                       strength: float = 1.0) -> dict:
    """Make a layer's colours sit in the scene: transfers the mean and contrast of each channel from a
    reference (another layer, an image file in the workspace, or a region [x,y,w,h] of the picture - by
    default the whole picture). Use after pasting or generating an element. Respects the selection."""
    return await _call("photo.match_colors", layer=layer, reference_layer=reference_layer,
                       reference_path=reference_path, reference_region=reference_region, strength=strength)


@tool
async def generate_video(prompt: str = "", backend: Optional[str] = None, model: Optional[str] = None,
                         use_canvas: bool = True, seconds: Optional[float] = None,
                         width: Optional[int] = None, height: Optional[int] = None,
                         script: Optional[str] = None, avatar_id: Optional[str] = None,
                         voice_id: Optional[str] = None, character: str = "avatar",
                         negative_prompt: Optional[str] = None, path: Optional[str] = None,
                         import_into_krita: bool = True, max_frames: int = 240,
                         extra: Optional[dict[str, Any]] = None) -> dict:
    """Make a video with a video model, save it in the workspace and (by default) open it in Krita as an
    animation to paint over, grade or re-render. use_canvas=true sends the current picture as the start
    frame (image-to-video) or, for HeyGen, as the background behind the avatar.
    backend: heygen (talking avatar: script + avatar_id + voice_id; character='photo' makes the character
    painted on the canvas talk), openai (Sora), gemini (Veo), replicate / fal (any video model id).
    Generation can take several minutes."""
    be = await _provider(gen_video.get, backend)
    image = None
    if use_canvas:
        ctx = await _call("gen.context", region="canvas", target=1280, multiple=2)
        image = base64.b64decode(ctx["image_png"])
        if width is None or height is None:
            width, height = ctx["size"]
    job = gen_video.VideoJob(prompt=prompt, negative=negative_prompt or "", image=image, width=width or 1280,
                             height=height or 720, seconds=seconds, model=model, script=script,
                             avatar_id=avatar_id, voice_id=voice_id, character=character, extra=extra or {})
    mp4, meta = await _provider(be.generate, job)
    rel = path or "generated/%s-%s.mp4" % (be.name, time.strftime("%Y%m%d-%H%M%S"))
    out_path = await _workspace_file(rel, (".mp4", ".webm", ".mov"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(mp4)
    result = {"backend": be.name, "path": str(out_path), "bytes": len(mp4), "meta": meta}
    if import_into_krita:
        result["imported"] = await _call("video.import", path=str(out_path), max_frames=max_frames)
    return result


@tool
async def ask_vision(question: str, provider: Optional[str] = None, model: Optional[str] = None,
                     max_size: int = 1024, x: Optional[int] = None, y: Optional[int] = None,
                     width: Optional[int] = None, height: Optional[int] = None,
                     layer: Optional[str] = None) -> dict:
    """Ask another vision AI about the canvas and get its answer as text: a second opinion or critique,
    a caption, or a detailed prompt for an image model. provider: anthropic (Claude), openai (GPT, or any
    OpenAI-compatible server such as Ollama or LM Studio), gemini. Optionally a region or a single layer."""
    r = await _call("canvas.snapshot", max_size=max_size, x=x, y=y, width=width, height=height, layer=layer)
    text, meta = await _provider(gen_vision.ask, base64.b64decode(r["png_base64"]), question, provider, model)
    return dict(answer=text, **meta)


# ------------------------------------------------------------------ prompts
@mcp.prompt()
def paint_scene(subject: str, style: str = "painterly, cinematic light") -> str:
    """Plan and paint a scene with Krita's own tools, then critique and refine it."""
    return ("Paint '%s' in Krita, style: %s. Create a document and block in the composition on separate layers "
            "(background, midground, subject, light) with brush_stroke and draw_shapes. Look at it with "
            "canvas_snapshot and analyze_image, fix values and colour harmony, add detail and a final lighting "
            "pass. Iterate at least twice before calling it done." % (subject, style))


@mcp.prompt()
def generative_inpaint(change: str) -> str:
    """Change part of the picture with an image model while keeping everything else."""
    return ("In the active Krita document: %s. Call ai_backends to see what is connected; canvas_snapshot with "
            "grid=100 to locate the area; set_selection around it and feather it with modify_selection; "
            "generate_image with mode=inpaint, count=3 and a precise prompt; compare the sheet and "
            "pick_variation the best; match_colors on the chosen layer; canvas_snapshot to check the seams."
            % change)


@mcp.prompt()
def concept_variations(brief: str, count: int = 4) -> str:
    """Explore several design directions quickly, then develop the best."""
    return ("Brief: %s. Explore with generate_image (mode=txt2img, count=%d), study the numbered sheet, "
            "pick_variation the strongest, then refine: img2img at strength 0.35 for polish, inpaint weak "
            "areas, and finish by hand with brush_stroke. Explain each choice." % (brief, count))


@mcp.prompt()
def retouch_photo(goal: str = "natural, balanced, magazine quality") -> str:
    """Retouch the open photo: tone, colour, cleanup, finish."""
    return ("Retouch the active photo; goal: %s. analyze_image first; auto_enhance or levels/curves; fix white "
            "balance with temperature; local fixes through selections + adjust_image (dodge/burn, denoise, "
            "sharpen); remove distractions with generate_image mode=inpaint; optionally add_vignette. Compare "
            "canvas_snapshot before and after and report what changed." % goal)


@mcp.prompt()
def animate(idea: str, frames: int = 12) -> str:
    """Make a short frame-by-frame or AI-assisted animation."""
    return ("Animate: %s (%d frames). Set fps and range with animation_set; draw keyframes with keyframe + "
            "brush_stroke (or paint a start frame and use generate_video for the motion); review with "
            "animation_contact_sheet, fix inconsistent frames, then render_video to mp4 and gif." % (idea, frames))


@mcp.prompt()
def product_shot(product: str, setting: str = "soft studio light, seamless backdrop") -> str:
    """Cut out a product and stage it in a newly generated setting."""
    return ("Make a product shot of %s in: %s. remove_background on the product layer; generate_image "
            "(txt2img, region=canvas) for the backdrop on a layer below it; match_colors the product to the "
            "backdrop; paint a contact shadow with brush_stroke on a multiply layer; export_image." %
            (product, setting))
