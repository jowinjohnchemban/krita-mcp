"""Looks and camera moves: prompt recipes that turn a plain request into a strong one for image and video
models (realistic photography, cinema, illustration styles), plus the paint-vs-generate decision."""
import re

REAL_NEGATIVE = ("cartoon, illustration, painting, drawing, anime, cgi, 3d render, plastic skin, airbrushed, "
                 "waxy, oversaturated, overexposed, deformed hands, extra fingers, extra limbs, bad anatomy, "
                 "blurry, low resolution, jpeg artifacts, watermark, signature, text, logo, frame")
ART_NEGATIVE = "blurry, low quality, jpeg artifacts, watermark, signature, text, deformed, bad anatomy"

# name: (prompt recipe appended to the request, negative prompt, realistic?)
STYLES = {
    "photo": ("photorealistic photograph, shot on a full-frame camera with a 35mm lens, natural light, true-to-life "
              "colours, fine detail, realistic textures, subtle film grain", REAL_NEGATIVE, True),
    "cinematic": ("cinematic film still, anamorphic lens, shallow depth of field, motivated lighting, rich contrast, "
                  "colour graded, 35mm film grain, atmospheric haze", REAL_NEGATIVE, True),
    "portrait": ("professional portrait photograph, 85mm lens at f/1.8, soft key light with gentle rim light, "
                 "realistic skin texture with pores, catchlights in the eyes, creamy bokeh", REAL_NEGATIVE, True),
    "product": ("commercial product photograph, studio softbox lighting, clean seamless backdrop, crisp reflections, "
                "accurate materials, high-end advertising look", REAL_NEGATIVE, True),
    "fashion": ("high-fashion editorial photograph, magazine cover quality, styled wardrobe, dramatic studio "
                "lighting, sharp fabric detail", REAL_NEGATIVE, True),
    "street": ("candid street photograph, available light, documentary style, 28mm lens, authentic moment",
               REAL_NEGATIVE, True),
    "landscape": ("landscape photograph, golden hour, wide-angle lens, deep focus, dramatic sky, high dynamic range",
                  REAL_NEGATIVE, True),
    "macro": ("macro photograph, 100mm macro lens, extreme close-up, razor-thin depth of field, intricate detail",
              REAL_NEGATIVE, True),
    "food": ("food photograph, appetising, soft window light, shallow depth of field, styled plating",
             REAL_NEGATIVE, True),
    "architecture": ("architectural photograph, tilt-shift corrected verticals, clean lines, balanced exposure",
                     REAL_NEGATIVE, True),
    "drone": ("aerial drone photograph, top-down perspective, sweeping vista, crisp detail", REAL_NEGATIVE, True),
    "concept_art": ("cinematic concept art, painterly, dramatic composition, atmospheric perspective, matte painting",
                    ART_NEGATIVE, False),
    "illustration": ("polished digital illustration, clean shapes, vibrant harmonious palette, crisp edges",
                     ART_NEGATIVE, False),
    "anime": ("anime key visual, clean line art, cel shading, vivid colours, detailed background", ART_NEGATIVE, False),
    "watercolor": ("watercolour painting, soft washes, paper texture, delicate bleeding edges", ART_NEGATIVE, False),
    "oil_painting": ("oil painting, visible impasto brushstrokes, rich pigments, canvas texture", ART_NEGATIVE, False),
    "3d_render": ("3D render, physically based materials, global illumination, octane-style lighting",
                  ART_NEGATIVE, False),
    "pixel_art": ("pixel art, limited palette, crisp pixels, no anti-aliasing", ART_NEGATIVE, False),
    "comic": ("comic book art, bold ink lines, halftone shading, dynamic composition", ART_NEGATIVE, False),
    "sketch": ("pencil sketch, graphite on paper, expressive hatching", ART_NEGATIVE, False),
}

# Camera moves for video models (and, locally, camera_move in Krita)
CAMERA = {
    "static": "locked-off static camera, no camera movement",
    "dolly_in": "smooth slow dolly-in toward the subject",
    "dolly_out": "smooth slow dolly-out revealing the surroundings",
    "push_in": "slow push-in on the subject",
    "pull_back": "slow pull-back reveal",
    "zoom_in": "steady optical zoom in",
    "zoom_out": "steady optical zoom out",
    "crash_zoom": "fast dramatic crash zoom into the subject",
    "pan_left": "smooth pan to the left",
    "pan_right": "smooth pan to the right",
    "tilt_up": "slow tilt up",
    "tilt_down": "slow tilt down",
    "orbit_left": "smooth orbit around the subject, moving left",
    "orbit_right": "smooth orbit around the subject, moving right",
    "crane_up": "crane shot rising up and over the scene",
    "crane_down": "crane shot descending toward the subject",
    "handheld": "handheld camera with subtle natural shake",
    "fpv_drone": "fast FPV drone flight through the scene, dynamic banking",
    "whip_pan": "fast whip pan with motion blur",
    "bullet_time": "bullet-time frozen moment while the camera arcs around the subject",
    "ken_burns": "slow Ken Burns zoom and drift",
}

ASPECTS = {"1:1": (1024, 1024), "4:3": (1152, 864), "3:4": (864, 1152), "3:2": (1216, 816), "2:3": (816, 1216),
           "16:9": (1344, 768), "9:16": (768, 1344), "21:9": (1536, 640), "4:5": (896, 1120), "5:4": (1120, 896)}

_REAL_WORDS = re.compile(r"\b(photo|photograph|photoreal|photorealistic|realistic|real life|hyperreal|cinematic|"
                         r"film still|portrait of|product shot|headshot|dslr|raw photo|35mm|85mm)\b", re.I)
_PAINT_WORDS = re.compile(r"\b(paint it|by hand|hand[- ]painted|with (the )?brush(es)?|brush ?strokes?|in krita|"
                          r"draw it|sketch it|line ?art|doodle|pixel art|vector|logo|icon|diagram|flat)\b", re.I)


def style_names():
    return sorted(STYLES)


def apply_style(prompt, negative, style):
    """(prompt, negative) with a style recipe applied; unknown styles raise ValueError."""
    if not style:
        return prompt, negative
    key = style.lower().replace("-", "_").replace(" ", "_")
    if key not in STYLES:
        raise ValueError("unknown style %r; choose from %s" % (style, ", ".join(style_names())))
    recipe, neg, _ = STYLES[key]
    return "%s, %s" % (prompt.rstrip(" ,."), recipe), negative if negative else neg


def apply_camera(prompt, camera):
    if not camera:
        return prompt
    key = camera.lower().replace("-", "_").replace(" ", "_")
    if key not in CAMERA:
        raise ValueError("unknown camera move %r; choose from %s" % (camera, ", ".join(sorted(CAMERA))))
    return "%s. Camera: %s." % (prompt.rstrip(" ."), CAMERA[key])


def guess_style(prompt):
    """A realistic style when the request reads like photography, else None."""
    if not _REAL_WORDS.search(prompt):
        return None
    p = prompt.lower()
    if "cinematic" in p or "film still" in p:
        return "cinematic"
    if "portrait" in p or "headshot" in p:
        return "portrait"
    if "product" in p:
        return "product"
    return "photo"


def choose_approach(prompt, style, have_generator):
    """paint | generate, and why. Painting keeps everything editable in Krita; generating gives realism."""
    if _PAINT_WORDS.search(prompt):
        return "paint", "the request asks for hand-made / graphic work"
    realistic = (style and STYLES.get(style, ("", "", False))[2]) or bool(_REAL_WORDS.search(prompt))
    if have_generator:
        return "generate", ("realistic content needs an image model" if realistic
                            else "an image model is connected")
    if realistic:
        return "paint", "no image model is connected (see ai_backends / docs/ai-backends.md); painting instead"
    return "paint", "no image model is connected; painting with Krita's brushes"
